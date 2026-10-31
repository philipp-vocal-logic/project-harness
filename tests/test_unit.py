"""Atomic unit tests for project-harness glob matching, Reason lines, config, and commands."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from trace_check import trace

from project_harness.cli import main
from project_harness.config import load_workflow, parse_workflow
from project_harness.core import materialize_policy_files, run_commands
from project_harness.model import has_reason, matches_any, matches_glob, matching_paths


@trace("REQ-PROTECTED")
@pytest.mark.parametrize(
    ("rel_path", "pattern", "expected"),
    [
        ("src/app.py", "src/**/*.py", True),
        ("src/pkg/sub/app.py", "src/**/*.py", True),
        ("tests/test_app.py", "src/**/*.py", False),
        (".policy/workflow.toml", ".policy/**", True),
        (".policy", ".policy/**", False),
        ("trace/requirements/REQ-1.toml", "trace/**/*.toml", True),
        ("README.md", "README.md", True),
    ],
)
def test_matches_glob_and_matches_any(rel_path: str, pattern: str, expected: bool) -> None:
    assert matches_glob(rel_path, pattern) is expected
    assert matches_any(rel_path, [pattern]) is expected


@trace("REQ-PROTECTED")
def test_matching_paths_returns_sorted_unique_matches() -> None:
    paths = ["src/b.py", ".policy/a.toml", "src/a.py", "src/b.py", "README.md"]
    assert matching_paths(paths, ["src/**", ".policy/**"]) == [
        ".policy/a.toml",
        "src/a.py",
        "src/b.py",
    ]


@trace("REQ-PROTECTED", "REQ-RED")
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("chore: raise limit\n\nReason: core grew\n", True),
        ("Reason: first line counts too", True),
        ("chore: x\n\nReason:\n", False),
        ("chore: x\n\n  Reason: indented\n", False),
        ("chore: x\n", False),
    ],
)
def test_has_reason(message: str, expected: bool) -> None:
    assert has_reason(message) is expected


@trace("REQ-POLICY")
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


@trace("REQ-POLICY")
@pytest.mark.parametrize(
    ("toml_text", "expected_match"),
    [
        ("unknown = 1\n", "unknown keys in workflow.toml"),
        ('workflow = "bad"\n', "'workflow' must be a table"),
        ("[workflow]\nstages = []\n", "unknown keys in workflow"),
        ('[workflow]\nprotected_paths = ["../outside"]\n', "glob must be a relative path"),
        ('[workflow]\nchecks = [""]\n', "must be a list of non-empty strings"),
        ('[workflow]\nsource_paths = ["src/**"]\n', "test_command is required"),
        ("[workflow]\ntest_command = 1\n", "must be a string"),
        ("[workflow]\nred_exit_codes = [0]\n", "list of exit codes in 1..255"),
        ("[workflow\n", "Expected"),
    ],
)
def test_parse_workflow_rejects_invalid_configs(toml_text: str, expected_match: str) -> None:
    with pytest.raises(ValueError, match=expected_match):
        parse_workflow(toml_text)


@trace("REQ-POLICY")
def test_load_workflow_reads_file(tmp_path: Path) -> None:
    cfg = tmp_path / "workflow.toml"
    cfg.write_text('[workflow]\nchecks = ["true"]\n', encoding="utf-8")
    assert load_workflow(cfg).checks == ("true",)


@trace("REQ-POLICY")
def test_materialize_policy_files_copies_and_cleans_up(tmp_path: Path) -> None:
    policy_dir = tmp_path / ".policy"
    policy_dir.mkdir()
    (policy_dir / "workflow.toml").write_text("# workflow\n", encoding="utf-8")
    (policy_dir / "arch.toml").write_text("# arch\n", encoding="utf-8")
    (policy_dir / "glossary.toml").write_text("# glossary\n", encoding="utf-8")
    (tmp_path / "glossary.toml").write_text("# glossary\n", encoding="utf-8")

    with materialize_policy_files(tmp_path) as materialized:
        assert materialized == frozenset({"arch.toml", "glossary.toml"})
        assert (tmp_path / "arch.toml").is_file()
        assert not (tmp_path / "workflow.toml").exists()

    assert not (tmp_path / "arch.toml").exists()
    assert (tmp_path / "glossary.toml").read_text(encoding="utf-8") == "# glossary\n"

    # A differing root file would shadow the .policy file: refuse instead of using it
    (tmp_path / "glossary.toml").write_text("# stale root glossary\n", encoding="utf-8")
    with (
        pytest.raises(ValueError, match=r"glossary\.toml in root differs"),
        materialize_policy_files(tmp_path),
    ):
        pass
    assert not (tmp_path / "arch.toml").exists()


@trace("REQ-POLICY")
def test_cli_returns_exit_2_on_missing_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--config", str(tmp_path / "missing.toml"), "check"])
    assert code == 2
    assert "[ERR_CONFIG]" in capsys.readouterr().err


@trace("REQ-POLICY")
@pytest.mark.parametrize(
    ("stdout_text", "stderr_text", "expected_detail"),
    [
        pytest.param(
            "Compiling app v0.1.0\n",
            "error[E0425]: cannot find value `missing_fn` in this scope\n",
            "error[E0425]: cannot find value `missing_fn` in this scope",
            id="rust_cargo_compile_error_on_stderr",
        ),
        pytest.param(
            "running 1 test\ntest tests::multiply_works ... FAILED\n",
            "thread 'tests::multiply_works' panicked at 'assertion failed'\n",
            "test tests::multiply_works ... FAILED",
            id="rust_cargo_test_failure_line",
        ),
        pytest.param(
            "[==========] Running 1 test\n[  FAILED  ] CalcSuite.MultiplyCase (0 ms)\n",
            "",
            "[  FAILED  ] CalcSuite.MultiplyCase (0 ms)",
            id="cpp_gtest_failure_line",
        ),
        pytest.param(
            "Building C object\n",
            "tests/test_calc.c:12:5: error: implicit declaration of function 'multiply'\n",
            "tests/test_calc.c:12:5: error: implicit declaration of function 'multiply'",
            id="ansi_c_gcc_diagnostic_on_stderr",
        ),
        pytest.param(
            "",
            "test_runner: test_calc.c:8: Assertion `multiply(3, 4) == 12' failed.\n",
            "test_runner: test_calc.c:8: Assertion `multiply(3, 4) == 12' failed.",
            id="ansi_c_assert_failure_on_stderr",
        ),
        pytest.param("", "", "exit code 1", id="empty_stdout_and_stderr_fallback_edge"),
    ],
)
def test_run_commands_extracts_compiler_and_test_failure_details(
    tmp_path: Path, stdout_text: str, stderr_text: str, expected_detail: str
) -> None:
    script = tmp_path / "emit_diag.py"
    script.write_text(
        "import sys\n"
        f"sys.stdout.write({stdout_text!r})\n"
        f"sys.stderr.write({stderr_text!r})\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    cmd = f"{Path(sys.executable).as_posix()} {script.as_posix()}"
    violations = run_commands(tmp_path, [cmd])
    assert violations == [f"{cmd} [ERR_CHECK]: {expected_detail}"]
