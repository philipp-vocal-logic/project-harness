"""Self-checks verifying project-harness adheres to its own .policy rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_harness.cli import main
from project_harness.config import load_workflow

REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_DIR = REPO_ROOT / ".policy"


@pytest.fixture(autouse=True)
def _run_in_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run harness from this repository, wherever pytest was started."""
    monkeypatch.chdir(REPO_ROOT)


def test_project_adheres_to_own_symbols() -> None:
    assert main(["symbol"]) == 0


def test_project_adheres_to_own_architecture() -> None:
    assert main(["arch"]) == 0


def test_project_adheres_to_own_traceability() -> None:
    assert main(["trace"]) == 0


def test_project_workflow_config_is_valid() -> None:
    workflow = load_workflow(POLICY_DIR / "workflow.toml")
    assert ".policy/**" in workflow.protected_paths
    assert workflow.source_paths == ("src/**",)
