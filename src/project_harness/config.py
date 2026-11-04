"""Loading and validation of .policy/*.toml configurations and git-based file discovery."""

from __future__ import annotations

import re
import subprocess
import tomllib
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from project_harness.model import (
    SYMBOL_KINDS,
    SYMBOL_VISIBILITIES,
    TEST_TYPE,
    ArchRule,
    SymbolRule,
    SymbolSchema,
    TraceConfig,
    TraceRule,
    Workflow,
    matches_any,
    split_subwords,
)

POLICY_DIR = ".policy"
ALLOWED_WORKFLOW_KEYS = frozenset(
    {"protected_paths", "checks", "source_paths", "test_command", "red_exit_codes"}
)
ALLOWED_ARCH_KEYS = frozenset({"files", "fix", "max_lines", "forbid"})
ALLOWED_SYMBOL_KEYS = frozenset({"forbidden_subwords"})
ALLOWED_SYMBOL_RULE_KEYS = frozenset({"files", "kinds", "visibility", "pattern", "fix"})
ALLOWED_TRACE_KEYS = frozenset({"schema", "statuses", "locations", "rules"})
ALLOWED_TRACE_RULE_KEYS = frozenset(
    {"id", "source_type", "when_status", "target_type", "target_status"}
)


def git(cwd: Path, *args: str) -> str:
    """Run git in cwd and return stdout; raise ValueError with git's message on failure."""
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise ValueError(f"git {' '.join(args)}: {detail}")
    return completed.stdout


def walk_files(root: Path, include: Sequence[str] = ("**",)) -> list[Path]:
    """Return sorted tracked and non-ignored files under root matching include."""
    base = root.resolve()
    listed = git(base, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return [
        base / rel_path
        for rel_path in sorted({part for part in listed.split("\0") if part})
        if matches_any(rel_path, include) and (base / rel_path).is_file()
    ]


def _require_table(raw_table: Any, scope: str) -> dict[str, Any]:
    if isinstance(raw_table, dict):
        return raw_table
    raise ValueError(f"'{scope}' must be a table")


def _check_keys(table: dict[str, Any], allowed: frozenset[str], scope: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ValueError(f"unknown keys in {scope}: {unknown}")


def _require_str(table: dict[str, Any], key: str, scope: str) -> str:
    raw = table.get(key)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    raise ValueError(f"{scope}: '{key}' must be a non-empty string")


def _parse_str_tuple(parts: Any, label: str) -> tuple[str, ...]:
    if not isinstance(parts, list) or not all(isinstance(s, str) and s.strip() for s in parts):
        raise ValueError(f"'{label}' must be a list of non-empty strings")
    return tuple(part.strip() for part in parts)


def _parse_subword_tuple(raw_list: Any, label: str) -> tuple[str, ...]:
    words = _parse_str_tuple(raw_list, label)
    for word in words:
        if len(split_subwords(word)) != 1:
            raise ValueError(f"'{label}' entries must be single subwords: '{word}'")
    return tuple(word.lower() for word in words)


def _parse_glob_tuple(raw_list: Any, label: str) -> tuple[str, ...]:
    patterns = _parse_str_tuple(raw_list, label)
    for pattern in patterns:
        posix = PurePosixPath(pattern)
        if posix.is_absolute() or ".." in posix.parts:
            raise ValueError(f"{label}: glob must be a relative path pattern: '{pattern}'")
    return patterns


def _is_valid_exit_code(code: Any) -> bool:
    return isinstance(code, int) and not isinstance(code, bool) and 1 <= code <= 255


def _parse_exit_codes(codes: Any, label: str) -> tuple[int, ...]:
    if not isinstance(codes, list) or not codes or not all(map(_is_valid_exit_code, codes)):
        raise ValueError(f"'{label}' must be a non-empty list of exit codes in 1..255")
    return tuple(codes)


def _parse_test_command(table: dict[str, Any], source_paths: tuple[str, ...]) -> str:
    command = table.get("test_command", "")
    if not isinstance(command, str):
        raise ValueError("'workflow.test_command' must be a string")
    if source_paths and not command.strip():
        raise ValueError("workflow.test_command is required when source_paths is set")
    return command.strip()


def parse_workflow(text: str) -> Workflow:
    """Parse and validate the text of a .policy/workflow.toml file."""
    raw = tomllib.loads(text)
    _check_keys(raw, frozenset({"workflow"}), "workflow.toml")
    table = _require_table(raw.get("workflow", {}), "workflow")
    _check_keys(table, ALLOWED_WORKFLOW_KEYS, "workflow")
    protected = _parse_glob_tuple(table.get("protected_paths", []), "workflow.protected_paths")
    checks = _parse_str_tuple(table.get("checks", []), "workflow.checks")
    source_paths = _parse_glob_tuple(table.get("source_paths", []), "workflow.source_paths")
    red_codes = _parse_exit_codes(table.get("red_exit_codes", [1]), "workflow.red_exit_codes")
    return Workflow(
        protected, checks, source_paths, _parse_test_command(table, source_paths), red_codes
    )


def load_workflow(path: Path) -> Workflow:
    """Load and validate a .policy/workflow.toml file."""
    return parse_workflow(path.read_text(encoding="utf-8"))


def _compile_pattern(pattern: str, label: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error as error:
        raise ValueError(f"invalid {label} regex {pattern!r}: {error}") from None


def _parse_arch_rule(raw_rule: Any) -> ArchRule:
    table = _require_table(raw_rule, "arch.rules")
    _check_keys(table, ALLOWED_ARCH_KEYS, "arch.rules")
    files = _parse_glob_tuple(table.get("files"), "arch.rules.files")
    if not files:
        raise ValueError("'arch.rules.files' must not be empty")
    max_lines = table.get("max_lines")
    if max_lines is not None and (not isinstance(max_lines, int) or max_lines < 1):
        raise ValueError("'arch.rules.max_lines' must be a positive integer")
    raw_forbid = _parse_str_tuple(table.get("forbid", []), "arch.rules.forbid")
    forbid = tuple(_compile_pattern(pattern, "forbid") for pattern in raw_forbid)
    if max_lines is None and not forbid:
        raise ValueError(f"arch.rules for {list(files)} needs max_lines or forbid")
    return ArchRule(files, _require_str(table, "fix", "arch.rules"), max_lines, forbid)


def parse_arch(text: str) -> tuple[ArchRule, ...]:
    """Parse and validate the text of a .policy/arch.toml file."""
    raw = tomllib.loads(text)
    _check_keys(raw, frozenset({"rules"}), "arch.toml")
    rules = raw.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("arch.toml must contain at least one [[rules]] table")
    return tuple(_parse_arch_rule(entry) for entry in rules)


def load_arch(path: Path) -> tuple[ArchRule, ...]:
    """Load and validate a .policy/arch.toml file."""
    return parse_arch(path.read_text(encoding="utf-8"))


def _parse_symbol_rule(raw_rule: Any) -> SymbolRule:
    table = _require_table(raw_rule, "symbols.rules")
    _check_keys(table, ALLOWED_SYMBOL_RULE_KEYS, "symbols.rules")
    files = _parse_glob_tuple(table.get("files", ["**/*.py"]), "symbols.rules.files")
    kinds = frozenset(_parse_str_tuple(table.get("kinds"), "symbols.rules.kinds"))
    if not files or not kinds or not kinds <= SYMBOL_KINDS:
        raise ValueError(f"symbols.rules needs files and kinds in {sorted(SYMBOL_KINDS)}")
    visibility = table.get("visibility", "all")
    if visibility not in SYMBOL_VISIBILITIES:
        raise ValueError(f"symbols.rules.visibility must be in {sorted(SYMBOL_VISIBILITIES)}")
    pattern = _compile_pattern(_require_str(table, "pattern", "symbols.rules"), "symbol")
    fix = _require_str(table, "fix", "symbols.rules")
    return SymbolRule(files, kinds, pattern, fix, visibility)


def parse_symbols(text: str) -> SymbolSchema:
    """Parse and validate the text of a .policy/symbols.toml file."""
    raw = tomllib.loads(text)
    _check_keys(raw, frozenset({"settings", "replacements", "rules"}), "symbols.toml")
    settings = _require_table(raw.get("settings", {}), "symbols.settings")
    _check_keys(settings, ALLOWED_SYMBOL_KEYS, "symbols.settings")
    replacements_table = _require_table(raw.get("replacements", {}), "symbols.replacements")
    replacements = {
        _parse_subword_tuple([canonical], "symbols.replacements")[0]: _parse_subword_tuple(
            forbidden, f"symbols.replacements.{canonical}"
        )
        for canonical, forbidden in replacements_table.items()
    }
    forbidden = _parse_subword_tuple(settings.get("forbidden_subwords", []), "forbidden_subwords")
    raw_rules = raw.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ValueError("'symbols.rules' must be a list of tables")
    return SymbolSchema(forbidden, replacements, tuple(map(_parse_symbol_rule, raw_rules)))


def load_symbols(path: Path) -> SymbolSchema:
    """Load and validate a .policy/symbols.toml file."""
    return parse_symbols(path.read_text(encoding="utf-8"))


def _parse_trace_rule(raw_rule: Any, chain: tuple[str, ...]) -> TraceRule:
    table = _require_table(raw_rule, "traceability.rules")
    _check_keys(table, ALLOWED_TRACE_RULE_KEYS, "traceability.rules")
    rule_id = _require_str(table, "id", "traceability.rules")
    source_type = _require_str(table, "source_type", rule_id)
    target_type = _require_str(table, "target_type", rule_id)
    if source_type not in chain or target_type not in (*chain, TEST_TYPE):
        raise ValueError(f"{rule_id}: types must be chain types or '{TEST_TYPE}'")
    is_test = target_type == TEST_TYPE
    if is_test and "target_status" in table:
        raise ValueError(f"{rule_id}: target_status is not allowed for target_type '{TEST_TYPE}'")
    status_key = f"{rule_id}.status"
    target_status = () if is_test else _parse_str_tuple(table.get("target_status"), status_key)
    when_status = _require_str(table, "when_status", rule_id)
    return TraceRule(rule_id, source_type, when_status, target_type, target_status)


def parse_traceability(text: str) -> TraceConfig:
    """Parse and validate the text of a .policy/traceability.toml file."""
    raw = tomllib.loads(text)
    _check_keys(raw, ALLOWED_TRACE_KEYS, "traceability.toml")
    schema = _require_table(raw.get("schema", {}), "traceability.schema")
    _check_keys(schema, frozenset({"chain"}), "traceability.schema")
    chain = _parse_str_tuple(schema.get("chain"), "traceability.schema.chain")
    if len(chain) < 2 or len(set(chain)) != len(chain) or TEST_TYPE in chain:
        raise ValueError(f"traceability.schema.chain needs >= 2 unique types, not '{TEST_TYPE}'")
    statuses_table = _require_table(raw.get("statuses", {}), "traceability.statuses")
    _check_keys(statuses_table, frozenset(chain), "traceability.statuses")
    statuses = {
        kind: frozenset(_parse_str_tuple(statuses_table.get(kind), f"statuses.{kind}"))
        for kind in chain
    }
    locations_table = _require_table(raw.get("locations", {}), "traceability.locations")
    _check_keys(locations_table, frozenset(chain), "traceability.locations")
    locations = {
        kind: _parse_glob_tuple([locations_table.get(kind)], f"locations.{kind}")[0]
        for kind in chain
    }
    raw_rules = raw.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ValueError("'traceability.rules' must be a list of tables")
    rules = tuple(_parse_trace_rule(entry, chain) for entry in raw_rules)
    return TraceConfig(chain, statuses, locations, rules)


def load_traceability(path: Path) -> TraceConfig:
    """Load and validate a .policy/traceability.toml file."""
    return parse_traceability(path.read_text(encoding="utf-8"))
