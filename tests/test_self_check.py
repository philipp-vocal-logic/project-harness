"""Self-checks verifying project-harness adheres to its own .policy rules."""

from __future__ import annotations

from pathlib import Path

import archcheck
from glossary_checker_cli.checker import check
from glossary_checker_cli.core import load_glossary
from symbol_checker.checker import check_project
from symbol_checker.schema import load_schema
from trace_check import trace
from trace_check.checker import check_config, load_config

from project_harness.config import load_workflow
from project_harness.core import materialize_policy_files

REPO_ROOT = Path(__file__).resolve().parent.parent


@trace("REQ-POLICY")
def test_project_adheres_to_own_glossary() -> None:
    with materialize_policy_files(REPO_ROOT):
        glossary = load_glossary(REPO_ROOT / "glossary.toml")
        assert check(glossary) == []


@trace("REQ-POLICY")
def test_project_adheres_to_own_symbols() -> None:
    schema = load_schema(REPO_ROOT / ".policy" / "symbols.toml")
    assert check_project(schema, root=REPO_ROOT) == []


@trace("REQ-POLICY")
def test_project_adheres_to_own_architecture() -> None:
    with materialize_policy_files(REPO_ROOT):
        assert archcheck.main(["--config", str(REPO_ROOT / "arch.toml")]) == 0


@trace("REQ-POLICY")
def test_project_adheres_to_own_traceability() -> None:
    with materialize_policy_files(REPO_ROOT):
        config = load_config(REPO_ROOT / "traceability.toml")
        assert check_config(config) == []


@trace("REQ-POLICY")
def test_project_workflow_config_is_valid() -> None:
    workflow = load_workflow(REPO_ROOT / ".policy" / "workflow.toml")
    assert ".policy/**" in workflow.protected_paths
    assert workflow.source_paths == ("src/**",)
