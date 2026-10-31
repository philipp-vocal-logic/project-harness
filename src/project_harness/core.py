"""Command execution and the merge check (protected paths, red tests) for project-harness."""

from __future__ import annotations

import contextlib
import re
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path

from project_harness.config import parse_workflow
from project_harness.model import (
    POLICY_BRANCH_PREFIX,
    REFACTOR_BRANCH_PREFIX,
    Workflow,
    has_reason,
    matches_glob,
    matching_paths,
)

FAILURE_LINE_PATTERN = re.compile(
    r"^(?:FAILED |ERROR |error(?:\[E\d+\])?:|test .+ \.\.\. FAILED"
    r"|thread '.+' panicked at|\[\s*FAILED\s*\])"
    r"|:\s*(?:fatal )?error:|Assertion .* failed"
)


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


def _run(cwd: Path, command: str, materialized: frozenset[str]) -> tuple[int, str]:
    """Run command in cwd; return its exit code and the most relevant output line."""
    args = _normalize_command(command, materialized)
    completed = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    raw_lines = f"{completed.stdout}\n{completed.stderr}".splitlines()
    lines = [line.strip() for line in raw_lines if line.strip()]
    detail = next(
        (line for line in lines if FAILURE_LINE_PATTERN.search(line)),
        lines[0] if lines else f"exit code {completed.returncode}",
    )
    return completed.returncode, detail


def run_commands(root: Path, checks: Sequence[str]) -> list[str]:
    """Execute check commands in root; each must exit 0."""
    violations: list[str] = []
    with materialize_policy_files(root) as materialized:
        for command in checks:
            code, detail = _run(root, command, materialized)
            if code != 0:
                violations.append(f"{command} [ERR_CHECK]: {detail}")
    return violations


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise ValueError(f"git {' '.join(args)}: {detail}")
    return completed.stdout


def _split_nul(output: str) -> list[str]:
    return [part for part in output.split("\0") if part.strip()]


def _exception_violations(
    root: Path, merge_base: str, branch: str, prefix: str, paths: Sequence[str]
) -> list[str]:
    """Allow changes to paths only on a prefix branch whose commits touching them give a Reason."""
    if not branch.startswith(prefix):
        return [f"branch '{branch}' [ERR_BRANCH]: exception requires a '{prefix}*' branch"]
    pathspecs = [f":(literal){rel_path}" for rel_path in paths]
    log = _git(
        root, "log", "-z", "--no-merges", "--format=%h%n%B", f"{merge_base}..HEAD", "--", *pathspecs
    )
    commits = [entry.partition("\n") for entry in _split_nul(log)]
    if not commits:
        return [f"branch '{branch}' [ERR_REASON]: no commit records these changes"]
    return [
        f"{sha} [ERR_REASON]: missing 'Reason: <why>' line"
        for sha, _, message in commits
        if not has_reason(message)
    ]


def _protected_violations(
    root: Path, workflow: Workflow, merge_base: str, branch: str, changed: list[str]
) -> list[str]:
    touched = matching_paths(changed, workflow.protected_paths)
    if not touched:
        return []
    problems = _exception_violations(root, merge_base, branch, POLICY_BRANCH_PREFIX, touched)
    if not problems:
        return []
    return [f"{rel_path} [ERR_PROTECTED]: path is protected by policy" for rel_path in touched] + (
        problems
    )


def _run_against_base_source(
    root: Path, workflow: Workflow, merge_base: str, sources: list[str]
) -> tuple[int, str]:
    """Run test_command on HEAD with the source files reset to merge_base."""
    patterns = [
        pattern
        for pattern in workflow.source_paths
        if any(matches_glob(rel_path, pattern) for rel_path in sources)
    ]
    with tempfile.TemporaryDirectory() as scratch:
        tree = Path(scratch) / "red"
        _git(root, "worktree", "add", "--detach", "--quiet", str(tree), "HEAD")
        try:
            pathspecs = [f":(glob){pattern}" for pattern in patterns]
            _git(tree, "restore", f"--source={merge_base}", "--worktree", "--", *pathspecs)
            return _run(tree, workflow.test_command, frozenset())
        finally:
            _git(root, "worktree", "remove", "--force", str(tree))


def _red_violations(
    root: Path, workflow: Workflow, merge_base: str, branch: str, changed: list[str]
) -> list[str]:
    sources = matching_paths(changed, workflow.source_paths)
    if not sources:
        return []
    code, detail = _run_against_base_source(root, workflow, merge_base, sources)
    if code in workflow.red_exit_codes:
        return []
    problems = _exception_violations(root, merge_base, branch, REFACTOR_BRANCH_PREFIX, sources)
    if not problems:
        return []
    red = (
        f"{workflow.test_command} [ERR_RED]: expected exit in {list(workflow.red_exit_codes)} "
        f"against base source, got exit {code}: {detail}"
    )
    return [red, *problems]


def check_merge(root: Path, config_rel: str, base_ref: str, branch: str | None) -> list[str]:
    """Check HEAD against base_ref using the workflow rules stored on base_ref.

    Protected paths may only change on a policy/* branch, source changes need tests that fail
    against the base source unless on a refactor/* branch; both exceptions need a Reason line
    in every commit touching the paths. Afterwards the base workflow checks run on HEAD.
    """
    if _git(root, "status", "--porcelain").strip():
        return ["working-tree [ERR_DIRTY]: uncommitted changes present"]
    workflow = parse_workflow(_git(root, "show", f"{base_ref}:{config_rel}"))
    merge_base = _git(root, "merge-base", base_ref, "HEAD").strip()
    branch_name = branch or _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    changed = _split_nul(
        _git(root, "diff", "-z", "--name-only", "--no-renames", merge_base, "HEAD")
    )
    violations = _protected_violations(root, workflow, merge_base, branch_name, changed)
    violations += _red_violations(root, workflow, merge_base, branch_name, changed)
    return violations or run_commands(root, workflow.checks)
