"""Domain model, path glob matching, and Reason lines for project-harness."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

REASON_PATTERN = re.compile(r"^Reason:[ \t]*\S", re.MULTILINE)
POLICY_BRANCH_PREFIX = "policy/"
REFACTOR_BRANCH_PREFIX = "refactor/"


@dataclass(frozen=True, slots=True)
class Workflow:
    """Parsed .policy/workflow.toml rules."""

    protected_paths: tuple[str, ...]
    checks: tuple[str, ...]
    source_paths: tuple[str, ...] = ()
    test_command: str = ""
    red_exit_codes: tuple[int, ...] = (1,)


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


def matching_paths(paths: Sequence[str], patterns: Sequence[str]) -> list[str]:
    """Return the sorted unique paths that match at least one glob pattern."""
    return sorted({rel_path for rel_path in paths if matches_any(rel_path, patterns)})


def has_reason(message: str) -> bool:
    """Return True if a commit message contains a 'Reason: <why>' line."""
    return REASON_PATTERN.search(message) is not None
