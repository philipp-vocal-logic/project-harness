"""Loading and validation of .policy/*.toml configurations and git-based file discovery."""

from __future__ import annotations

import re
import subprocess
import tomllib
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from project_harness.model import (
    REQUIREMENT_ID_PATTERN,
    SYMBOL_KINDS,
    SYMBOL_VISIBILITIES,
    ArchitectureRule,
    Requirement,
    SymbolRule,
    SymbolSchema,
    TraceabilityConfig,
    Workflow,
    matches_any,
    split_subwords,
)

POLICY_DIRECTORY = ".policy"
ALLOWED_WORKFLOW_KEYS = frozenset(
    {"protected_paths", "checks", "source_paths", "test_command", "red_exit_codes"}
)
ALLOWED_ARCHITECTURE_KEYS = frozenset({"files", "fix", "max_lines", "forbid"})
ALLOWED_SYMBOL_KEYS = frozenset({"forbidden_subwords"})
ALLOWED_SYMBOL_RULE_KEYS = frozenset({"files", "kinds", "visibility", "pattern", "fix"})
ALLOWED_TRACEABILITY_KEYS = frozenset({"requirements", "tests"})
ALLOWED_REQUIREMENT_KEYS = frozenset({"id", "title", "acceptance", "tests"})


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
        base / relative_path
        for relative_path in sorted({part for part in listed.split("\0") if part})
        if matches_any(relative_path, include) and (base / relative_path).is_file()
    ]


def _require_table(raw_table: Any, scope: str) -> dict[str, Any]:
    if isinstance(raw_table, dict):
        return raw_table
    raise ValueError(f"'{scope}' must be a table")


def _check_keys(table: dict[str, Any], allowed: frozenset[str], scope: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ValueError(f"unknown keys in {scope}: {unknown} (allowed: {sorted(allowed)})")


def _require_string(table: dict[str, Any], key: str, scope: str) -> str:
    raw = table.get(key)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    raise ValueError(f"{scope}: '{key}' must be a non-empty string")


def _parse_string_tuple(parts: Any, label: str) -> tuple[str, ...]:
    if not isinstance(parts, list) or not all(isinstance(s, str) and s.strip() for s in parts):
        raise ValueError(f"'{label}' must be a list of non-empty strings")
    return tuple(part.strip() for part in parts)


def _parse_subword_tuple(raw_list: Any, label: str) -> tuple[str, ...]:
    words = _parse_string_tuple(raw_list, label)
    for word in words:
        if len(split_subwords(word)) != 1:
            raise ValueError(f"'{label}' entries must be single subwords: '{word}'")
    return tuple(word.lower() for word in words)


def _parse_glob_tuple(raw_list: Any, label: str) -> tuple[str, ...]:
    patterns = _parse_string_tuple(raw_list, label)
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
    checks = _parse_string_tuple(table.get("checks", []), "workflow.checks")
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


def _parse_architecture_rule(raw_rule: Any) -> ArchitectureRule:
    table = _require_table(raw_rule, "architecture.rules")
    _check_keys(table, ALLOWED_ARCHITECTURE_KEYS, "architecture.rules")
    files = _parse_glob_tuple(table.get("files"), "architecture.rules.files")
    if not files:
        raise ValueError("'architecture.rules.files' must not be empty")
    max_lines = table.get("max_lines")
    if max_lines is not None and (not isinstance(max_lines, int) or max_lines < 1):
        raise ValueError("'architecture.rules.max_lines' must be a positive integer")
    raw_forbid = _parse_string_tuple(table.get("forbid", []), "architecture.rules.forbid")
    forbid = tuple(_compile_pattern(pattern, "forbid") for pattern in raw_forbid)
    if max_lines is None and not forbid:
        raise ValueError(f"architecture.rules for {list(files)} needs max_lines or forbid")
    return ArchitectureRule(
        files, _require_string(table, "fix", "architecture.rules"), max_lines, forbid
    )


def parse_architecture(text: str) -> tuple[ArchitectureRule, ...]:
    """Parse and validate the text of a .policy/architecture.toml file."""
    raw = tomllib.loads(text)
    _check_keys(raw, frozenset({"rules"}), "architecture.toml")
    rules = raw.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("architecture.toml must contain at least one [[rules]] table")
    return tuple(_parse_architecture_rule(entry) for entry in rules)


def _parse_symbol_rule(raw_rule: Any) -> SymbolRule:
    table = _require_table(raw_rule, "symbols.rules")
    _check_keys(table, ALLOWED_SYMBOL_RULE_KEYS, "symbols.rules")
    files = _parse_glob_tuple(table.get("files", ["**/*.py"]), "symbols.rules.files")
    kinds = frozenset(_parse_string_tuple(table.get("kinds"), "symbols.rules.kinds"))
    if not files or not kinds or not kinds <= SYMBOL_KINDS:
        raise ValueError(f"symbols.rules needs files and kinds in {sorted(SYMBOL_KINDS)}")
    visibility = table.get("visibility", "all")
    if visibility not in SYMBOL_VISIBILITIES:
        raise ValueError(f"symbols.rules.visibility must be in {sorted(SYMBOL_VISIBILITIES)}")
    pattern = _compile_pattern(_require_string(table, "pattern", "symbols.rules"), "symbol")
    fix = _require_string(table, "fix", "symbols.rules")
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


def _is_test_link(test_link: str) -> bool:
    file_part, separator, name_part = test_link.partition("::")
    posix = PurePosixPath(file_part)
    return (
        bool(file_part and separator)
        and all(name_part.split("::"))
        and not posix.is_absolute()
        and ".." not in posix.parts
    )


def _parse_test_links(raw_tests: Any, relative_path: str) -> tuple[str, ...]:
    if not isinstance(raw_tests, list) or not all(isinstance(s, str) for s in raw_tests):
        raise ValueError(f"{relative_path}: 'tests' must be a list of strings")
    cleaned = tuple(entry.strip() for entry in raw_tests)
    if len(set(cleaned)) != len(cleaned) or not all(map(_is_test_link, cleaned)):
        raise ValueError(
            f"{relative_path}: 'tests' must be unique '<relative path>::<test name>' links"
        )
    return cleaned


def _parse_requirement_id(table: dict[str, Any], relative_path: str) -> str:
    stem = PurePosixPath(relative_path).stem
    requirement_id = _require_string(table, "id", relative_path)
    if requirement_id != stem or not REQUIREMENT_ID_PATTERN.match(requirement_id):
        raise ValueError(f"{relative_path}: id '{requirement_id}' must match filename '{stem}'")
    return requirement_id


def _parse_acceptance(raw_acceptance: Any, relative_path: str) -> tuple[str, ...]:
    criteria = _parse_string_tuple(raw_acceptance, f"{relative_path}.acceptance")
    if not criteria or len(set(criteria)) != len(criteria) or any("\n" in e for e in criteria):
        raise ValueError(
            f"{relative_path}: 'acceptance' must be a non-empty list of unique single-line strings"
        )
    return criteria


def parse_requirement(text: str, relative_path: str) -> Requirement:
    """Parse and validate a requirement TOML document."""
    raw = tomllib.loads(text)
    _check_keys(raw, ALLOWED_REQUIREMENT_KEYS, relative_path)
    requirement_id = _parse_requirement_id(raw, relative_path)
    title = _require_string(raw, "title", relative_path)
    acceptance = _parse_acceptance(raw.get("acceptance"), relative_path)
    tests = _parse_test_links(raw.get("tests", []), relative_path)
    return Requirement(requirement_id, title, acceptance, tests, relative_path)


def parse_traceability(text: str) -> TraceabilityConfig:
    """Parse and validate the text of a .policy/traceability.toml file."""
    raw = tomllib.loads(text)
    _check_keys(raw, frozenset({"traceability"}), "traceability.toml")
    table = _require_table(raw.get("traceability", {}), "traceability")
    _check_keys(table, ALLOWED_TRACEABILITY_KEYS, "traceability")
    requirements = _parse_glob_tuple(
        table.get("requirements", ["trace/*.toml"]), "traceability.requirements"
    )
    tests = _parse_glob_tuple(table.get("tests", ["tests/**/test_*.py"]), "traceability.tests")
    if not requirements or not tests:
        raise ValueError("traceability.requirements and traceability.tests must not be empty")
    return TraceabilityConfig(requirements, tests)
