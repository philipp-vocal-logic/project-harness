"""Loading and validation of .policy/workflow.toml for project-harness."""

from __future__ import annotations

import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

from project_harness.model import Workflow

ALLOWED_ROOT_KEYS = frozenset({"workflow"})
ALLOWED_WORKFLOW_KEYS = frozenset(
    {"protected_paths", "checks", "source_paths", "test_command", "red_exit_codes"}
)


def _require_table(raw_table: Any, scope: str) -> dict[str, Any]:
    if isinstance(raw_table, dict):
        return raw_table
    raise ValueError(f"'{scope}' must be a table")


def _check_keys(table: dict[str, Any], allowed: frozenset[str], scope: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ValueError(f"unknown keys in {scope}: {unknown}")


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


def _exit_code_tuple(raw_list: Any, label: str) -> tuple[int, ...]:
    if (
        not isinstance(raw_list, list)
        or not raw_list
        or not all(
            isinstance(code, int) and not isinstance(code, bool) and 1 <= code <= 255
            for code in raw_list
        )
    ):
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
    _check_keys(raw, ALLOWED_ROOT_KEYS, "workflow.toml")
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
