"""Loading and validation of .policy/workflow.toml for project-harness."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

from project_harness.model import Orchestrator, Stage, Workflow

ALLOWED_ROOT_KEYS = frozenset({"workflow", "orchestrator", "stages"})
ALLOWED_WORKFLOW_KEYS = frozenset(
    {"protected_paths", "checks", "commit_types", "policy_commit_type"}
)
ALLOWED_ORCHESTRATOR_KEYS = frozenset({"agent_command", "max_retries"})
ALLOWED_STAGE_KEYS = frozenset(
    {"id", "commit_prefix", "allow_write", "checks", "fail_checks", "fail_exit_codes", "prompt"}
)
PREFIX_PATTERN = re.compile(r"[a-z][a-z0-9_-]*")


def _require_table(raw_table: Any, scope: str) -> dict[str, Any]:
    if isinstance(raw_table, dict):
        return raw_table
    raise ValueError(f"'{scope}' must be a table")


def _check_keys(table: dict[str, Any], allowed: frozenset[str], scope: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ValueError(f"unknown keys in {scope}: {unknown}")


def _require_str(table: dict[str, Any], key: str, scope: str) -> str:
    field = table.get(key)
    if isinstance(field, str) and field.strip():
        return field.strip()
    raise ValueError(f"{scope}: missing or invalid '{key}'")


def _str_tuple(raw_list: Any, label: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(raw_list, list) or not all(
        isinstance(part, str) and part.strip() for part in raw_list
    ):
        raise ValueError(f"'{label}' must be a list of non-empty strings")
    if not allow_empty and not raw_list:
        raise ValueError(f"'{label}' must not be empty")
    return tuple(part.strip() for part in raw_list)


def _glob_tuple(raw_list: Any, label: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    patterns = _str_tuple(raw_list, label, allow_empty=allow_empty)
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


def _parse_stage(raw_stage: Any) -> Stage:
    table = _require_table(raw_stage, "stage")
    _check_keys(table, ALLOWED_STAGE_KEYS, "stage")
    stage_id = _require_str(table, "id", "stage")
    scope = f"stage '{stage_id}'"
    prefix = _require_str(table, "commit_prefix", scope)
    allow_write = _glob_tuple(table.get("allow_write"), f"{scope}.allow_write")
    checks = _str_tuple(table.get("checks", []), f"{scope}.checks")
    fail_checks = _str_tuple(table.get("fail_checks", []), f"{scope}.fail_checks")
    prompt = _require_str(table, "prompt", scope) if "prompt" in table else ""
    exit_codes = _exit_code_tuple(table.get("fail_exit_codes", [1]), f"{scope}.fail_exit_codes")
    return Stage(stage_id, prefix, allow_write, checks, fail_checks, prompt, exit_codes)


def _parse_stages(raw_stages: Any) -> tuple[Stage, ...]:
    if not isinstance(raw_stages, list) or not raw_stages:
        raise ValueError("'stages' must be a non-empty list of tables")
    stages: list[Stage] = []
    seen_ids: set[str] = set()
    seen_prefixes: set[str] = set()
    for raw_stage in raw_stages:
        stage = _parse_stage(raw_stage)
        if stage.id in seen_ids:
            raise ValueError(f"duplicate stage id '{stage.id}'")
        if stage.commit_prefix in seen_prefixes:
            raise ValueError(f"duplicate stage commit_prefix '{stage.commit_prefix}'")
        seen_ids.add(stage.id)
        seen_prefixes.add(stage.commit_prefix)
        stages.append(stage)
    return tuple(stages)


def _parse_orchestrator(raw_orch: Any) -> Orchestrator | None:
    if raw_orch is None:
        return None
    table = _require_table(raw_orch, "orchestrator")
    _check_keys(table, ALLOWED_ORCHESTRATOR_KEYS, "orchestrator")
    cmd = _str_tuple(table.get("agent_command"), "orchestrator.agent_command", allow_empty=False)
    retries = table.get("max_retries", 3)
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 1:
        raise ValueError("orchestrator.max_retries must be a positive integer")
    return Orchestrator(cmd, retries)


def _parse_commit_types(raw_list: Any, stages: tuple[Stage, ...]) -> tuple[str, ...]:
    commit_types = _str_tuple(raw_list, "workflow.commit_types")
    stage_prefixes = {stage.commit_prefix for stage in stages}
    for commit_type in commit_types:
        if not PREFIX_PATTERN.fullmatch(commit_type):
            raise ValueError(f"workflow.commit_types: invalid commit type '{commit_type}'")
        if commit_type in stage_prefixes:
            raise ValueError(f"workflow.commit_types: '{commit_type}' is a stage commit_prefix")
    return commit_types


def _parse_policy_commit_type(
    table: dict[str, Any], stages: tuple[Stage, ...], commit_types: tuple[str, ...]
) -> str:
    if "policy_commit_type" not in table:
        return ""
    policy_type = _require_str(table, "policy_commit_type", "workflow")
    taken = {stage.commit_prefix for stage in stages} | set(commit_types)
    if not PREFIX_PATTERN.fullmatch(policy_type) or policy_type in taken:
        raise ValueError(f"workflow.policy_commit_type: invalid or duplicate '{policy_type}'")
    return policy_type


def load_workflow(path: Path) -> Workflow:
    """Load and validate a .policy/workflow.toml file."""
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    _check_keys(raw, ALLOWED_ROOT_KEYS, "workflow.toml")
    wf_table = _require_table(raw.get("workflow", {}), "workflow")
    _check_keys(wf_table, ALLOWED_WORKFLOW_KEYS, "workflow")
    protected = _glob_tuple(
        wf_table.get("protected_paths", []), "workflow.protected_paths", allow_empty=True
    )
    checks = _str_tuple(wf_table.get("checks", []), "workflow.checks")
    stages = _parse_stages(raw.get("stages"))
    orchestrator = _parse_orchestrator(raw.get("orchestrator"))
    commit_types = _parse_commit_types(wf_table.get("commit_types", []), stages)
    policy_type = _parse_policy_commit_type(wf_table, stages, commit_types)
    return Workflow(protected, checks, stages, orchestrator, commit_types, policy_type)
