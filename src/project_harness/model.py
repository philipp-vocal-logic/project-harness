"""Pure domain models, path globs, subword morphology, and Reason lines for project-harness."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

REASON_PATTERN = re.compile(r"^Reason:[ \t]*\S", re.MULTILINE)
POLICY_BRANCH_PREFIX = "policy/"
REFACTOR_BRANCH_PREFIX = "refactor/"
DEPRECATED_STATUS = "DEPRECATED"
TEST_TYPE = "Test"
NODE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
SYMBOL_KINDS: frozenset[str] = frozenset(
    {
        "module",
        "class",
        "type_var",
        "function",
        "method",
        "constant",
        "variable",
        "parameter",
        "attribute",
        "alias",
    }
)
SYMBOL_VISIBILITIES: frozenset[str] = frozenset({"all", "public", "private"})

_UPPER = r"[A-ZÀ-ÖØ-Þ]"
_LOWER = r"[a-zß-öø-ÿ]"
SUBWORD_PATTERN = re.compile(
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
class ArchRule:
    """A single .policy/arch.toml rule binding file globs to line limits and forbidden regexes."""

    files: tuple[str, ...]
    fix: str
    max_lines: int | None = None
    forbid: tuple[re.Pattern[str], ...] = ()


@dataclass(frozen=True, slots=True)
class Term:
    """An approved canonical term and its forbidden synonyms in .policy/glossary.toml."""

    term: str
    definition: str
    forbidden_terms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Replacement:
    """A canonical symbol subword and its disallowed synonyms in .policy/symbols.toml."""

    canonical: str
    forbidden: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SymbolRule:
    """A single .policy/symbols.toml rule binding files, symbol kinds, and visibility to a regex."""

    files: tuple[str, ...]
    kinds: frozenset[str]
    pattern: re.Pattern[str]
    fix: str
    visibility: str = "all"


@dataclass(frozen=True, slots=True)
class SymbolSchema:
    """Parsed .policy/symbols.toml configuration."""

    forbidden_subwords: tuple[str, ...] = ()
    replacements: tuple[Replacement, ...] = ()
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
class Node:
    """A single traceability node in the project graph."""

    id: str
    type: str
    status: str
    links: tuple[str, ...] = ()
    location: str = ""
    tests: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TraceRule:
    """A declarative status and link coverage constraint in .policy/traceability.toml.

    A target_type of TEST_TYPE requires at least one entry in the node's tests list.
    """

    id: str
    source_type: str
    when_status: str
    target_type: str
    target_status: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TraceConfig:
    """Parsed .policy/traceability.toml schema, statuses, locations, and rules."""

    chain: tuple[str, ...]
    statuses: dict[str, frozenset[str]]
    locations: dict[str, str]
    rules: tuple[TraceRule, ...]


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


def split_subwords(text: str) -> tuple[str, ...]:
    """Split an identifier or phrase into lowercase subwords."""
    return tuple(match.group(0).lower() for match in SUBWORD_PATTERN.finditer(text))


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
