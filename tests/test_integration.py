"""Integration tests for the merge check: protected paths, red check, and Reason exceptions."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from project_harness.cli import main

PYTHON_EXECUTABLE = sys.executable
TEST_COMMAND = f"{PYTHON_EXECUTABLE} -m pytest -q -p no:cacheprovider"

WORKFLOW_TOML = f"""[workflow]
protected_paths = [".policy/**", "ci/**"]
checks = ["{TEST_COMMAND}"]
source_paths = ["src/**"]
test_command = "{TEST_COMMAND}"
red_exit_codes = [1, 2]
"""

ADD_TEST = "from src.calc import add\n\n\ndef test_add() -> None:\n    assert add(2, 3) == 5\n"
ADD_IMPLEMENTATION = "def add(a: int, b: int) -> int:\n    return a + b\n"
SMOKE_REQUIREMENT = (
    'id = "REQ-1"\ntitle = "Smoke"\nacceptance = ["Pass smoke."]\n'
    'tests = ["tests/test_smoke.py::test_smoke"]\n'
)
EXPANDED_REQUIREMENT = (
    'id = "REQ-1"\ntitle = "Smoke"\nacceptance = ["Pass smoke.", "Add ints."]\n'
    'tests = ["tests/test_smoke.py::test_smoke", "tests/test_calc.py::test_add"]\n'
)


def _git_run(repository: Path, *args: str) -> None:
    """Execute a git command in repository, failing on non-zero exit."""
    subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True)


def _commit_file(repository: Path, relative_path: str, content: str, *message: str) -> None:
    """Write relative_path and commit it with the given message paragraphs."""
    target = repository / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git_run(repository, "add", "-A")
    _git_run(repository, "commit", *(part for paragraph in message for part in ("-m", paragraph)))


@pytest.fixture(autouse=True)
def _run_in_tmp_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run harness from the temporary repository, like a user in their project."""
    monkeypatch.chdir(tmp_path)


def _init_git_repository(repository: Path, branch: str) -> None:
    """Create a stable main with .policy/workflow.toml and switch to a new branch."""
    _git_run(repository, "init", "-b", "main")
    _git_run(repository, "config", "user.email", "test@example.com")
    _git_run(repository, "config", "user.name", "Test User")
    (repository / ".gitignore").write_text("__pycache__/\n*.py[cod]\n", encoding="utf-8")
    (repository / ".policy").mkdir(exist_ok=True)
    (repository / ".policy" / "workflow.toml").write_text(WORKFLOW_TOML, encoding="utf-8")
    (repository / "src").mkdir()
    (repository / "src" / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return 0\n")
    (repository / "tests").mkdir()
    (repository / "tests" / "test_smoke.py").write_text("def test_smoke() -> None:\n    pass\n")
    _git_run(repository, "add", "-A")
    _git_run(repository, "commit", "-m", "chore: baseline")
    _git_run(repository, "checkout", "-b", branch)


def _merge_check(branch: str) -> int:
    """Run the merge check of HEAD against main for branch."""
    return main(["check", "--base", "main", "--branch", branch])


def test_merge_check_accepts_test_first_source_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _init_git_repository(tmp_path, "feat/add")
    _commit_file(
        tmp_path, "tests/test_calc.py", f"import src.extra\n{ADD_TEST}", "test: add and extra"
    )
    _commit_file(tmp_path, "src/extra.py", "EXTRA = 1\n", "feat: extra module")
    _commit_file(tmp_path, "src/calc.py", ADD_IMPLEMENTATION, "feat: add")

    assert _merge_check("feat/add") == 0
    assert capsys.readouterr().out == ""
    assert (tmp_path / "src" / "calc.py").read_text(encoding="utf-8") == ADD_IMPLEMENTATION
    assert (tmp_path / "src" / "extra.py").read_text(encoding="utf-8") == "EXTRA = 1\n"


def test_merge_check_rejects_source_change_without_red_test(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _init_git_repository(tmp_path, "feat/add")
    _commit_file(tmp_path, "src/calc.py", ADD_IMPLEMENTATION, "feat: add without test")

    assert _merge_check("feat/add") == 1
    out = capsys.readouterr().out
    assert "[ERR_RED]: expected exit in [1, 2] against base source, got exit 0" in out
    assert "branch 'feat/add' [ERR_BRANCH]: exception requires a 'refactor/*' branch" in out
    assert (tmp_path / "src" / "calc.py").read_text(encoding="utf-8") == ADD_IMPLEMENTATION


def test_refactor_branch_needs_reason_in_source_commits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _init_git_repository(tmp_path, "refactor/calc")
    body = "def add(a: int, b: int) -> int:\n    result = 0\n    return result\n"
    _commit_file(tmp_path, "src/calc.py", body, "refactor: name result")

    assert _merge_check("refactor/calc") == 1
    assert "[ERR_REASON]: missing 'Reason: <why>' line" in capsys.readouterr().out

    _git_run(tmp_path, "commit", "--amend", "-m", "refactor: name result", "-m", "Reason: clarity")
    assert _merge_check("refactor/calc") == 0
    assert capsys.readouterr().out == ""


def test_merge_check_rejects_protected_change_using_base_rules(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _init_git_repository(tmp_path, "fix/ci")
    loosened = WORKFLOW_TOML.replace('[".policy/**", "ci/**"]', "[]")
    _commit_file(tmp_path, ".policy/workflow.toml", loosened, "fix: loosen")
    _commit_file(tmp_path, "ci/run.sh", "true\n", "fix: ci", "Reason: needed")

    assert _merge_check("fix/ci") == 1
    assert capsys.readouterr().out.splitlines() == [
        ".policy/workflow.toml [ERR_PROTECTED]: path is protected by policy "
        "(Fix: revert '.policy/workflow.toml' or use a 'policy/*' branch with 'Reason: <why>')",
        "ci/run.sh [ERR_PROTECTED]: path is protected by policy "
        "(Fix: revert 'ci/run.sh' or use a 'policy/*' branch with 'Reason: <why>')",
        "branch 'fix/ci' [ERR_BRANCH]: exception requires a 'policy/*' branch "
        "(Fix: switch to a 'policy/*' branch)",
    ]


def test_policy_branch_needs_reason_in_every_protected_commit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _init_git_repository(tmp_path, "policy/ci")
    _commit_file(tmp_path, "ci/run.sh", "true\n", "chore: add ci", "Reason: run checks in CI")
    _commit_file(tmp_path, "ci/run.sh", "true\ntrue\n", "chore: tweak ci")
    sha = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "--short", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert _merge_check("policy/ci") == 1
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "ci/run.sh [ERR_PROTECTED]: path is protected by policy "
        "(Fix: revert 'ci/run.sh' or use a 'policy/*' branch with 'Reason: <why>')",
        f"{sha} [ERR_REASON]: missing 'Reason: <why>' line "
        f"(Fix: add a 'Reason: <why>' line to commit {sha})",
    ]

    _git_run(tmp_path, "commit", "--amend", "-m", "chore: tweak ci", "-m", "Reason: second step")
    assert _merge_check("policy/ci") == 0
    assert capsys.readouterr().out == ""


def test_merge_check_sees_renamed_and_non_ascii_protected_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _init_git_repository(tmp_path, "fix/move")
    _git_run(tmp_path, "mv", ".policy/workflow.toml", "workflow.toml")
    _git_run(tmp_path, "commit", "-m", "fix: move")
    _commit_file(tmp_path, "ci/ä.sh", "true\n", "fix: unicode")

    assert _merge_check("fix/move") == 1
    out = capsys.readouterr().out
    assert ".policy/workflow.toml [ERR_PROTECTED]" in out
    assert "ci/ä.sh [ERR_PROTECTED]" in out


def test_merge_check_rejects_dirty_tree_and_reports_config_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _init_git_repository(tmp_path, "fix/x")
    (tmp_path / "README.md").write_text("dirty\n", encoding="utf-8")
    assert _merge_check("fix/x") == 1
    assert "[ERR_DIRTY]: uncommitted changes present" in capsys.readouterr().out

    (tmp_path / "README.md").unlink()
    assert main(["check", "--base", "missing-ref"]) == 2
    assert "[ERR_CONFIG]" in capsys.readouterr().err


def test_merge_check_runs_base_checks_after_rules_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _init_git_repository(tmp_path, "docs/readme")
    _commit_file(tmp_path, "src/calc.py", ADD_IMPLEMENTATION, "feat: untestable while head fails")
    _commit_file(tmp_path, "tests/test_fail.py", "def test_fail() -> None:\n    assert 0\n", "docs")

    assert _merge_check("docs/readme") == 1
    assert "[ERR_CHECK]: FAILED " in capsys.readouterr().out
    assert main(["check"]) == 1


def test_approved_policy_branch_is_judged_by_its_own_policies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    strict = '[[rules]]\nfiles = ["docs/*.md"]\nforbid = ["TODO"]\nfix = "Finish docs."\n'
    (tmp_path / ".policy").mkdir()
    (tmp_path / ".policy" / "architecture.toml").write_text(strict, encoding="utf-8")
    _init_git_repository(tmp_path, "policy/docs")
    _commit_file(tmp_path, "docs/plan.md", "TODO\n", "docs: plan")
    assert _merge_check("policy/docs") == 1
    assert "docs/plan.md:1:1 [ERR_ARCHITECTURE]" in capsys.readouterr().out

    relaxed = strict.replace('forbid = ["TODO"]', "max_lines = 10")
    _commit_file(
        tmp_path, ".policy/architecture.toml", relaxed, "policy: allow TODO", "Reason: drafts"
    )
    _commit_file(
        tmp_path,
        "src/calc.py",
        "def add(a: int, b: int) -> int:\n    result = 0\n    return result\n",
        "policy: align calc style",
        "Reason: match updated policy",
    )
    assert _merge_check("policy/docs") == 0
    assert capsys.readouterr().out == ""


def test_merge_check_allows_additive_requirements_and_protects_weakening(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".policy").mkdir()
    (tmp_path / ".policy" / "traceability.toml").write_text("[traceability]\n", encoding="utf-8")
    (tmp_path / "trace").mkdir()
    (tmp_path / "trace" / "REQ-1.toml").write_text(SMOKE_REQUIREMENT, encoding="utf-8")
    _init_git_repository(tmp_path, "feat/add")
    _commit_file(tmp_path, "tests/test_calc.py", ADD_TEST, "test: add")
    _commit_file(tmp_path, "src/calc.py", ADD_IMPLEMENTATION, "feat: add")
    _commit_file(tmp_path, "trace/REQ-1.toml", EXPANDED_REQUIREMENT, "trace: link test_add")
    assert _merge_check("feat/add") == 0
    assert capsys.readouterr().out == ""

    weakened = EXPANDED_REQUIREMENT.replace('"Smoke"', '"Changed"')
    _commit_file(tmp_path, "trace/REQ-1.toml", weakened, "trace: weaken title")
    assert _merge_check("feat/add") == 1
    assert "trace/REQ-1.toml [ERR_PROTECTED]" in capsys.readouterr().out


def test_parallel_worktrees_require_rebase_onto_updated_base(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".policy").mkdir()
    (tmp_path / ".policy" / "traceability.toml").write_text("[traceability]\n", encoding="utf-8")
    _init_git_repository(tmp_path, "feat/a")
    worktree_b = tmp_path.with_name(f"{tmp_path.name}_b")
    _git_run(tmp_path, "worktree", "add", "-b", "feat/b", str(worktree_b), "main")
    _commit_file(tmp_path, "trace/REQ-A.toml", SMOKE_REQUIREMENT.replace("1", "A"), "trace: A")
    assert _merge_check("feat/a") == 0
    _git_run(tmp_path, "checkout", "main")
    _git_run(tmp_path, "merge", "--ff-only", "feat/a")
    monkeypatch.chdir(worktree_b)
    _commit_file(worktree_b, "tests/test_calc.py", ADD_TEST, "test: add")
    _commit_file(worktree_b, "src/calc.py", ADD_IMPLEMENTATION, "feat: add")
    _commit_file(worktree_b, "trace/REQ-B.toml", EXPANDED_REQUIREMENT.replace("1", "B"), "trace: B")
    assert _merge_check("feat/b") == 1
    assert "trace/REQ-A.toml [ERR_PROTECTED]" in capsys.readouterr().out
    _git_run(worktree_b, "rebase", "main")
    assert _merge_check("feat/b") == 0


def test_check_runs_from_subdirectory_and_rejects_config_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_git_repository(tmp_path, "feat/x")
    monkeypatch.chdir(tmp_path / "src")
    assert main(["check"]) == 0
    with pytest.raises(SystemExit):
        main(["--config", ".policy/workflow.toml", "check"])


def test_policies_skip_git_ignored_files(tmp_path: Path) -> None:
    _init_git_repository(tmp_path, "feat/x")
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "gen.py").write_text("temp_value = 1\n", encoding="utf-8")
    architecture = '[[rules]]\nfiles = ["src/future/*.py"]\nmax_lines = 10\nfix = "slim"\n'
    (tmp_path / ".policy" / "architecture.toml").write_text(architecture, encoding="utf-8")
    symbols = '[settings]\nforbidden_subwords = ["temp"]\n'
    (tmp_path / ".policy" / "symbols.toml").write_text(symbols, encoding="utf-8")
    assert main(["check"]) == 0
