"""Stage/merge checks, command execution, and orchestrator loop for project-harness."""

from __future__ import annotations

import contextlib
import re
import shlex
import shutil
import subprocess
from collections.abc import Iterator, Sequence
from itertools import groupby
from pathlib import Path

from project_harness.model import (
    Stage,
    Workflow,
    check_paths_protected,
    check_paths_scope,
    check_policy_commit,
    is_git_generated,
    parse_subject,
)

FAILURE_LINE_PATTERN = re.compile(
    r"^(?:FAILED |ERROR |error(?:\[E\d+\])?:|test .+ \.\.\. FAILED"
    r"|thread '.+' panicked at|\[\s*FAILED\s*\])"
    r"|:\s*(?:fatal )?error:|Assertion .* failed"
)
PLACEHOLDER_PATTERN = re.compile(r"\{(\w+)\}")


@contextlib.contextmanager
def materialize_policy_files(root: Path) -> Iterator[frozenset[str]]:
    """Temporarily copy .policy/*.toml files to root so standalone CLIs resolve root."""
    policy_dir = root / ".policy"
    created: list[Path] = []
    names: set[str] = set()
    try:
        for source in sorted(policy_dir.glob("*.toml")):
            if source.name == "workflow.toml":
                continue
            target = root / source.name
            if not target.exists():
                shutil.copyfile(source, target)
                created.append(target)
            elif target.read_bytes() != source.read_bytes():
                raise ValueError(f"{target.name} in root differs from .policy/{source.name}")
            names.add(source.name)
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
    raw_lines = f"{completed.stdout}\n{completed.stderr}".splitlines()
    lines = [line.strip() for line in raw_lines if line.strip()]
    detail = next(
        (line for line in lines if FAILURE_LINE_PATTERN.search(line)),
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
    is_policy = _is_policy_subject(workflow, subject)
    if (stage is not None or is_policy) and header.target is None:
        return None, [f"{label} [ERR_COMMIT_MSG]: '{header.prefix}' commit requires (<target>)"]
    if stage is None and not is_policy and header.prefix not in workflow.commit_types:
        return None, [f"{label} [ERR_COMMIT_MSG]: unknown commit type '{header.prefix}'"]
    return stage, []


def _is_policy_subject(workflow: Workflow, subject: str) -> bool:
    header = parse_subject(subject)
    return bool(workflow.policy_commit_type) and (
        header is not None and header.prefix == workflow.policy_commit_type
    )


def _non_stage_violations(
    workflow: Workflow, subject: str, message: str, paths: list[str], label: str
) -> list[str]:
    """Policy commits: protected paths only + Reason. Other non-stage commits: no protected."""
    if _is_policy_subject(workflow, subject):
        return check_policy_commit(paths, message, workflow.protected_paths, label)
    return check_paths_protected(paths, workflow.protected_paths, label)


def check_commit_file(root: Path, workflow: Workflow, msg_file: Path) -> list[str]:
    """Validate commit message file and run the corresponding stage checks on staged files."""
    message = msg_file.read_text(encoding="utf-8")
    lines = [line for line in message.splitlines() if not line.startswith("#")]
    subject = lines[0].strip() if lines else ""
    if is_git_generated(subject):
        return []
    stage, violations = _classify_subject(workflow, subject, str(msg_file))
    if violations:
        return violations
    if stage is not None:
        return check_stage(root, workflow, stage.id, staged_only=True)
    staged = changed_working_paths(root, staged_only=True)
    label = "working-tree"
    return _non_stage_violations(
        workflow, subject, "\n".join(lines), staged, label
    ) or run_commands(root, workflow.checks)


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
        message = "\n".join(_git_lines(root, "log", "-1", "--format=%B", sha))
        return None, _non_stage_violations(workflow, subject, message, files, short_sha)
    return stage.id, check_paths_scope(files, stage, workflow.protected_paths, label=short_sha)


def check_merge(root: Path, workflow: Workflow, base_ref: str) -> list[str]:
    """Verify clean tree, commit types, stage sequence and scopes in base_ref..HEAD, and checks.

    Merge commits are skipped. Allowed non-stage commits (workflow.commit_types, Revert) must
    not touch protected_paths; policy commits must only touch protected_paths and give a Reason.
    If the branch has any stage commit, the stage commits must form the full stage sequence
    for a single target.
    """
    if changed_working_paths(root):
        return ["working-tree [ERR_DIRTY]: uncommitted changes present"]
    commit_lines = _git_lines(
        root, "log", "--reverse", "--no-merges", "--format=%H %s", f"{base_ref}..HEAD"
    )
    if not commit_lines:
        return [f"{base_ref}..HEAD [ERR_HISTORY]: no commits ahead of {base_ref}"]
    stage_commits: list[tuple[str, str]] = []
    violations: list[str] = []
    for line in commit_lines:
        stage_id, commit_violations = _check_single_commit(root, workflow, line)
        violations.extend(commit_violations)
        if stage_id is not None:
            stage_commits.append((stage_id, line.partition(" ")[2]))
    violations.extend(_history_violations(workflow, stage_commits, f"{base_ref}..HEAD"))
    return violations or run_commands(root, workflow.checks)


def _history_violations(
    workflow: Workflow, stage_commits: list[tuple[str, str]], label: str
) -> list[str]:
    """Stage commits (stage id, subject) must form the full stage sequence for one target."""
    if not stage_commits:
        return []
    seen_stages = [stage_id for stage_id, _ in groupby(stage_id for stage_id, _ in stage_commits)]
    expected = [stage.id for stage in workflow.stages]
    headers = [parse_subject(subject) for _, subject in stage_commits]
    targets = sorted({header.target for header in headers if header and header.target})
    violations: list[str] = []
    if seen_stages != expected:
        violations.append(
            f"{label} [ERR_HISTORY]: stage sequence {seen_stages} != expected {expected}"
        )
    if len(targets) > 1:
        violations.append(f"{label} [ERR_HISTORY]: stage commits target several nodes {targets}")
    return violations


def _fill(template: str, **values: str) -> str:
    """Replace known {name} placeholders in one pass; other literal braces are kept."""
    return PLACEHOLDER_PATTERN.sub(lambda match: values.get(match[1], match[0]), template)


def _run_agent_attempt(
    root: Path, workflow: Workflow, stage: Stage, target: str, feedback: list[str]
) -> list[str]:
    assert workflow.orchestrator is not None
    base_prompt = _fill(stage.prompt, target=target, stage=stage.id)
    prompt = f"{base_prompt}\nViolations:\n" + "\n".join(feedback) if feedback else base_prompt
    args = [
        _fill(part, prompt=prompt, target=target, stage=stage.id)
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
