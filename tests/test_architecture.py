"""Unit tests for .policy/architecture.toml parsing and architecture rule checking."""

from __future__ import annotations

import pytest

from project_harness.architecture import check_architecture_text
from project_harness.config import parse_architecture


def test_check_architecture_detects_line_limit_and_forbid_violations() -> None:
    architecture_rules = parse_architecture(
        '[[rules]]\nfiles = ["src/*.py"]\nmax_lines = 1\nforbid = ["print"]\nfix = "slim"\n'
    )
    violations = check_architecture_text("src/a.py", "x = 1\nprint(x)\n", architecture_rules[0])
    assert len(violations) == 2 and all(
        "[ERR_ARCHITECTURE]" in violation for violation in violations
    )


def test_parse_architecture_rejects_rule_without_max_lines_or_forbid() -> None:
    with pytest.raises(ValueError, match="needs max_lines or forbid"):
        parse_architecture('[[rules]]\nfiles = ["src/*.py"]\nfix = "slim"\n')
