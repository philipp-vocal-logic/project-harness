"""Command execution, built-in policy orchestration, and merge checks for project-harness."""

from __future__ import annotations

import re
import shlex
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from project_harness.arch import check_arch
from project_harness.config import (
    load_workflow,
    parse_arch,
    parse_glossary,
    parse_symbols,
    parse_traceability,
    parse_workflow,
)
from project_harness.glossary import check_glossary
from project_harness.model import (
    POLICY_BRANCH_PREFIX,
    REFACTOR_BRANCH_PREFIX,
    Workflow,
    has_reason,
    matches_glob,
    matching_paths,
)
from project_harness.symbol import check_symbols
from project_harness.traceability import check_trace

BUILTIN_POLICY_FILES: tuple[str, ...] = (
    "arch.toml",
    "glossary.toml",
    "symbols.toml",
    "traceability.toml",
)
FAILURE_LINE_PATTERN = re.compile(
    r"^(?:FAILED |ERROR |error(?:\[E\d+\])?:|test .+ \.\.\. FAILED"
    r"|thread '.+' panicked at|\[\s*FAILED\s*\])"
    r"|:\s*(?:fatal )?error:|Assertion .* failed"
)


def _run(cwd: Path, command: str) -> tuple[int, str]:
    """Run command in cwd; return its exit code and the most relevant output line."""
    args = shlex.split(command)
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
    for command in checks:
        code, detail = _run(root, command)
        if code != 0:
            violations.append(f"{command} [ERR_CHECK]: {detail}")
    return violations


def check_builtin_policies(root: Path, policy_texts: Mapping[str, str]) -> list[str]:
    """Run built-in policy checkers in-process for each present .policy/*.toml text."""
    violations: list[str] = []
    if "arch.toml" in policy_texts:
        violations.extend(check_arch(root, parse_arch(policy_texts["arch.toml"])))
    if "glossary.toml" in policy_texts:
        violations.extend(check_glossary(root, parse_glossary(policy_texts["glossary.toml"])))
    if "symbols.toml" in policy_texts:
        violations.extend(check_symbols(root, parse_symbols(policy_texts["symbols.toml"])))
    if "traceability.toml" in policy_texts:
        violations.extend(check_trace(root, parse_traceability(policy_texts["traceability.toml"])))
    return violations


def _load_local_policies(config_path: Path) -> dict[str, str]:
    policy_dir = config_path.resolve().parent
    if policy_dir.name != ".policy":  # glossary: ignore[policy]
        return {}
    return {
        name: (policy_dir / name).read_text(encoding="utf-8")
        for name in BUILTIN_POLICY_FILES
        if (policy_dir / name).is_file()
    }


def check_local(root: Path, config_path: Path) -> list[str]:
    """Run built-in .policy/*.toml checks and workflow.toml commands on the working tree."""
    workflow = load_workflow(config_path)
    violations = check_builtin_policies(root, _load_local_policies(config_path))
    return violations + run_commands(root, workflow.checks)


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
            return _run(tree, workflow.test_command)
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


def _load_base_policies(root: Path, base_ref: str, config_rel: str) -> dict[str, str]:
    parent_rel = PurePosixPath(config_rel).parent.as_posix()
    if parent_rel != ".policy":  # glossary: ignore[policy]
        return {}
    existing = set(_git(root, "ls-tree", "--name-only", base_ref, ".policy/").splitlines())
    return {
        name: _git(root, "show", f"{base_ref}:.policy/{name}")
        for name in BUILTIN_POLICY_FILES
        if f".policy/{name}" in existing
    }


def check_merge(root: Path, config_rel: str, base_ref: str, branch: str | None) -> list[str]:
    """Check HEAD against base_ref using the workflow and policy rules stored on base_ref."""
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
    if violations:
        return violations
    policy_violations = check_builtin_policies(
        root, _load_base_policies(root, base_ref, config_rel)
    )
    return policy_violations + run_commands(root, workflow.checks)
