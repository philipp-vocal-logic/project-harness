"""Self-checks verifying project-harness adheres to its own .policy rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_harness.architecture import check_architecture
from project_harness.config import (
    load_workflow,
    parse_architecture,
    parse_symbols,
    parse_traceability,
)
from project_harness.symbol import check_symbols
from project_harness.traceability import check_traceability

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
POLICY_DIRECTORY = REPOSITORY_ROOT / ".policy"


@pytest.fixture(autouse=True)
def _run_in_repository_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run harness from this repository, wherever pytest was started."""
    monkeypatch.chdir(REPOSITORY_ROOT)


def test_project_adheres_to_own_symbols() -> None:
    symbols = parse_symbols((POLICY_DIRECTORY / "symbols.toml").read_text(encoding="utf-8"))
    assert check_symbols(REPOSITORY_ROOT, symbols) == []


def test_project_adheres_to_own_architecture() -> None:
    rules = parse_architecture((POLICY_DIRECTORY / "architecture.toml").read_text(encoding="utf-8"))
    assert check_architecture(REPOSITORY_ROOT, rules) == []


def test_project_adheres_to_own_traceability() -> None:
    config = parse_traceability(
        (POLICY_DIRECTORY / "traceability.toml").read_text(encoding="utf-8")
    )
    assert check_traceability(REPOSITORY_ROOT, config) == []


def test_project_workflow_config_is_valid() -> None:
    workflow = load_workflow(POLICY_DIRECTORY / "workflow.toml")
    assert ".policy/**" in workflow.protected_paths
    assert workflow.source_paths == ("src/**",)
