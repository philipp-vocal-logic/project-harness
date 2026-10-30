"""Configuration loading, stage/merge checks, and orchestrator loop for project-harness."""

from __future__ import annotations

import contextlib
import shlex
import shutil
import subprocess
import tomllib
from collections.abc import Iterator, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from project_harness.model import (
    Orchestrator,
    Stage,
    Workflow,
    check_paths_scope,
    parse_subject,
)

ALLOWED_ROOT_KEYS = frozenset({"workflow", "orchestrator", "stages"})
ALLOWED_WORKFLOW_KEYS = frozenset({"protected_paths", "checks"})
ALLOWED_ORCHESTRATOR_KEYS = frozenset({"agent_command", "max_retries"})
ALLOWED_STAGE_KEYS = frozenset(
    {"id", "commit_prefix", "allow_write", "checks", "fail_checks", "prompt"}
)


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
    return Stage(stage_id, prefix, allow_write, checks, fail_checks, prompt)


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
    return Workflow(protected, checks, stages, orchestrator)


@contextlib.contextmanager
def materialize_policy_files(root: Path) -> Iterator[frozenset[str]]:
    """Temporarily copy .policy/*.toml files to root so standalone CLIs resolve root."""
    policy_dir = root / ".policy"
    created: list[Path] = []
    names: set[str] = set()
    try:
        if policy_dir.is_dir():
            for source in sorted(policy_dir.glob("*.toml")):
                if source.name == "workflow.toml":
                    continue
                target = root / source.name
                names.add(source.name)
                if not target.exists():
                    shutil.copyfile(source, target)
                    created.append(target)
        yield frozenset(names)
    finally:
        for path in created:
            path.unlink(missing_ok=True)


def _normalize_command(command: str, materialized: frozenset[str]) -> list[str]:
    tokens = shlex.split(command)
    return [
        token.removeprefix(".policy/")
        if token.startswith(".policy/") and token.removeprefix(".policy/") in materialized
        else token
        for token in tokens
    ]


def _run_single_check(
    root: Path, command: str, materialized: frozenset[str], *, expect_fail: bool = False
) -> str | None:
    args = _normalize_command(command, materialized)
    completed = subprocess.run(args, cwd=root, capture_output=True, text=True, check=False)
    if completed.returncode == (1 if expect_fail else 0):
        return None
    lines = (completed.stdout.strip() or completed.stderr.strip()).splitlines()
    detail = next(
        (line for line in lines if line.startswith(("FAILED ", "ERROR "))),
        lines[0] if lines else f"exit code {completed.returncode}",
    )
    if not expect_fail:
        return f"{command} [ERR_CHECK]: {detail}"
    return f"{command} [ERR_RED_CHECK]: expected exit 1, got exit {completed.returncode}: {detail}"


def run_commands(root: Path, checks: Sequence[str], fail_checks: Sequence[str] = ()) -> list[str]:
    """Execute configured check and fail_check commands against root."""
    violations: list[str] = []
    with materialize_policy_files(root) as materialized:
        for command in checks:
            if violation := _run_single_check(root, command, materialized):
                violations.append(violation)
        for command in fail_checks:
            if violation := _run_single_check(root, command, materialized, expect_fail=True):
                violations.append(violation)
    return violations


def _git_lines(root: Path, *args: str) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise ValueError(f"git {' '.join(args)}: {detail}")
    return [line for line in completed.stdout.splitlines() if line.strip()]


def changed_working_paths(root: Path, *, staged_only: bool = False) -> list[str]:
    """Return sorted relative POSIX paths modified or untracked in the git working tree."""
    staged = _git_lines(root, "diff", "--cached", "--name-only")
    if staged_only:
        return sorted(set(staged))
    unstaged = _git_lines(root, "diff", "--name-only")
    untracked = _git_lines(root, "ls-files", "--others", "--exclude-standard")
    return sorted({*staged, *unstaged, *untracked})


def check_stage(
    root: Path, workflow: Workflow, stage_id: str, *, staged_only: bool = False
) -> list[str]:
    """Validate modified files and run commands for a single workflow stage."""
    stage = workflow.stage_by_id(stage_id)
    if stage is None:
        raise ValueError(f"unknown stage '{stage_id}'")
    paths = changed_working_paths(root, staged_only=staged_only)
    if not paths:
        return [f"working-tree [ERR_EMPTY] ({stage.id}): no modified files for stage"]
    scope_violations = check_paths_scope(paths, stage, workflow.protected_paths)
    if scope_violations:
        return scope_violations
    return run_commands(root, stage.checks, stage.fail_checks)


def check_commit_file(root: Path, workflow: Workflow, msg_file: Path) -> list[str]:
    """Validate commit message file and run the corresponding stage checks on staged files."""
    lines = msg_file.read_text(encoding="utf-8").splitlines()
    subject = lines[0].strip() if lines else ""
    header = parse_subject(subject)
    if header is None:
        return [
            f"{msg_file} [ERR_COMMIT_MSG]: invalid subject '{subject}'; "
            "expected <prefix>(<target>): <summary> (<= 72 chars)"
        ]
    stage = workflow.stage_by_prefix(header.prefix)
    if stage is None:
        return [f"{msg_file} [ERR_COMMIT_MSG]: unknown stage prefix '{header.prefix}'"]
    return check_stage(root, workflow, stage.id, staged_only=True)


def _check_single_commit(
    root: Path, workflow: Workflow, commit_line: str
) -> tuple[str | None, list[str]]:
    sha, _, subject = commit_line.partition(" ")
    short_sha = sha[:7]
    header = parse_subject(subject)
    if header is None:
        return None, [f"{short_sha} [ERR_COMMIT_MSG]: invalid commit subject '{subject}'"]
    stage = workflow.stage_by_prefix(header.prefix)
    if stage is None:
        return None, [f"{short_sha} [ERR_COMMIT_MSG]: unknown stage prefix '{header.prefix}'"]
    files = _git_lines(root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha)
    return stage.id, check_paths_scope(files, stage, workflow.protected_paths, label=short_sha)


def check_merge(root: Path, workflow: Workflow, base_ref: str) -> list[str]:
    """Verify clean tree, stage commit sequence and scopes in base_ref..HEAD, and merge checks."""
    if changed_working_paths(root):
        return ["working-tree [ERR_DIRTY]: uncommitted changes present"]
    commit_lines = _git_lines(root, "log", "--reverse", "--format=%H %s", f"{base_ref}..HEAD")
    if not commit_lines:
        return [f"{base_ref}..HEAD [ERR_HISTORY]: no commits ahead of {base_ref}"]
    seen_stages: list[str] = []
    violations: list[str] = []
    for line in commit_lines:
        stage_id, commit_violations = _check_single_commit(root, workflow, line)
        violations.extend(commit_violations)
        if stage_id is not None and (not seen_stages or seen_stages[-1] != stage_id):
            seen_stages.append(stage_id)
    expected = [stage.id for stage in workflow.stages]
    if not violations and seen_stages != expected:
        violations.append(
            f"{base_ref}..HEAD [ERR_HISTORY]: stage sequence {seen_stages} != expected {expected}"
        )
    return violations or run_commands(root, workflow.checks)


def _run_agent_attempt(
    root: Path, workflow: Workflow, stage: Stage, target: str, feedback: list[str]
) -> list[str]:
    assert workflow.orchestrator is not None
    base_prompt = stage.prompt.format(target=target, stage=stage.id)
    prompt = f"{base_prompt}\nViolations:\n" + "\n".join(feedback) if feedback else base_prompt
    args = [
        part.format(prompt=prompt, target=target, stage=stage.id)
        for part in workflow.orchestrator.agent_command
    ]
    completed = subprocess.run(args, cwd=root, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "agent failed"
        return [f"agent ({stage.id}) [ERR_AGENT]: {detail}"]
    return check_stage(root, workflow, stage.id)


def _execute_stage(root: Path, workflow: Workflow, stage: Stage, target: str) -> list[str]:
    assert workflow.orchestrator is not None
    feedback: list[str] = []
    for _ in range(workflow.orchestrator.max_retries):
        feedback = _run_agent_attempt(root, workflow, stage, target, feedback)
        if not feedback:
            _git_lines(root, "add", "-A")
            _git_lines(
                root, "commit", "--no-verify", "-m", f"{stage.commit_prefix}({target}): {stage.id}"
            )
            return []
    return feedback


def run_workflow(root: Path, workflow: Workflow, target: str) -> list[str]:
    """Run all configured TDD workflow stages in sequence for target, committing each stage."""
    if workflow.orchestrator is None:
        raise ValueError("orchestrator section is required in workflow.toml for 'harness run'")
    if changed_working_paths(root):
        return ["working-tree [ERR_DIRTY]: uncommitted changes present before workflow run"]
    for stage in workflow.stages:
        if violations := _execute_stage(root, workflow, stage, target):
            return violations
    return []
