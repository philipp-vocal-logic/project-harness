"""Unit tests for .policy/traceability.toml parsing and node/test link checking."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from project_harness.config import parse_traceability
from project_harness.traceability import check_trace

TEST_LINK_TRACE_TOML = (
    '[schema]\nchain = ["Story", "Requirement"]\n'
    '[statuses]\nStory = ["APPROVED"]\nRequirement = ["APPROVED"]\n'
    '[locations]\nStory = "stories/*.toml"\nRequirement = "reqs/*.toml"\n'
    '[[rules]]\nid = "REQ-NEEDS-TEST"\nsource_type = "Requirement"\n'
    'when_status = "APPROVED"\ntarget_type = "Test"\n'
)


def _git_init(repo: Path) -> None:
    """Make repo a git work tree so harness can list its files."""
    subprocess.run(["git", "init", "-q", str(repo)], check=True)


def test_check_trace_detects_dangling_links(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "stories").mkdir()
    (tmp_path / "reqs").mkdir()
    (tmp_path / "stories" / "US-1.toml").write_text('status = "APPROVED"\n', encoding="utf-8")
    (tmp_path / "reqs" / "REQ-1.toml").write_text(
        'status = "APPROVED"\nlinks = ["MISSING"]\n', encoding="utf-8"
    )
    trace_config = parse_traceability(
        '[schema]\nchain = ["Story", "Requirement"]\n'
        '[statuses]\nStory = ["APPROVED"]\nRequirement = ["APPROVED"]\n'
        '[locations]\nStory = "stories/*.toml"\nRequirement = "reqs/*.toml"\n'
    )
    trace_hits = check_trace(tmp_path, trace_config)
    assert any("[ERR_DANGLING_LINK]" in hit for hit in trace_hits)


def test_trace_checks_requirement_side_test_links(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text(
        "class TestCalc:\n    def test_add(self) -> None:\n        pass\n", encoding="utf-8"
    )
    (tmp_path / "stories").mkdir()
    (tmp_path / "stories" / "US-1.toml").write_text('status = "APPROVED"\n', encoding="utf-8")
    (tmp_path / "reqs").mkdir()
    requirements = {
        "REQ-1": '["tests/test_calc.py::TestCalc::test_add"]',
        "REQ-2": '["tests/test_calc.py::test_sub", "tests/missing.py::test_add"]',
        "REQ-3": "[]",
    }
    for requirement_id, tests in requirements.items():
        (tmp_path / "reqs" / f"{requirement_id}.toml").write_text(
            f'status = "APPROVED"\ntests = {tests}\n', encoding="utf-8"
        )
    trace_config = parse_traceability(TEST_LINK_TRACE_TOML)
    assert check_trace(tmp_path, trace_config) == [
        "reqs/REQ-2.toml: REQ-2 [ERR_DANGLING_TEST]: "
        "test 'tests/test_calc.py::test_sub' does not exist",
        "reqs/REQ-2.toml: REQ-2 [ERR_DANGLING_TEST]: "
        "test 'tests/missing.py::test_add' does not exist",
        "reqs/REQ-3.toml: REQ-3 [ERR_MISSING_LINK] (REQ-NEEDS-TEST): requires >= 1 test",
    ]

    (tmp_path / "reqs" / "REQ-3.toml").write_text(
        'status = "APPROVED"\ntests = ["tests/test_calc.py"]\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="relative path"):
        check_trace(tmp_path, trace_config)


@pytest.mark.parametrize(
    ("rule_tail", "expected_match"),
    [
        ('target_type = "Test"\ntarget_status = ["APPROVED"]\n', "target_status is not allowed"),
        ('target_type = "Tests"\n', "types must be chain types"),
    ],
)
def test_parse_traceability_rejects_invalid_test_rules(rule_tail: str, expected_match: str) -> None:
    text = TEST_LINK_TRACE_TOML.replace('target_type = "Test"\n', rule_tail)
    with pytest.raises(ValueError, match=expected_match):
        parse_traceability(text)
