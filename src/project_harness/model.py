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
SYMBOL_KINDS: frozenset[str] = frozenset(
    {
        "module",
        "class",
        "function",
        "method",
        "macro",
        "typedef",
        "type",
        "enumerator",
        "variable",
    }
)
SYMBOL_VISIBILITIES: frozenset[str] = frozenset({"all", "public", "private"})
_C_COMMENT_OR_LITERAL_PATTERN = re.compile(
    r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|/\*[\s\S]*?\*/|//[^\n]*'
)
_C_DIRECTIVE_PATTERN = re.compile(r"^\s*#\s*(if\s+0\b|if\b|ifdef\b|ifndef\b|else\b|endif\b)")

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
    files: tuple[str, ...] = ("**/*.py",)


@dataclass(frozen=True, slots=True)
class Symbol:
    """A named code identifier extracted from a source file."""

    name: str
    kind: str
    file: Path
    line: int
    column: int
    is_private: bool = False


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


def _blank_span(span: str) -> str:
    return "".join("\n" if char == "\n" else " " for char in span)


def _next_disabled_depth(depth: int, directive: str) -> int:
    if depth == 0:
        return 1 if directive.startswith("if") and directive.endswith("0") else 0
    if directive.startswith("if"):
        return depth + 1
    if directive == "endif" or (directive == "else" and depth == 1):
        return depth - 1
    return depth


def blank_c_source(text: str) -> str:
    """Replace C comments, literals, and '#if 0' blocks with spaces while preserving newlines."""
    without_comments = _C_COMMENT_OR_LITERAL_PATTERN.sub(
        lambda match: _blank_span(match.group(0)), text
    )
    lines: list[str] = []
    depth = 0
    for line in without_comments.splitlines(keepends=True):
        directive = match.group(1) if (match := _C_DIRECTIVE_PATTERN.match(line)) else ""
        next_depth = _next_disabled_depth(depth, directive)
        lines.append(_blank_span(line) if (depth or next_depth) else line)
        depth = next_depth
    return "".join(lines)
