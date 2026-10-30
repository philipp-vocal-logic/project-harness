"""Stage/merge checks, command execution, and orchestrator loop for project-harness."""

from __future__ import annotations

import contextlib
import shlex
import shutil
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path

from project_harness.model import (
    Stage,
    Workflow,
    check_paths_protected,
    check_paths_scope,
    is_git_generated,
    parse_subject,
)


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
    root: Path, command: str, materialized: frozenset[str], expected: tuple[int, ...] = (0,)
) -> str | None:
    args = _normalize_command(command, materialized)
    completed = subprocess.run(args, cwd=root, capture_output=True, text=True, check=False)
    if completed.returncode in expected:
        return None
    lines = (completed.stdout.strip() or completed.stderr.strip()).splitlines()
    detail = next(
        (line for line in lines if line.startswith(("FAILED ", "ERROR "))),
        lines[0] if lines else f"exit code {completed.returncode}",
    )
    if expected == (0,):
        return f"{command} [ERR_CHECK]: {detail}"
    return (
        f"{command} [ERR_RED_CHECK]: expected exit in {list(expected)}, "
        f"got exit {completed.returncode}: {detail}"
    )


def run_commands(
    root: Path,
    checks: Sequence[str],
    fail_checks: Sequence[str] = (),
    fail_exit_codes: tuple[int, ...] = (1,),
) -> list[str]:
    """Execute check commands (must exit 0) and fail_check commands (must exit fail_exit_codes)."""
    violations: list[str] = []
    with materialize_policy_files(root) as materialized:
        for command in checks:
            if violation := _run_single_check(root, command, materialized):
                violations.append(violation)
        for command in fail_checks:
            if violation := _run_single_check(root, command, materialized, fail_exit_codes):
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
    return run_commands(root, stage.checks, stage.fail_checks, stage.fail_exit_codes)


def _classify_subject(
    workflow: Workflow, subject: str, label: str
) -> tuple[Stage | None, list[str]]:
    """Return (stage, []) for stage commits, (None, []) for allowed non-stage commits."""
    if is_git_generated(subject):
        return None, []
    header = parse_subject(subject)
    if header is None:
        return None, [
            f"{label} [ERR_COMMIT_MSG]: invalid subject '{subject}'; "
            "expected <type>(<target>): <summary> (<= 72 chars)"
        ]
    stage = workflow.stage_by_prefix(header.prefix)
    if stage is not None and header.target is None:
        return None, [f"{label} [ERR_COMMIT_MSG]: stage commit '{subject}' requires (<target>)"]
    if stage is None and header.prefix not in workflow.commit_types:
        return None, [f"{label} [ERR_COMMIT_MSG]: unknown commit type '{header.prefix}'"]
    return stage, []


def check_commit_file(root: Path, workflow: Workflow, msg_file: Path) -> list[str]:
    """Validate commit message file and run the corresponding stage checks on staged files."""
    lines = msg_file.read_text(encoding="utf-8").splitlines()
    subject = lines[0].strip() if lines else ""
    if is_git_generated(subject):
        return []
    stage, violations = _classify_subject(workflow, subject, str(msg_file))
    if violations:
        return violations
    if stage is not None:
        return check_stage(root, workflow, stage.id, staged_only=True)
    staged = changed_working_paths(root, staged_only=True)
    return check_paths_protected(staged, workflow.protected_paths) or run_commands(
        root, workflow.checks
    )


def _check_single_commit(
    root: Path, workflow: Workflow, commit_line: str
) -> tuple[str | None, list[str]]:
    sha, _, subject = commit_line.partition(" ")
    short_sha = sha[:7]
    stage, violations = _classify_subject(workflow, subject, short_sha)
    if violations:
        return None, violations
    files = _git_lines(root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha)
    if stage is None:
        return None, check_paths_protected(files, workflow.protected_paths, label=short_sha)
    return stage.id, check_paths_scope(files, stage, workflow.protected_paths, label=short_sha)


def check_merge(root: Path, workflow: Workflow, base_ref: str) -> list[str]:
    """Verify clean tree, commit types, stage sequence and scopes in base_ref..HEAD, and checks.

    Merge commits are skipped. Allowed non-stage commits (workflow.commit_types, Revert) are
    only checked against protected_paths. If the branch has any stage commit, the stage
    commits must form the full configured stage sequence.
    """
    if changed_working_paths(root):
        return ["working-tree [ERR_DIRTY]: uncommitted changes present"]
    commit_lines = _git_lines(
        root, "log", "--reverse", "--no-merges", "--format=%H %s", f"{base_ref}..HEAD"
    )
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
    if not violations and seen_stages and seen_stages != expected:
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
