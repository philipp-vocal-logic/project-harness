"""Glossary policy checker enforcing canonical terminology and forbidden synonyms."""

from __future__ import annotations

import itertools
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from project_harness.config import walk_files
from project_harness.model import (
    SUBWORD_PATTERN,
    Term,
    normalize_subwords,
    singularize,
)

TOKEN_PATTERN = re.compile(r"\w+(?:-\w+)*")

ForbiddenRule = tuple[tuple[str, ...], str, Term]


@dataclass(frozen=True, slots=True)
class _SubwordSpan:
    word: str
    start: int
    end: int
    token_start: int
    token_end: int


def _build_rule_index(terms: Sequence[Term]) -> dict[str, list[ForbiddenRule]]:
    rule_index: dict[str, list[ForbiddenRule]] = {}
    for entry in terms:
        for forbidden in entry.forbidden_terms:
            if norm := normalize_subwords(forbidden):
                rule_index.setdefault(norm[0], []).append((norm, forbidden, entry))
    return rule_index


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
) -> list[str]:
    violations: list[str] = []
    for index, span in enumerate(spans):
        for forbidden_words, raw_forbidden, entry in rule_index.get(span.word, ()):
            window = spans[index : index + len(forbidden_words)]
            if not _matches_window(line, window, forbidden_words):
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


def check_glossary_text(rel_path: str, text: str, terms: Sequence[Term]) -> list[str]:
    """Check a text string for any forbidden_terms defined in terms."""
    rule_index = _build_rule_index(terms)
    if not rule_index:
        return []
    violations: list[str] = []
    for line_no, raw_line in enumerate(text.split("\n"), start=1):
        line = raw_line.removesuffix("\r")
        spans = _extract_line_subwords(line)
        if spans:
            violations.extend(_check_line_spans(rel_path, line, line_no, spans, rule_index))
    return violations


def check_glossary(root: Path, terms: Sequence[Term]) -> list[str]:
    """Check all tracked text files under root and return violation lines."""
    base = root.resolve()
    violations: list[str] = []
    for file_path in walk_files(base):
        rel = file_path.relative_to(base).as_posix()
        if rel == ".policy/glossary.toml":
            continue
        raw_bytes = file_path.read_bytes()
        if b"\x00" in raw_bytes:
            continue
        violations.extend(check_glossary_text(rel, raw_bytes.decode("utf-8"), terms))
    return violations
