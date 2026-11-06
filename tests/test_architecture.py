"""Unit tests for .policy/architecture.toml parsing and architecture rule checking."""

from __future__ import annotations

import pytest

from project_harness.architecture import check_architecture_text
from project_harness.config import parse_architecture


def test_check_architecture_detects_line_limit_and_forbid_violations() -> None:
    architecture_rules = parse_architecture(
        '[[rules]]\nfiles = ["src/*.py"]\nmax_lines = 1\nforbid = ["print"]\nfix = "slim"\n'
    )
    rule = architecture_rules[0]
    assert check_architecture_text("src/a.py", "", rule) == []
    assert check_architecture_text("src/a.py", "x = 1\n", rule) == []
    assert check_architecture_text("src/a.py", "x = 1\nprint(x)\n", rule) == [
        "src/a.py:1:1 [ERR_ARCHITECTURE]: 2/1 lines (Fix: slim)",
        "src/a.py:2:1 [ERR_ARCHITECTURE]: print(x) (Fix: slim)",
    ]


@pytest.mark.parametrize(
    ("bad_toml", "expected_match"),
    [
        ("", "at least one"),
        ('[[rules]]\nfiles = []\nmax_lines = 10\nfix = "slim"\n', "must not be empty"),
        ('[[rules]]\nfiles = ["src/*.py"]\nmax_lines = 0\nfix = "slim"\n', "positive integer"),
        ('[[rules]]\nfiles = ["src/*.py"]\nforbid = ["("]\nfix = "slim"\n', "invalid forbid regex"),
        ('[[rules]]\nfiles = ["src/*.py"]\nmax_lines = 10\nfix = ""\n', "non-empty string"),
        ('[[rules]]\nfiles = ["src/*.py"]\nfix = "slim"\n', "needs max_lines or forbid"),
    ],
)
def test_parse_architecture_rejects_rule_without_max_lines_or_forbid(
    bad_toml: str, expected_match: str
) -> None:
    with pytest.raises(ValueError, match=expected_match):
        parse_architecture(bad_toml)
