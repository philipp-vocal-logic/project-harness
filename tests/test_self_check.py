"""Self-checks verifying project-harness adheres to its own .policy rules."""

from __future__ import annotations

from pathlib import Path

from project_harness import trace
from project_harness.cli import main
from project_harness.config import load_workflow

REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_DIR = REPO_ROOT / ".policy"


@trace("REQ-POLICY")
def test_project_adheres_to_own_glossary() -> None:
    assert main(["glossary", "--config", str(POLICY_DIR / "glossary.toml")]) == 0


@trace("REQ-POLICY")
def test_project_adheres_to_own_symbols() -> None:
    assert main(["symbol", "--config", str(POLICY_DIR / "symbols.toml")]) == 0


@trace("REQ-POLICY")
def test_project_adheres_to_own_architecture() -> None:
    assert main(["arch", "--config", str(POLICY_DIR / "arch.toml")]) == 0


@trace("REQ-POLICY")
def test_project_adheres_to_own_traceability() -> None:
    assert main(["trace", "--config", str(POLICY_DIR / "traceability.toml")]) == 0


@trace("REQ-POLICY")
def test_project_workflow_config_is_valid() -> None:
    workflow = load_workflow(POLICY_DIR / "workflow.toml")
    assert ".policy/**" in workflow.protected_paths
    assert workflow.source_paths == ("src/**",)
