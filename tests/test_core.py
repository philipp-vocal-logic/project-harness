"""Unit tests for command execution, failure detail extraction, and CLI error handling."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from project_harness.cli import main
from project_harness.core import run_commands


def _git_init(repository: Path) -> None:
    """Make repository a git work tree so harness can list its files."""
    subprocess.run(["git", "init", "-q", str(repository)], check=True)


def test_run_commands_passes_dot_policy_args_without_copying_to_root(tmp_path: Path) -> None:
    policy_directory = tmp_path / ".policy"
    policy_directory.mkdir()
    (policy_directory / "architecture.toml").write_text("# architecture\n", encoding="utf-8")
    script = tmp_path / "verify_args.py"
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "assert sys.argv[1:] == ['--config', '.policy/architecture.toml']\n"
        "assert not Path('architecture.toml').exists()\n",
        encoding="utf-8",
    )
    command = (
        f"{Path(sys.executable).as_posix()} {script.as_posix()} --config .policy/architecture.toml"
    )
    assert run_commands(tmp_path, [command]) == []


def test_cli_returns_exit_2_on_missing_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _git_init(tmp_path)
    monkeypatch.chdir(tmp_path)
    code = main(["check"])
    assert code == 2
    assert "[ERR_CONFIG]" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("stdout_text", "stderr_text", "expected_detail"),
    [
        pytest.param(
            "collected 2 items\n\nFAILED tests/test_calc.py::test_add - assert 0 == 5\n",
            "",
            "FAILED tests/test_calc.py::test_add - assert 0 == 5",
            id="pytest_failed_line",
        ),
        pytest.param(
            "",
            "ERROR tests/test_calc.py - ImportError: cannot import name 'add'\n",
            "ERROR tests/test_calc.py - ImportError: cannot import name 'add'",
            id="pytest_collection_error_line",
        ),
        pytest.param(
            "F401 [*] `os` imported but unused\n  --> src/a.py:3:8\n",
            "",
            "src/a.py:3:8: F401 [*] `os` imported but unused",
            id="arrow_location_line",
        ),
        pytest.param(
            "...                                                                      [100%]\n"
            "=== 3 passed in 0.01s ===\n",
            "",
            "3 passed in 0.01s",
            id="pytest_summary_line",
        ),
        pytest.param("Would reformat: src/a.py\n", "", "Would reformat: src/a.py", id="first_line"),
        pytest.param("", "", "exit code 1", id="empty_stdout_and_stderr_fallback_edge"),
    ],
)
def test_run_commands_parses_failure_details(
    tmp_path: Path, stdout_text: str, stderr_text: str, expected_detail: str
) -> None:
    script = tmp_path / "emit_diagnostic.py"
    script.write_text(
        "import sys\n"
        f"sys.stdout.write({stdout_text!r})\n"
        f"sys.stderr.write({stderr_text!r})\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    command = f"{Path(sys.executable).as_posix()} {script.as_posix()}"
    violations = run_commands(tmp_path, [command])
    assert violations == [f"{command} [ERR_CHECK]: {expected_detail}"]
