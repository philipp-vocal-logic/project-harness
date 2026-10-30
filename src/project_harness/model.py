"""Domain models, path glob matching, and commit subject parsing for project-harness."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

COMMIT_PATTERN = re.compile(r"^([a-z][a-z0-9_-]*)(?:\(([A-Za-z0-9_.-]+)\))?!?: (.+)$")
GIT_GENERATED_PATTERN = re.compile(r"^(Merge|Revert) ")
REASON_PATTERN = re.compile(r"^Reason:[ \t]*\S")
MAX_SUBJECT_LENGTH = 72


@dataclass(frozen=True, slots=True)
class Stage:
    """A single workflow stage bound to a commit prefix, write globs, and check commands."""

    id: str
    commit_prefix: str
    allow_write: tuple[str, ...]
    checks: tuple[str, ...] = ()
    fail_checks: tuple[str, ...] = ()
    prompt: str = ""
    fail_exit_codes: tuple[int, ...] = (1,)


@dataclass(frozen=True, slots=True)
class Orchestrator:
    """Configuration for automated agent execution across workflow stages."""

    agent_command: tuple[str, ...]
    max_retries: int = 3


@dataclass(frozen=True, slots=True)
class Workflow:
    """Parsed .policy/workflow.toml rules, ordered stages, and optional orchestrator."""

    protected_paths: tuple[str, ...]
    checks: tuple[str, ...]
    stages: tuple[Stage, ...]
    orchestrator: Orchestrator | None = None
    commit_types: tuple[str, ...] = ()
    policy_commit_type: str = ""

    def stage_by_id(self, stage_id: str) -> Stage | None:
        """Return the configured stage with the given id, or None."""
        return next((stage for stage in self.stages if stage.id == stage_id), None)

    def stage_by_prefix(self, prefix: str) -> Stage | None:
        """Return the configured stage with the given commit_prefix, or None."""
        return next((stage for stage in self.stages if stage.commit_prefix == prefix), None)


@dataclass(frozen=True, slots=True)
class CommitHeader:
    """Parsed commit subject matching <prefix>[(<target>)][!]: <summary>."""

    prefix: str
    target: str | None
    summary: str


def _match_parts(path_parts: tuple[str, ...], pattern_parts: tuple[str, ...]) -> bool:
    if not pattern_parts:
        return not path_parts
    if pattern_parts == ("**",):
        return bool(path_parts)
    if pattern_parts[0] == "**":
        return _match_parts(path_parts, pattern_parts[1:]) or (
            bool(path_parts) and _match_parts(path_parts[1:], pattern_parts)
        )
    return (
        bool(path_parts)
        and fnmatchcase(path_parts[0], pattern_parts[0])
        and _match_parts(path_parts[1:], pattern_parts[1:])
    )


def matches_glob(rel_path: str, pattern: str) -> bool:
    """Return True if a relative POSIX path matches a glob pattern supporting **."""
    return _match_parts(PurePosixPath(rel_path).parts, PurePosixPath(pattern).parts)


def matches_any(rel_path: str, patterns: Sequence[str]) -> bool:
    """Return True if rel_path matches at least one glob pattern."""
    return any(matches_glob(rel_path, pattern) for pattern in patterns)


def parse_subject(subject: str) -> CommitHeader | None:
    """Parse a commit subject line into CommitHeader if valid and <= 72 chars.

    The (<target>) part is optional here; stage commits enforce it separately.
    """
    line = subject.strip()
    if len(line) > MAX_SUBJECT_LENGTH:
        return None
    match = COMMIT_PATTERN.match(line)
    if not match:
        return None
    return CommitHeader(match.group(1), match.group(2), match.group(3))


def is_git_generated(subject: str) -> bool:
    """Return True for subjects git creates itself (Merge ..., Revert ...)."""
    return GIT_GENERATED_PATTERN.match(subject.strip()) is not None


def check_paths_protected(
    paths: Sequence[str], protected_paths: Sequence[str], label: str = "working-tree"
) -> list[str]:
    """Report modified paths that match protected_paths."""
    return [
        f"{label}: {rel_path} [ERR_PROTECTED]: path is protected by policy"
        for rel_path in sorted(set(paths))
        if matches_any(rel_path, protected_paths)
    ]


def check_policy_commit(
    paths: Sequence[str], message: str, protected_paths: Sequence[str], label: str = "working-tree"
) -> list[str]:
    """Check that a policy commit only touches protected_paths and has a 'Reason: <why>' line."""
    violations = [
        f"{label}: {rel_path} [ERR_SCOPE] (policy): path outside protected_paths"
        for rel_path in sorted(set(paths))
        if not matches_any(rel_path, protected_paths)
    ]
    if not any(REASON_PATTERN.match(line) for line in message.splitlines()):
        violations.append(f"{label} [ERR_POLICY_REASON]: missing 'Reason: <why>' line")
    return violations


def check_paths_scope(
    paths: Sequence[str], stage: Stage, protected_paths: Sequence[str], label: str = "working-tree"
) -> list[str]:
    """Check modified paths against protected_paths and stage.allow_write globs."""
    outside = [
        f"{label}: {rel_path} [ERR_SCOPE] ({stage.id}): "
        f"path outside allow_write {list(stage.allow_write)}"
        for rel_path in sorted(set(paths))
        if not matches_any(rel_path, (*protected_paths, *stage.allow_write))
    ]
    return sorted(check_paths_protected(paths, protected_paths, label) + outside)
