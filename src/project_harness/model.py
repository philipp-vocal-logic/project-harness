"""Pure domain models, path globs, subword morphology, and Reason lines for project-harness."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

REASON_PATTERN = re.compile(r"^Reason:[ \t]*\S", re.MULTILINE)
POLICY_BRANCH_PREFIX = "policy/"
REFACTOR_BRANCH_PREFIX = "refactor/"
REQUIREMENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
SYMBOL_KINDS: frozenset[str] = frozenset({"module", "class", "function", "method"})
SYMBOL_VISIBILITIES: frozenset[str] = frozenset({"all", "public", "private"})

_UPPER = r"[A-ZÀ-ÖØ-Þ]"
_LOWER = r"[a-zß-öø-ÿ]"
_SUBWORD_PATTERN = re.compile(
    rf"{_UPPER}{{2,}}s(?={_UPPER}{_LOWER}|\d|_|\b)"
    rf"|{_UPPER}+(?={_UPPER}{_LOWER}|\d|_|\b)"
    rf"|{_UPPER}?{_LOWER}+|\d+"
)


@dataclass(frozen=True, slots=True)
class Workflow:
    """Parsed .policy/workflow.toml rules."""

    protected_paths: tuple[str, ...]
    checks: tuple[str, ...]
    source_paths: tuple[str, ...] = ()
    test_command: str = ""
    red_exit_codes: tuple[int, ...] = (1,)


@dataclass(frozen=True, slots=True)
class ArchitectureRule:
    """A .policy/architecture.toml rule binding file globs to line limits and forbidden patterns."""

    files: tuple[str, ...]
    fix: str
    max_lines: int | None = None
    forbid: tuple[re.Pattern[str], ...] = ()


@dataclass(frozen=True, slots=True)
class SymbolRule:
    """A .policy/symbols.toml rule binding files, symbol kinds, and visibility to a pattern."""

    files: tuple[str, ...]
    kinds: frozenset[str]
    pattern: re.Pattern[str]
    fix: str
    visibility: str = "all"


@dataclass(frozen=True, slots=True)
class SymbolSchema:
    """Parsed .policy/symbols.toml configuration."""

    forbidden_subwords: tuple[str, ...] = ()
    replacements: dict[str, tuple[str, ...]] = field(default_factory=dict)
    rules: tuple[SymbolRule, ...] = ()


@dataclass(frozen=True, slots=True)
class Symbol:
    """A named code identifier extracted from a source file."""

    name: str
    kind: str
    file: Path
    line: int
    column: int


@dataclass(frozen=True, slots=True)
class Requirement:
    """A requirement specification linking domain intent to verification tests."""

    id: str
    title: str
    acceptance: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    location: str = ""


@dataclass(frozen=True, slots=True)
class TraceabilityConfig:
    """Parsed .policy/traceability.toml requirement and test file globs."""

    requirements: tuple[str, ...] = ("trace/*.toml",)
    tests: tuple[str, ...] = ("tests/**/test_*.py",)


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


def matches_glob(relative_path: str, pattern: str) -> bool:
    """Return True if a relative POSIX path matches a glob pattern supporting **."""
    return _match_parts(PurePosixPath(relative_path).parts, PurePosixPath(pattern).parts)


def matches_any(relative_path: str, patterns: Sequence[str]) -> bool:
    """Return True if relative_path matches at least one glob pattern."""
    return any(matches_glob(relative_path, pattern) for pattern in patterns)


def matching_paths(paths: Sequence[str], patterns: Sequence[str]) -> list[str]:
    """Return the sorted unique paths that match at least one glob pattern."""
    return sorted(
        {relative_path for relative_path in paths if matches_any(relative_path, patterns)}
    )


def split_subwords(text: str) -> tuple[str, ...]:
    """Split an identifier or phrase into lowercase subwords."""
    return tuple(match.group(0).lower() for match in _SUBWORD_PATTERN.finditer(text))


def subword_forms(word: str) -> frozenset[str]:
    """Return a lowercase subword and its regular English plural forms."""
    base = word.lower()
    forms = {base, f"{base}s"}
    if base.endswith(("s", "x", "z", "ch", "sh")):
        forms.add(f"{base}es")
    if len(base) > 1 and base.endswith("y") and base[-2] not in "aeiou":
        forms.add(f"{base[:-1]}ies")
    return frozenset(forms)


def has_reason(message: str) -> bool:
    """Return True if a commit message contains a 'Reason: <why>' line."""
    return REASON_PATTERN.search(message) is not None
