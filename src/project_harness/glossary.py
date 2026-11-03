"""Glossary policy checker enforcing canonical terminology and forbidden synonyms."""

from __future__ import annotations

import itertools
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from project_harness.config import DEFAULT_EXCLUDES, walk_files
from project_harness.model import (
    SUBWORD_PATTERN,
    Glossary,
    Term,
    is_excluded,
    normalize_subwords,
    singularize,
)

TOKEN_PATTERN = re.compile(r"\w+(?:-\w+)*")
IGNORE_PATTERN = re.compile(
    r"(?:#|//|--|/\*|<!--)\s*glossary:\s*ignore(?:\[([^\]]+)\])?\s*(?:\*/|-->)?\s*$"
)

ForbiddenRule = tuple[tuple[str, ...], str, Term]


@dataclass(frozen=True, slots=True)
class _SubwordSpan:
    word: str
    start: int
    end: int
    token_start: int
    token_end: int


def _build_rule_index(glossary: Glossary) -> dict[str, list[ForbiddenRule]]:
    rule_index: dict[str, list[ForbiddenRule]] = {}
    for entry in glossary.terms:
        for forbidden in entry.forbidden_terms:
            if norm := normalize_subwords(forbidden):
                rule_index.setdefault(norm[0], []).append((norm, forbidden, entry))
    return rule_index


def _extract_active_line(line: str) -> tuple[str, set[tuple[str, ...]] | None]:
    match = IGNORE_PATTERN.search(line)
    if match is None:
        return line, set()
    raw_bracket = match.group(1)
    if raw_bracket is None:
        return "", None
    ignored = {norm for part in raw_bracket.split(",") if (norm := normalize_subwords(part))}
    return line[: match.start()], ignored


def _extract_line_subwords(line: str) -> list[_SubwordSpan]:
    return [
        _SubwordSpan(
            word=singularize(sub.group(0)),
            start=tok.start() + sub.start(),
            end=tok.start() + sub.end(),
            token_start=tok.start(),
            token_end=tok.end(),
        )
        for tok in TOKEN_PATTERN.finditer(line)
        for sub in SUBWORD_PATTERN.finditer(tok.group(0))
    ]


def _matches_window(
    line: str, window: Sequence[_SubwordSpan], forbidden_words: tuple[str, ...]
) -> bool:
    if tuple(span.word for span in window) != forbidden_words:
        return False
    return all(
        line[prev.end : nxt.start].strip("_- \t") == "" for prev, nxt in itertools.pairwise(window)
    )


def _check_line_spans(
    rel_path: str,
    line: str,
    line_no: int,
    spans: Sequence[_SubwordSpan],
    rule_index: dict[str, list[ForbiddenRule]],
    ignored_norms: set[tuple[str, ...]],
) -> list[str]:
    violations: list[str] = []
    for index, span in enumerate(spans):
        for forbidden_words, raw_forbidden, entry in rule_index.get(span.word, ()):
            window = spans[index : index + len(forbidden_words)]
            if forbidden_words in ignored_norms or not _matches_window(
                line, window, forbidden_words
            ):
                continue
            first, last = window[0], window[-1]
            context = (
                line[first.token_start : first.token_end]
                if first.token_start == last.token_start
                else line[first.start : last.end]
            )
            violations.append(
                f"{rel_path}:{line_no}:{first.start + 1} [ERR_GLOSSARY]: "
                f"forbidden term '{raw_forbidden}' in '{context}' "
                f"(Fix: use '{entry.term}' - {entry.definition})"
            )
    return violations


def check_glossary_text(rel_path: str, text: str, glossary: Glossary) -> list[str]:
    """Check a text string for any forbidden_terms defined in glossary."""
    rule_index = _build_rule_index(glossary)
    if not rule_index:
        return []
    violations: list[str] = []
    for line_no, raw_line in enumerate(text.split("\n"), start=1):
        active_line, ignored_norms = _extract_active_line(raw_line.removesuffix("\r"))
        if ignored_norms is None:
            continue
        spans = _extract_line_subwords(active_line)
        if spans:
            violations.extend(
                _check_line_spans(rel_path, active_line, line_no, spans, rule_index, ignored_norms)
            )
    return violations


def _target_candidates(base: Path, raw_target: str) -> list[Path]:
    target = (base / raw_target).resolve()
    if not target.exists():
        raise ValueError(f"glossary target path does not exist: '{raw_target}'")
    return [target] if target.is_file() else walk_files(target, ("**",), ())


def _discover_glossary_files(root: Path, glossary: Glossary) -> list[Path]:
    base = root.resolve()
    excludes = (*DEFAULT_EXCLUDES, ".policy/glossary.toml", "glossary.toml", *glossary.exclude)
    discovered = [
        file_path
        for raw_target in glossary.paths
        for file_path in _target_candidates(base, raw_target)
        if not is_excluded(file_path.relative_to(base).as_posix(), excludes)
    ]
    return list(dict.fromkeys(discovered))


def check_glossary(root: Path, glossary: Glossary) -> list[str]:
    """Check all configured glossary target files under root and return violation lines."""
    base = root.resolve()
    violations: list[str] = []
    for file_path in _discover_glossary_files(base, glossary):
        raw_bytes = file_path.read_bytes()
        if b"\x00" in raw_bytes:
            continue
        rel = file_path.relative_to(base).as_posix()
        violations.extend(check_glossary_text(rel, raw_bytes.decode("utf-8"), glossary))
    return violations
