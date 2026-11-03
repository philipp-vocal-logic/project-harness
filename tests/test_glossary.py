"""Unit tests for .policy/glossary.toml parsing and terminology checking."""

from __future__ import annotations

import pytest

from project_harness.config import parse_glossary
from project_harness.glossary import check_glossary_text


def test_check_glossary_detects_forbidden_terms_and_plurals() -> None:
    glossary = parse_glossary(
        '[[terms]]\nterm = "multiply"\ndefinition = "product"\nforbidden_terms = ["times_op"]\n'
    )
    assert check_glossary_text("doc.md", "our times_ops here\n", glossary) != []
    assert check_glossary_text("doc.md", "our multiply here\n", glossary) == []


def test_parse_glossary_rejects_unused_similarity_threshold() -> None:
    with pytest.raises(ValueError, match=r"unknown keys in glossary\.toml"):
        parse_glossary("[settings]\nsimilarity_threshold = 0.65\n")
