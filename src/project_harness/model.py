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
NODE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_UPPER = r"[A-ZÀ-ÖØ-Þ]"
_LOWER = r"[a-zß-öø-ÿ]"
SUBWORD_PATTERN = re.compile(
    rf"{_UPPER}{{2,}}s(?={_UPPER}{_LOWER}|\d|_|\b)"
    rf"|{_UPPER}+(?={_UPPER}{_LOWER}|\d|_|\b)"
    rf"|{_UPPER}?{_LOWER}+|\d+"
)
INVARIANT_SINGULARS: frozenset[str] = frozenset(
    {
        "alias",
        "atlas",
        "bias",
        "bus",
        "canvas",
        "chaos",
        "corpus",
        "gas",
        "lens",
        "news",
        "series",
        "species",
        "status",
        "virus",
    }
)
IRREGULAR_PLURALS: dict[str, str] = {
    "aches": "ache",
    "analyses": "analysis",
    "axes": "axis",
    "caches": "cache",
    "children": "child",
    "cookies": "cookie",
    "crises": "crisis",
    "dbs": "db",
    "diagnoses": "diagnosis",
    "does": "do",
    "goodies": "goodie",
    "hypotheses": "hypothesis",
    "ids": "id",
    "indices": "index",
    "ips": "ip",
    "matrices": "matrix",
    "movies": "movie",
    "niches": "niche",
    "parentheses": "parenthesis",
    "people": "person",
    "prs": "pr",
    "syntheses": "synthesis",
    "theses": "thesis",
    "uis": "ui",
    "vertices": "vertex",
    "vms": "vm",
}


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
class SymbolSchema:
    """Parsed .policy/symbols.toml configuration."""

    forbidden_subwords: tuple[str, ...] = ()
    replacements: tuple[Replacement, ...] = ()


@dataclass(frozen=True, slots=True)
class Symbol:
    """A named code identifier extracted from a source file."""

    name: str
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


def _strip_plural_suffix(word: str) -> str:
    if word.endswith("es") and word[:-2] in INVARIANT_SINGULARS:
        return word[:-2]
    if len(word) > 4 and word.endswith("ies"):
        return f"{word[:-3]}y"
    if word.endswith(("sses", "xes", "zes", "zzes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def singularize(word: str) -> str:
    """Normalize an English subword to its lowercase singular form."""
    if len(word) >= 3 and word.endswith("s") and word[:-1].isupper():
        return word[:-1].lower()
    lower = word.lower()
    if lower in IRREGULAR_PLURALS:
        return IRREGULAR_PLURALS[lower]
    if len(lower) <= 3 or lower in INVARIANT_SINGULARS:
        return lower
    return _strip_plural_suffix(lower)


def normalize_subwords(text: str) -> tuple[str, ...]:
    """Split an identifier or phrase into singularized lowercase subwords."""
    return tuple(singularize(match.group(0)) for match in SUBWORD_PATTERN.finditer(text))


def matches_subwords(words: tuple[str, ...], target: tuple[str, ...]) -> bool:
    """Return True when target appears as a contiguous subword slice within words."""
    if not target:
        return False
    length = len(target)
    return any(words[index : index + length] == target for index in range(len(words) - length + 1))


def has_reason(message: str) -> bool:
    """Return True if a commit message contains a 'Reason: <why>' line."""
    return REASON_PATTERN.search(message) is not None
