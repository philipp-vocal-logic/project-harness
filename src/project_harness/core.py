"""Command execution, built-in policy orchestration, and merge checks for project-harness."""

from __future__ import annotations

import re
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from os import environ
from pathlib import Path

from project_harness.architecture import check_architecture
from project_harness.config import (
    POLICY_DIRECTORY,
    git,
    load_workflow,
    parse_architecture,
    parse_requirement,
    parse_symbols,
    parse_traceability,
    parse_workflow,
)
from project_harness.model import (
    POLICY_BRANCH_PREFIX,
    REFACTOR_BRANCH_PREFIX,
    Requirement,
    Workflow,
    has_reason,
    matching_paths,
)
from project_harness.symbol import check_symbols
from project_harness.traceability import check_requirement_changes, check_traceability

BUILTIN_POLICY_FILES: tuple[str, ...] = (
    "architecture.toml",
    "symbols.toml",
    "traceability.toml",
)
WORKFLOW_FILE = f"{POLICY_DIRECTORY}/workflow.toml"
FAILURE_LINE_PATTERN = re.compile(
    r"^(?:FAILED|ERROR)\b|:\s*(?:fatal )?error:|:FAIL\b|\bAssertion .*failed"
)


def _failure_detail(lines: Sequence[str], returncode: int) -> str:
    if not lines:
        return f"exit code {returncode}"
    if match := next((line for line in lines if FAILURE_LINE_PATTERN.search(line)), None):
        return match
    if len(lines) > 1 and lines[1].startswith("-->"):
        return f"{lines[1].removeprefix('-->').strip()}: {lines[0]}"
    return lines[-1].strip("= ") if lines[0].startswith(("=", ".")) else lines[0]


def _run(cwd: Path, command: str) -> tuple[int, str]:
    """Run command in cwd and return its exit code with the most relevant output line."""
    args = shlex.split(command)
    environment = {key: environ[key] for key in environ.keys() - {"VIRTUAL_ENV"}}
    completed = subprocess.run(
        args, cwd=cwd, env=environment, capture_output=True, text=True, check=False
    )
    raw_lines = f"{completed.stdout}\n{completed.stderr}".splitlines()
    lines = [line.strip() for line in raw_lines if line.strip()]
    return completed.returncode, _failure_detail(lines, completed.returncode)


def run_commands(root: Path, checks: Sequence[str]) -> list[str]:
    """Execute check commands in root and report a violation whenever a command exits non-zero."""
    violations: list[str] = []
    for command in checks:
        code, detail = _run(root, command)
        if code != 0:
            violations.append(f"{command} [ERR_CHECK]: {detail}")
    return violations


def check_builtin_policies(root: Path, policy_texts: Mapping[str, str]) -> list[str]:
    """Run built-in .policy/*.toml checks in-process for each present policy file."""
    violations: list[str] = []
    if "architecture.toml" in policy_texts:
        violations.extend(
            check_architecture(root, parse_architecture(policy_texts["architecture.toml"]))
        )
    if "symbols.toml" in policy_texts:
        violations.extend(check_symbols(root, parse_symbols(policy_texts["symbols.toml"])))
    if "traceability.toml" in policy_texts:
        violations.extend(
            check_traceability(root, parse_traceability(policy_texts["traceability.toml"]))
        )
    return violations


def _load_local_policies(root: Path) -> dict[str, str]:
    policy_directory = root / POLICY_DIRECTORY
    return {
        name: (policy_directory / name).read_text(encoding="utf-8")
        for name in BUILTIN_POLICY_FILES
        if (policy_directory / name).is_file()
    }


def check_local(root: Path) -> list[str]:
    """Run built-in .policy/*.toml checks and workflow.toml commands on the working tree."""
    workflow = load_workflow(root / WORKFLOW_FILE)
    violations = check_builtin_policies(root, _load_local_policies(root))
    return violations + run_commands(root, workflow.checks)


def _split_null(output: str) -> list[str]:
    return [part for part in output.split("\0") if part.strip()]


def _exception_violations(
    root: Path, merge_base: str, branch: str, prefixes: tuple[str, ...], paths: Sequence[str]
) -> list[str]:
    """Allow changes to paths only on a prefix branch whose commits touching them give a Reason."""
    if not branch.startswith(prefixes):
        primary = prefixes[0]
        return [
            f"branch '{branch}' [ERR_BRANCH]: exception requires a '{primary}*' branch "
            f"(Fix: switch to a '{primary}*' branch)"
        ]
    pathspecs = [f":(literal){relative_path}" for relative_path in paths]
    log = git(root, "log", "-z", "--format=%h%n%B", f"{merge_base}..HEAD", "--", *pathspecs)
    return [
        f"{sha} [ERR_REASON]: missing 'Reason: <why>' line "
        f"(Fix: add a 'Reason: <why>' line to commit {sha})"
        for sha, _, message in (entry.partition("\n") for entry in _split_null(log))
        if not has_reason(message)
    ]


def _protected_violations(
    root: Path,
    workflow: Workflow,
    merge_base: str,
    branch: str,
    changed: list[str],
    weakened_requirements: Sequence[str] = (),
) -> list[str]:
    touched = sorted({*matching_paths(changed, workflow.protected_paths), *weakened_requirements})
    if not touched:
        return []
    reasons = _exception_violations(root, merge_base, branch, (POLICY_BRANCH_PREFIX,), touched)
    if not reasons:
        return []
    return [
        f"{relative_path} [ERR_PROTECTED]: path is protected by policy "
        f"(Fix: revert '{relative_path}' or use a 'policy/*' branch with 'Reason: <why>')"
        for relative_path in touched
    ] + reasons


def _run_against_base_source(
    root: Path, workflow: Workflow, merge_base: str, sources: list[str]
) -> tuple[int, str]:
    """Run test_command on HEAD with the source files reset to merge_base."""
    pathspecs = [f":(literal){relative_path}" for relative_path in sources]
    git(root, "restore", f"--source={merge_base}", "--staged", "--worktree", "--", *pathspecs)
    try:
        return _run(root, workflow.test_command)
    finally:
        git(root, "restore", "--source=HEAD", "--staged", "--worktree", "--", *pathspecs)


def _red_violations(
    root: Path, workflow: Workflow, merge_base: str, branch: str, changed: list[str]
) -> list[str]:
    sources = matching_paths(changed, workflow.source_paths)
    if not sources:
        return []
    code, detail = _run_against_base_source(root, workflow, merge_base, sources)
    if code in workflow.red_exit_codes:
        return []
    reasons = _exception_violations(
        root, merge_base, branch, (REFACTOR_BRANCH_PREFIX, POLICY_BRANCH_PREFIX), sources
    )
    if not reasons:
        return []
    red = (
        f"{workflow.test_command} [ERR_RED]: expected exit in {list(workflow.red_exit_codes)} "
        f"against base source, got exit {code}: {detail} "
        "(Fix: add a test that fails against the base source or use a 'refactor/*' branch "
        "with 'Reason: <why>')"
    )
    return [red, *reasons]


def _load_policies(root: Path, reference: str) -> dict[str, str]:
    existing = set(
        git(root, "ls-tree", "--name-only", reference, f"{POLICY_DIRECTORY}/").splitlines()
    )
    return {
        name: git(root, "show", f"{reference}:{POLICY_DIRECTORY}/{name}")
        for name in BUILTIN_POLICY_FILES
        if f"{POLICY_DIRECTORY}/{name}" in existing
    }


def _load_reference_requirements(
    root: Path, reference: str, patterns: Sequence[str]
) -> dict[str, Requirement]:
    listed = git(root, "ls-tree", "-r", "--name-only", reference).splitlines()
    requirements: dict[str, Requirement] = {}
    for relative_path in matching_paths(listed, patterns):
        if relative_path.endswith(".toml"):
            text = git(root, "show", f"{reference}:{relative_path}")
            requirement = parse_requirement(text, relative_path)
            requirements[requirement.id] = requirement
    return requirements


def _weakened_requirements(
    root: Path, base_reference: str, base_policies: Mapping[str, str], changed: Sequence[str]
) -> list[str]:
    if (
        "traceability.toml" not in base_policies
        or f"{POLICY_DIRECTORY}/traceability.toml" in changed
    ):
        return []
    patterns = parse_traceability(base_policies["traceability.toml"]).requirements
    base_requirements = _load_reference_requirements(root, base_reference, patterns)
    head_requirements = _load_reference_requirements(root, "HEAD", patterns)
    return check_requirement_changes(base_requirements, head_requirements)


def check_merge(root: Path, base_reference: str, branch: str | None) -> list[str]:
    """Check HEAD against base_reference using the workflow and policy rules on base_reference.

    Evaluates built-in and workflow checks against the base .policy/ by default, or against HEAD's
    .policy/ when an approved policy/* branch modifies .policy/ files.
    """
    if git(root, "status", "--porcelain").strip():
        return [
            "working-tree [ERR_DIRTY]: uncommitted changes present "
            "(Fix: commit or revert changes before running the merge check)"
        ]
    workflow = parse_workflow(git(root, "show", f"{base_reference}:{WORKFLOW_FILE}"))
    base_policies = _load_policies(root, base_reference)
    merge_base = git(root, "merge-base", base_reference, "HEAD").strip()
    branch_name = branch or git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    changed = _split_null(
        git(root, "diff", "-z", "--name-only", "--no-renames", merge_base, "HEAD")
    )
    weakened = _weakened_requirements(root, base_reference, base_policies, changed)
    violations = _protected_violations(root, workflow, merge_base, branch_name, changed, weakened)
    violations += _red_violations(root, workflow, merge_base, branch_name, changed)
    if violations:
        return violations
    approved = matching_paths(changed, workflow.protected_paths)
    has_policy_change = any(path.startswith(f"{POLICY_DIRECTORY}/") for path in approved)
    policies = _load_policies(root, "HEAD") if has_policy_change else base_policies
    policy_reference = "HEAD" if has_policy_change else base_reference
    checks = parse_workflow(git(root, "show", f"{policy_reference}:{WORKFLOW_FILE}")).checks
    return check_builtin_policies(root, policies) + run_commands(root, checks)
