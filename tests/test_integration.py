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
commit_types = ["chore", "docs"]
policy_commit_type = "policy"
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
checks = ["{PYTHON_BIN} -m ruff check --no-cache --select E9 tests"]
fail_checks = ["{PYTHON_BIN} -m pytest -q"]
fail_exit_codes = [1, 2]

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

    # 2. Syntax error in red-test stage must be rejected by ruff / ERR_CHECK
    test_file.write_text("def test_broken(:\n", encoding="utf-8")
    assert main(["--config", str(cfg), "check", "--stage", "red-test"]) == 1
    assert "ruff check --no-cache --select E9 tests [ERR_CHECK]" in capsys.readouterr().out

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


@trace("REQ-STAGE")
def test_red_test_stage_accepts_collection_error_for_unimplemented_symbol(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = init_git_repo(tmp_path)
    (tmp_path / "tests" / "test_calc.py").write_text(
        "from src.calc import subtract\n\n"
        "def test_subtract() -> None:\n    assert subtract(5, 3) == 2\n",
        encoding="utf-8",
    )
    # pytest exits 2 (collection error); fail_exit_codes = [1, 2] accepts it as red
    assert main(["--config", str(cfg), "check", "--stage", "red-test"]) == 0
    assert capsys.readouterr().out == ""


@trace("REQ-STAGE")
def test_commit_msg_check_runs_workflow_checks_for_non_stage_commit_types(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = init_git_repo(tmp_path)
    msg_file = tmp_path / "COMMIT_EDITMSG"
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok() -> None:\n    assert True\n")
    git_run(tmp_path, "add", "-A")

    for subject, expected in [
        ("docs: describe usage", 0),
        ("Merge branch 'side'", 0),
        ("fix(core): not in commit_types", 1),
        ("plan: stage commit without target", 1),
    ]:
        msg_file.write_text(subject + "\n", encoding="utf-8")
        assert main(["--config", str(cfg), "check", "--commit-msg", str(msg_file)]) == expected
    assert "[ERR_COMMIT_MSG]" in capsys.readouterr().out

    (tmp_path / ".policy" / "workflow.toml").write_text(WORKFLOW_TOML + "\n")
    git_run(tmp_path, "add", "-A")
    msg_file.write_text("chore(policy): sneak policy change\n", encoding="utf-8")
    assert main(["--config", str(cfg), "check", "--commit-msg", str(msg_file)]) == 1
    assert "[ERR_PROTECTED]" in capsys.readouterr().out


def _commit_file(repo: Path, rel_path: str, text: str, subject: str) -> None:
    (repo / rel_path).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel_path).write_text(text, encoding="utf-8")
    git_run(repo, "add", "-A")
    git_run(repo, "commit", "-m", subject)


@trace("REQ-MERGE")
def test_merge_check_allows_non_stage_and_merge_commits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = init_git_repo(tmp_path)
    git_run(tmp_path, "checkout", "-b", "side")
    _commit_file(tmp_path, "NOTES.md", "notes\n", "docs: add notes")
    smoke = "def test_smoke() -> None:\n    assert True\n"
    _commit_file(tmp_path, "tests/test_smoke.py", smoke, "chore(tests): add smoke test")
    git_run(tmp_path, "checkout", "-b", "feat/req-1", "main")
    _commit_file(tmp_path, "trace/REQ-1.toml", 'status = "DRAFT"\n', "plan(REQ-1): plan")
    _commit_file(
        tmp_path,
        "tests/test_calc.py",
        "from src.calc import add\ndef test_add() -> None:\n    assert add(2, 3) == 5\n",
        "test(REQ-1): red test",
    )
    _commit_file(tmp_path, "README.md", "readme\n", "chore(readme): add readme")
    git_run(tmp_path, "merge", "--no-ff", "-m", "Merge branch 'side'", "side")
    impl = "def add(a: int, b: int) -> int:\n    return a + b\n"
    _commit_file(tmp_path, "src/calc.py", impl, "feat(REQ-1): implement add")
    _commit_file(tmp_path, "trace/REQ-1.toml", 'status = "APPROVED"\n', "review(REQ-1): ok")
    assert main(["--config", str(cfg), "check", "--base", "main"]) == 0

    # Branch with only non-stage commits needs no stage sequence
    git_run(tmp_path, "checkout", "side")
    assert main(["--config", str(cfg), "check", "--base", "main"]) == 0

    # Non-stage commits must not touch protected paths
    git_run(tmp_path, "checkout", "-b", "chore/policy", "main")
    _commit_file(tmp_path, ".policy/workflow.toml", WORKFLOW_TOML + "\n", "chore: edit policy")
    assert main(["--config", str(cfg), "check", "--base", "main"]) == 1
    assert "[ERR_PROTECTED]" in capsys.readouterr().out


@trace("REQ-POLICY")
def test_policy_commit_updates_protected_paths_with_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = init_git_repo(tmp_path)
    _commit_file(tmp_path, "tests/test_ok.py", "def test_ok() -> None:\n    pass\n", "chore: ok")
    git_run(tmp_path, "checkout", "-b", "policy/update")
    (tmp_path / ".policy" / "workflow.toml").write_text(WORKFLOW_TOML + "\n")
    git_run(tmp_path, "add", "-A")
    msg_file = tmp_path / ".git" / "COMMIT_EDITMSG"

    msg_file.write_text("policy(workflow): tweak\n", encoding="utf-8")
    assert main(["--config", str(cfg), "check", "--commit-msg", str(msg_file)]) == 1
    assert "[ERR_POLICY_REASON]" in capsys.readouterr().out

    message = "policy(workflow): tweak\n\nReason: keep config tidy\n# comment\n"
    msg_file.write_text(message, encoding="utf-8")
    assert main(["--config", str(cfg), "check", "--commit-msg", str(msg_file)]) == 0

    # Mixing non-protected paths into a policy commit is rejected
    (tmp_path / "src" / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return 1\n")
    git_run(tmp_path, "add", "-A")
    assert main(["--config", str(cfg), "check", "--commit-msg", str(msg_file)]) == 1
    assert "src/calc.py [ERR_SCOPE] (policy)" in capsys.readouterr().out

    git_run(tmp_path, "reset", "-q", "src/calc.py")
    git_run(tmp_path, "checkout", "--", "src/calc.py")
    git_run(tmp_path, "commit", "-q", "-F", str(msg_file))
    assert main(["--config", str(cfg), "check", "--base", "main"]) == 0
