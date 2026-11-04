"""Unit tests for .policy/arch.toml parsing and architecture rule checking."""

from __future__ import annotations

import pytest

from project_harness.arch import check_arch_text
from project_harness.config import parse_arch


def test_check_arch_detects_line_limit_and_forbid_violations() -> None:
    arch_rules = parse_arch(
        '[[rules]]\nfiles = ["src/*.py"]\nmax_lines = 1\nforbid = ["print"]\nfix = "slim"\n'
    )
    violations = check_arch_text("src/a.py", "x = 1\nprint(x)\n", arch_rules[0])
    assert len(violations) == 2 and all("[ERR_ARCH]" in violation for violation in violations)


def test_parse_arch_rejects_rule_without_max_lines_or_forbid() -> None:
    with pytest.raises(ValueError, match="needs max_lines or forbid"):
        parse_arch('[[rules]]\nfiles = ["src/*.py"]\nfix = "slim"\n')
