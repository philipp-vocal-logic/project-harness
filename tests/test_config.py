"""Unit tests for .policy/workflow.toml loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_harness.config import load_workflow, parse_workflow


def test_parse_workflow_reads_all_keys() -> None:
    workflow = parse_workflow(
        '[workflow]\nprotected_paths = [".policy/**"]\nchecks = ["true"]\n'
        'source_paths = ["src/**"]\ntest_command = "pytest -q"\nred_exit_codes = [1, 2]\n'
    )
    assert workflow.protected_paths == (".policy/**",)
    assert workflow.checks == ("true",)
    assert workflow.source_paths == ("src/**",)
    assert workflow.test_command == "pytest -q"
    assert workflow.red_exit_codes == (1, 2)


@pytest.mark.parametrize(
    ("toml_text", "expected_match"),
    [
        (
            "unknown = 1\n",
            r"unknown keys in workflow\.toml: \['unknown'\] \(allowed: \['workflow'\]\)",
        ),
        ('workflow = "bad"\n', "'workflow' must be a table"),
        ("[workflow]\nstages = []\n", "unknown keys in workflow"),
        ('[workflow]\nprotected_paths = ["../outside"]\n', "glob must be a relative path"),
        ('[workflow]\nchecks = [""]\n', "must be a list of non-empty strings"),
        ('[workflow]\nsource_paths = ["src/**"]\n', "test_command is required"),
        ("[workflow]\ntest_command = 1\n", "must be a string"),
        ("[workflow]\nred_exit_codes = [0]\n", "list of exit codes in 1..255"),
        ("[workflow]\nred_exit_codes = [true]\n", "list of exit codes in 1..255"),
        ("[workflow\n", "Expected"),
    ],
)
def test_parse_workflow_rejects_invalid_configs(toml_text: str, expected_match: str) -> None:
    with pytest.raises(ValueError, match=expected_match):
        parse_workflow(toml_text)


def test_load_workflow_reads_file(tmp_path: Path) -> None:
    config_path = tmp_path / "workflow.toml"
    config_path.write_text('[workflow]\nchecks = ["true"]\n', encoding="utf-8")
    assert load_workflow(config_path).checks == ("true",)
