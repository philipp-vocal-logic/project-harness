"""Integration tests for the merge check: protected paths, red check, and Reason exceptions."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from project_harness.cli import main

PYTHON_BIN = sys.executable
TEST_COMMAND = f"{PYTHON_BIN} -m pytest -q -p no:cacheprovider"

WORKFLOW_TOML = f"""[workflow]
protected_paths = [".policy/**", "ci/**"]
checks = ["{TEST_COMMAND}"]
source_paths = ["src/**"]
test_command = "{TEST_COMMAND}"
red_exit_codes = [1, 2]
"""

ADD_TEST = "from src.calc import add\n\n\ndef test_add() -> None:\n    assert add(2, 3) == 5\n"
ADD_IMPL = "def add(a: int, b: int) -> int:\n    return a + b\n"


def git_run(repo: Path, *args: str) -> None:
    """Execute a git command in repo, failing on non-zero exit."""
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def commit_file(repo: Path, rel_path: str, content: str, *message: str) -> None:
    """Write rel_path and commit it with the given message paragraphs."""
    target = repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    git_run(repo, "add", "-A")
    git_run(repo, "commit", *(part for paragraph in message for part in ("-m", paragraph)))


def init_git_repo(repo: Path, branch: str) -> Path:
    """Create a stable main with .policy/workflow.toml and switch to a new branch."""
    git_run(repo, "init", "-b", "main")
    git_run(repo, "config", "user.email", "test@example.com")
    git_run(repo, "config", "user.name", "Test User")
    (repo / ".gitignore").write_text("__pycache__/\n*.py[cod]\n", encoding="utf-8")
    (repo / ".policy").mkdir()
    (repo / ".policy" / "workflow.toml").write_text(WORKFLOW_TOML, encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return 0\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_smoke.py").write_text("def test_smoke() -> None:\n    pass\n")
    git_run(repo, "add", "-A")
    git_run(repo, "commit", "-m", "chore: baseline")
    git_run(repo, "checkout", "-b", branch)
    return repo / ".policy" / "workflow.toml"


def merge_check(cfg: Path, branch: str) -> int:
    """Run the merge check of HEAD against main for branch."""
    return main(["--config", str(cfg), "check", "--base", "main", "--branch", branch])


def test_merge_check_accepts_test_first_source_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = init_git_repo(tmp_path, "feat/add")
    commit_file(tmp_path, "tests/test_calc.py", ADD_TEST, "test: add")
    commit_file(tmp_path, "src/calc.py", ADD_IMPL, "feat: add")

    assert merge_check(cfg, "feat/add") == 0
    assert capsys.readouterr().out == ""
    assert not list((tmp_path / ".git" / "worktrees").glob("*"))


def test_merge_check_rejects_source_change_without_red_test(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = init_git_repo(tmp_path, "feat/add")
    commit_file(tmp_path, "src/calc.py", ADD_IMPL, "feat: add without test")

    assert merge_check(cfg, "feat/add") == 1
    out = capsys.readouterr().out
    assert "[ERR_RED]: expected exit in [1, 2] against base source, got exit 0" in out
    assert "branch 'feat/add' [ERR_BRANCH]: exception requires a 'refactor/*' branch" in out


def test_refactor_branch_needs_reason_in_source_commits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = init_git_repo(tmp_path, "refactor/calc")
    body = "def add(a: int, b: int) -> int:\n    result = 0\n    return result\n"
    commit_file(tmp_path, "src/calc.py", body, "refactor: name result")

    assert merge_check(cfg, "refactor/calc") == 1
    assert "[ERR_REASON]: missing 'Reason: <why>' line" in capsys.readouterr().out

    git_run(tmp_path, "commit", "--amend", "-m", "refactor: name result", "-m", "Reason: clarity")
    assert merge_check(cfg, "refactor/calc") == 0
    assert capsys.readouterr().out == ""


def test_merge_check_rejects_protected_change_using_base_rules(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = init_git_repo(tmp_path, "fix/ci")
    # The branch drops all protection, but the rules are read from main.
    loosened = WORKFLOW_TOML.replace('[".policy/**", "ci/**"]', "[]")
    commit_file(tmp_path, ".policy/workflow.toml", loosened, "fix: loosen")
    commit_file(tmp_path, "ci/run.sh", "true\n", "fix: ci", "Reason: needed")

    assert merge_check(cfg, "fix/ci") == 1
    assert capsys.readouterr().out.splitlines() == [
        ".policy/workflow.toml [ERR_PROTECTED]: path is protected by policy",
        "ci/run.sh [ERR_PROTECTED]: path is protected by policy",
        "branch 'fix/ci' [ERR_BRANCH]: exception requires a 'policy/*' branch",
    ]


def test_policy_branch_needs_reason_in_every_protected_commit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = init_git_repo(tmp_path, "policy/ci")
    commit_file(tmp_path, "ci/run.sh", "true\n", "chore: add ci", "Reason: run checks in CI")
    commit_file(tmp_path, "ci/run.sh", "true\ntrue\n", "chore: tweak ci")
    sha = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "--short", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert merge_check(cfg, "policy/ci") == 1
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "ci/run.sh [ERR_PROTECTED]: path is protected by policy",
        f"{sha} [ERR_REASON]: missing 'Reason: <why>' line",
    ]

    git_run(tmp_path, "commit", "--amend", "-m", "chore: tweak ci", "-m", "Reason: second step")
    assert merge_check(cfg, "policy/ci") == 0
    assert capsys.readouterr().out == ""


def test_merge_check_sees_renamed_and_non_ascii_protected_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = init_git_repo(tmp_path, "fix/move")
    git_run(tmp_path, "mv", ".policy/workflow.toml", "workflow.toml")
    git_run(tmp_path, "commit", "-m", "fix: move")
    commit_file(tmp_path, "ci/ä.sh", "true\n", "fix: unicode")

    assert merge_check(cfg, "fix/move") == 1
    out = capsys.readouterr().out
    assert ".policy/workflow.toml [ERR_PROTECTED]" in out
    assert "ci/ä.sh [ERR_PROTECTED]" in out


def test_merge_check_rejects_dirty_tree_and_reports_config_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = init_git_repo(tmp_path, "fix/x")
    (tmp_path / "README.md").write_text("dirty\n", encoding="utf-8")
    assert merge_check(cfg, "fix/x") == 1
    assert capsys.readouterr().out == "working-tree [ERR_DIRTY]: uncommitted changes present\n"

    (tmp_path / "README.md").unlink()
    assert main(["--config", str(cfg), "check", "--base", "missing-ref"]) == 2
    assert "[ERR_CONFIG]" in capsys.readouterr().err


def test_merge_check_runs_base_checks_after_rules_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = init_git_repo(tmp_path, "docs/readme")
    commit_file(tmp_path, "tests/test_fail.py", "def test_fail() -> None:\n    assert 0\n", "docs")

    assert merge_check(cfg, "docs/readme") == 1
    assert "[ERR_CHECK]: FAILED " in capsys.readouterr().out
    assert main(["--config", str(cfg), "check"]) == 1
