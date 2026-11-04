"""Unit tests for pure domain models, path globs, subword morphology, and Reason lines."""

from __future__ import annotations

import pytest

from project_harness.model import (
    has_reason,
    matches_any,
    matches_glob,
    matching_paths,
    split_subwords,
    subword_forms,
)


@pytest.mark.parametrize(
    ("relative_path", "pattern", "expected"),
    [
        ("src/app.py", "src/**/*.py", True),
        ("src/pkg/sub/app.py", "src/**/*.py", True),
        ("tests/test_app.py", "src/**/*.py", False),
        (".policy/workflow.toml", ".policy/**", True),
        (".policy", ".policy/**", False),
        ("trace/requirements/REQ-1.toml", "trace/**/*.toml", True),
        ("README.md", "README.md", True),
    ],
)
def test_matches_glob_and_matches_any(relative_path: str, pattern: str, expected: bool) -> None:
    assert matches_glob(relative_path, pattern) is expected
    assert matches_any(relative_path, [pattern]) is expected


def test_matching_paths_returns_sorted_unique_matches() -> None:
    paths = ["src/b.py", ".policy/a.toml", "src/a.py", "src/b.py", "README.md"]
    assert matching_paths(paths, ["src/**", ".policy/**"]) == [
        ".policy/a.toml",
        "src/a.py",
        "src/b.py",
    ]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("chore: raise limit\n\nReason: core grew\n", True),
        ("Reason: first line counts too", True),
        ("chore: x\n\nReason:\n", False),
        ("chore: x\n\n  Reason: indented\n", False),
        ("chore: x\n", False),
    ],
)
def test_has_reason(message: str, expected: bool) -> None:
    assert has_reason(message) is expected


def test_split_subwords_and_subword_forms_handle_camel_case_acronyms_and_plurals() -> None:
    assert split_subwords("HTTPResponses_and_URLs") == ("http", "responses", "and", "urls")
    assert subword_forms("policy") == frozenset({"policy", "policys", "policies"})
    assert subword_forms("branch") == frozenset({"branch", "branchs", "branches"})
