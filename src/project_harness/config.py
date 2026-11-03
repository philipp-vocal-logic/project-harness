"""Loading and validation of .policy/*.toml configurations and file discovery."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from project_harness.model import (
    DEFAULT_DEPRECATED_STATUS,
    TEST_TYPE,
    ArchRule,
    Glossary,
    Replacement,
    SymbolSchema,
    Term,
    TraceConfig,
    TraceRule,
    Workflow,
    is_excluded,
    matches_any,
)  # fmt: skip

DEFAULT_EXCLUDES: tuple[str, ...] = (
    ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "*.egg-info"
)  # fmt: skip
ALLOWED_WORKFLOW_KEYS = frozenset(
    {"protected_paths", "checks", "source_paths", "test_command", "red_exit_codes"}
)
ALLOWED_ARCH_KEYS = frozenset({"files", "fix", "ignore", "max_lines", "forbid"})
ALLOWED_GLOSSARY_SETTINGS = frozenset({"paths", "exclude", "similarity_threshold"})
ALLOWED_TERM_KEYS = frozenset({"term", "definition", "forbidden_terms"})
ALLOWED_SYMBOL_SETTINGS = frozenset({"include", "exclude", "forbidden_subwords", "allowed_symbols"})
ALLOWED_TRACE_KEYS = frozenset({"schema", "statuses", "locations", "rules"})
ALLOWED_TRACE_RULE_KEYS = frozenset(
    {"id", "source_type", "when_status", "target_type", "target_status"}
)


def resolve_root(config_path: Path) -> Path:
    """Resolve the project root directory from a configuration file path."""
    resolved = config_path.resolve()
    is_policy = resolved.parent.name == ".policy"  # glossary: ignore[policy]
    return resolved.parent.parent if is_policy else resolved.parent


def walk_files(
    root: Path, include: Sequence[str] = ("**",), exclude: Sequence[str] = ()
) -> list[Path]:
    """Walk root and return sorted files matching include globs and not exclude globs."""
    base = root.resolve()
    combined_exclude = (*DEFAULT_EXCLUDES, *exclude)
    matched: list[Path] = []
    for dir_path, dir_names, file_names in os.walk(base):
        rel_dir = Path(dir_path).relative_to(base)
        dir_names[:] = sorted(
            name
            for name in dir_names
            if not is_excluded((rel_dir / name).as_posix(), combined_exclude)
        )
        for name in sorted(file_names):
            rel_posix = (rel_dir / name).as_posix()
            if not is_excluded(rel_posix, combined_exclude) and matches_any(rel_posix, include):
                matched.append(base / rel_posix)
    return matched


def _require_table(raw_table: Any, scope: str) -> dict[str, Any]:
    if isinstance(raw_table, dict):
        return raw_table
    raise ValueError(f"'{scope}' must be a table")


def _check_keys(table: dict[str, Any], allowed: frozenset[str], scope: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ValueError(f"unknown keys in {scope}: {unknown}")


def _require_str(table: dict[str, Any], key: str, scope: str) -> str:
    value = table.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError(f"{scope}: '{key}' must be a non-empty string")


def _str_tuple(raw_list: Any, label: str) -> tuple[str, ...]:
    if not isinstance(raw_list, list) or not all(
        isinstance(part, str) and part.strip() for part in raw_list
    ):
        raise ValueError(f"'{label}' must be a list of non-empty strings")
    return tuple(part.strip() for part in raw_list)


def _glob_tuple(raw_list: Any, label: str) -> tuple[str, ...]:
    patterns = _str_tuple(raw_list, label)
    for pattern in patterns:
        posix = PurePosixPath(pattern)
        if posix.is_absolute() or ".." in posix.parts:
            raise ValueError(f"{label}: glob must be a relative path pattern: '{pattern}'")
    return patterns


def _valid_exit_code(code: Any) -> bool:
    return isinstance(code, int) and not isinstance(code, bool) and 1 <= code <= 255


def _exit_code_tuple(raw_list: Any, label: str) -> tuple[int, ...]:
    if not isinstance(raw_list, list) or not raw_list or not all(map(_valid_exit_code, raw_list)):
        raise ValueError(f"'{label}' must be a non-empty list of exit codes in 1..255")
    return tuple(raw_list)


def _test_command(table: dict[str, Any], source_paths: tuple[str, ...]) -> str:
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
    source_paths = _glob_tuple(table.get("source_paths", []), "workflow.source_paths")
    return Workflow(
        protected_paths=_glob_tuple(table.get("protected_paths", []), "workflow.protected_paths"),
        checks=_str_tuple(table.get("checks", []), "workflow.checks"),
        source_paths=source_paths,
        test_command=_test_command(table, source_paths),
        red_exit_codes=_exit_code_tuple(
            table.get("red_exit_codes", [1]), "workflow.red_exit_codes"
        ),
    )


def load_workflow(path: Path) -> Workflow:
    """Load and validate a .policy/workflow.toml file."""
    return parse_workflow(path.read_text(encoding="utf-8"))


def _compile_regexes(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error as error:
            raise ValueError(f"invalid forbid regex {pattern!r}: {error}") from None
    return tuple(compiled)


def _parse_arch_rule(raw_rule: Any) -> ArchRule:
    table = _require_table(raw_rule, "arch.rules")
    _check_keys(table, ALLOWED_ARCH_KEYS, "arch.rules")
    files = _glob_tuple(table.get("files"), "arch.rules.files")
    if not files:
        raise ValueError("'arch.rules.files' must not be empty")
    max_lines = table.get("max_lines")
    if max_lines is not None and (not isinstance(max_lines, int) or max_lines < 1):
        raise ValueError("'arch.rules.max_lines' must be a positive integer")
    forbid = _compile_regexes(_str_tuple(table.get("forbid", []), "arch.rules.forbid"))
    ignore = _glob_tuple(table.get("ignore", []), "arch.rules.ignore")
    return ArchRule(files, _require_str(table, "fix", "arch.rules"), ignore, max_lines, forbid)


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


def _parse_term(raw_term: Any) -> Term:
    table = _require_table(raw_term, "glossary.terms")
    _check_keys(table, ALLOWED_TERM_KEYS, "glossary.terms")
    return Term(
        term=_require_str(table, "term", "glossary.terms"),
        definition=_require_str(table, "definition", "glossary.terms"),
        forbidden_terms=_str_tuple(table.get("forbidden_terms", []), "glossary.forbidden_terms"),
    )


def parse_glossary(text: str) -> Glossary:
    """Parse and validate the text of a .policy/glossary.toml file."""
    raw = tomllib.loads(text)
    _check_keys(raw, frozenset({"settings", "terms"}), "glossary.toml")
    settings = _require_table(raw.get("settings", {}), "glossary.settings")
    _check_keys(settings, ALLOWED_GLOSSARY_SETTINGS, "glossary.settings")
    raw_terms = raw.get("terms", [])
    if not isinstance(raw_terms, list):
        raise ValueError("'glossary.terms' must be a list of tables")
    paths = _str_tuple(settings.get("paths", ["."]), "glossary.settings.paths")
    if not paths:
        raise ValueError("'glossary.settings.paths' must not be empty")
    terms = tuple(_parse_term(entry) for entry in raw_terms)
    return Glossary(terms, paths, _str_tuple(settings.get("exclude", []), "glossary.exclude"))


def load_glossary(path: Path) -> Glossary:
    """Load and validate a .policy/glossary.toml file."""
    return parse_glossary(path.read_text(encoding="utf-8"))


def parse_symbols(text: str) -> SymbolSchema:
    """Parse and validate the text of a .policy/symbols.toml file."""
    raw = tomllib.loads(text)
    _check_keys(raw, frozenset({"settings", "replacements"}), "symbols.toml")
    settings = _require_table(raw.get("settings", {}), "symbols.settings")
    _check_keys(settings, ALLOWED_SYMBOL_SETTINGS, "symbols.settings")
    rep_table = _require_table(raw.get("replacements", {}), "symbols.replacements")
    replacements = tuple(
        Replacement(canonical.strip(), _str_tuple(syns, f"symbols.replacements.{canonical}"))
        for canonical, syns in rep_table.items()
    )
    default_inc = ["src/**/*.py", "tests/**/*.py"]
    return SymbolSchema(
        include=_glob_tuple(settings.get("include", default_inc), "symbols.settings.include"),
        exclude=_str_tuple(settings.get("exclude", []), "symbols.settings.exclude"),
        forbidden_subwords=tuple(
            w.lower() for w in _str_tuple(settings.get("forbidden_subwords", []), "forbidden")
        ),
        allowed_symbols=_str_tuple(settings.get("allowed_symbols", []), "allowed_symbols"),
        replacements=replacements,
    )


def load_symbols(path: Path) -> SymbolSchema:
    """Load and validate a .policy/symbols.toml file."""
    return parse_symbols(path.read_text(encoding="utf-8"))


def _parse_trace_rule(raw_rule: Any, chain: tuple[str, ...]) -> TraceRule:
    table = _require_table(raw_rule, "traceability.rules")
    _check_keys(table, ALLOWED_TRACE_RULE_KEYS, "traceability.rules")
    rid = _require_str(table, "id", "traceability.rules")
    source_type = _require_str(table, "source_type", rid)
    target_type = _require_str(table, "target_type", rid)
    if source_type not in chain or target_type not in (*chain, TEST_TYPE):
        raise ValueError(f"{rid}: source_type and target_type must be chain types or '{TEST_TYPE}'")
    is_test = target_type == TEST_TYPE
    if is_test and "target_status" in table:
        raise ValueError(f"{rid}: target_status is not allowed for target_type '{TEST_TYPE}'")
    return TraceRule(
        id=rid,
        source_type=source_type,
        when_status=_require_str(table, "when_status", rid),
        target_type=target_type,
        target_status=() if is_test else _str_tuple(table.get("target_status"), f"{rid}.status"),
    )


def parse_traceability(text: str) -> TraceConfig:
    """Parse and validate the text of a .policy/traceability.toml file."""
    raw = tomllib.loads(text)
    _check_keys(raw, ALLOWED_TRACE_KEYS, "traceability.toml")
    schema = _require_table(raw.get("schema", {}), "traceability.schema")
    _check_keys(schema, frozenset({"chain", "deprecated_status"}), "traceability.schema")
    chain = _str_tuple(schema.get("chain"), "traceability.schema.chain")
    if len(chain) < 2 or len(set(chain)) != len(chain) or TEST_TYPE in chain:
        raise ValueError(f"traceability.schema.chain needs >= 2 unique types, not '{TEST_TYPE}'")
    stat_table = _require_table(raw.get("statuses", {}), "traceability.statuses")
    _check_keys(stat_table, frozenset(chain), "traceability.statuses")
    statuses = {
        kind: frozenset(_str_tuple(stat_table.get(kind), f"statuses.{kind}")) for kind in chain
    }
    loc_table = _require_table(raw.get("locations", {}), "traceability.locations")
    _check_keys(loc_table, frozenset(chain), "traceability.locations")
    locations = {kind: _glob_tuple([loc_table.get(kind)], f"locations.{kind}")[0] for kind in chain}
    raw_rules = raw.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ValueError("'traceability.rules' must be a list of tables")
    depr = str(schema.get("deprecated_status", DEFAULT_DEPRECATED_STATUS)).strip()
    return TraceConfig(
        chain=chain,
        statuses=statuses,
        locations=locations,
        rules=tuple(_parse_trace_rule(entry, chain) for entry in raw_rules),
        deprecated_status=depr,
    )


def load_traceability(path: Path) -> TraceConfig:
    """Load and validate a .policy/traceability.toml file."""
    return parse_traceability(path.read_text(encoding="utf-8"))
