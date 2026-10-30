"""Integration tests exercising the stage check, red-test check, commit hook, and merge gate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from trace_check import trace

from project_harness.cli import main

PYTHON_BIN = sys.executable

WORKFLOW_TOML = f"""[workflow]
protected_paths = [".policy/**"]
checks = ["{PYTHON_BIN} -m pytest -q"]

[[stages]]
id = "planner"
commit_prefix = "plan"
allow_write = ["trace/**/*.toml"]
checks = []

[[stages]]
id = "red-test"
commit_prefix = "test"
allow_write = ["tests/**/*.py"]
checks = ["{PYTHON_BIN} -m pytest --collect-only -q"]
fail_checks = ["{PYTHON_BIN} -m pytest -q"]

[[stages]]
id = "implementer"
commit_prefix = "feat"
allow_write = ["src/**/*.py"]
checks = ["{PYTHON_BIN} -m pytest -q"]

[[stages]]
id = "reviewer"
commit_prefix = "review"
allow_write = ["trace/**/*.toml"]
checks = ["{PYTHON_BIN} -m pytest -q"]
"""


def git_run(repo: Path, *args: str) -> None:
    """Execute a git command in repo, failing on non-zero exit."""
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def init_git_repo(repo: Path) -> Path:
    """Create a baseline git repository on main with .policy/workflow.toml."""
    git_run(repo, "init", "-b", "main")
    git_run(repo, "config", "user.email", "test@example.com")
    git_run(repo, "config", "user.name", "Test User")
    (repo / ".gitignore").write_text("__pycache__/\n*.py[cod]\n.pytest_cache/\n", encoding="utf-8")
    (repo / ".policy").mkdir()
    cfg = repo / ".policy" / "workflow.toml"
    cfg.write_text(WORKFLOW_TOML, encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return 0\n")
    (repo / "tests").mkdir()
    (repo / "trace").mkdir()
    git_run(repo, "add", "-A")
    git_run(repo, "commit", "-m", "chore(init): baseline")
    return cfg


@trace("REQ-STAGE")
def test_stage_check_enforces_scope_and_protected_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = init_git_repo(tmp_path)
    (tmp_path / "src" / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return 1\n")
    (tmp_path / ".policy" / "workflow.toml").write_text(WORKFLOW_TOML + "\n")

    code = main(["--config", str(cfg), "check", "--stage", "planner"])
    output = capsys.readouterr().out
    assert code == 1
    assert "[ERR_PROTECTED]" in output
    assert "[ERR_SCOPE] (planner)" in output


@trace("REQ-STAGE")
def test_red_test_stage_requires_failing_test_and_rejects_passing_or_broken_test(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = init_git_repo(tmp_path)
    test_file = tmp_path / "tests" / "test_calc.py"

    # 1. Passing test in red-test stage must be rejected with ERR_RED_CHECK
    test_file.write_text("def test_trivial() -> None:\n    assert True\n", encoding="utf-8")
    assert main(["--config", str(cfg), "check", "--stage", "red-test"]) == 1
    assert "[ERR_RED_CHECK]" in capsys.readouterr().out

    # 2. Syntax error in red-test stage must be rejected by collect-only / ERR_CHECK
    test_file.write_text("def test_broken(:\n", encoding="utf-8")
    assert main(["--config", str(cfg), "check", "--stage", "red-test"]) == 1
    assert "[ERR_CHECK]: ERROR " in capsys.readouterr().out

    # 3. Genuinely failing test in red-test stage passes (exit 0)
    test_file.write_text(
        "from src.calc import add\ndef test_add() -> None:\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    assert main(["--config", str(cfg), "check", "--stage", "red-test"]) == 0
    assert main(["--config", str(cfg), "check"]) == 1
    assert "[ERR_CHECK]: FAILED " in capsys.readouterr().out


@trace("REQ-STAGE")
def test_commit_msg_check_validates_subject_and_staged_scope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = init_git_repo(tmp_path)
    (tmp_path / "trace" / "REQ-1.toml").write_text('status = "DRAFT"\n', encoding="utf-8")
    git_run(tmp_path, "add", "trace/REQ-1.toml")

    msg_file = tmp_path / "COMMIT_EDITMSG"
    msg_file.write_text("bad commit message\n", encoding="utf-8")
    assert main(["--config", str(cfg), "check", "--commit-msg", str(msg_file)]) == 1
    assert "[ERR_COMMIT_MSG]" in capsys.readouterr().out

    msg_file.write_text("plan(REQ-1): create draft requirement\n", encoding="utf-8")
    assert main(["--config", str(cfg), "check", "--commit-msg", str(msg_file)]) == 0


@trace("REQ-MERGE")
def test_merge_check_rejects_skipped_stages_and_tampered_tests(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = init_git_repo(tmp_path)
    git_run(tmp_path, "checkout", "-b", "feat/skipped")
    (tmp_path / "trace" / "REQ-1.toml").write_text('status = "APPROVED"\n', encoding="utf-8")
    git_run(tmp_path, "add", "-A")
    git_run(tmp_path, "commit", "-m", "plan(REQ-1): plan")

    # Skip red-test and jump straight to implementer while touching tests/
    (tmp_path / "src" / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
    (tmp_path / "tests" / "test_calc.py").write_text("def test_ok() -> None:\n    assert True\n")
    git_run(tmp_path, "add", "-A")
    git_run(tmp_path, "commit", "-m", "feat(REQ-1): implement and tamper with tests")

    assert main(["--config", str(cfg), "check", "--base", "main"]) == 1
    out = capsys.readouterr().out
    assert "[ERR_SCOPE] (implementer)" in out
