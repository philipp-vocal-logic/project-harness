"""Atomic unit tests for project-harness models, glob matching, subjects, and config loading."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from trace_check import trace

from project_harness.cli import main
from project_harness.config import load_workflow
from project_harness.core import _fill, materialize_policy_files, run_commands
from project_harness.model import (
    CommitHeader,
    Stage,
    check_paths_scope,
    check_policy_commit,
    matches_any,
    matches_glob,
    parse_subject,
)


@trace("REQ-STAGE")
@pytest.mark.parametrize(
    ("rel_path", "pattern", "expected"),
    [
        ("src/app.py", "src/**/*.py", True),
        ("src/pkg/sub/app.py", "src/**/*.py", True),
        ("tests/test_app.py", "src/**/*.py", False),
        (".policy/workflow.toml", ".policy/**", True),
        ("trace/requirements/REQ-1.toml", "trace/**/*.toml", True),
        ("README.md", "README.md", True),
        ("src/app.py", "src/app.py", True),
    ],
)
def test_matches_glob_and_matches_any(rel_path: str, pattern: str, expected: bool) -> None:
    assert matches_glob(rel_path, pattern) is expected
    assert matches_any(rel_path, [pattern]) is expected


@trace("REQ-MERGE")
@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        (
            "plan(REQ-1): add requirement node",
            CommitHeader("plan", "REQ-1", "add requirement node"),
        ),
        (
            "test(US-2.1): add failing red test",
            CommitHeader("test", "US-2.1", "add failing red test"),
        ),
        ("feat(REQ-1): " + "x" * 60, None),
        ("invalid commit message", None),
        ("Plan(REQ-1): uppercase prefix not allowed", None),
        ("plan(): missing target", None),
        ("docs: no target", CommitHeader("docs", None, "no target")),
        ("feat(core)!: breaking change", CommitHeader("feat", "core", "breaking change")),
    ],
)
def test_parse_subject(subject: str, expected: CommitHeader | None) -> None:
    assert parse_subject(subject) == expected


@trace("REQ-STAGE")
def test_check_paths_scope_reports_protected_and_out_of_scope_files() -> None:
    stage = Stage(id="implementer", commit_prefix="feat", allow_write=("src/**/*.py",))
    protected = (".policy/**", "pyproject.toml")
    assert check_paths_scope(["src/core.py"], stage, protected) == []

    violations = check_paths_scope(
        ["src/core.py", "tests/test_core.py", ".policy/workflow.toml"],
        stage,
        protected,
        label="abc1234",
    )
    assert violations == [
        "abc1234: .policy/workflow.toml [ERR_PROTECTED]: path is protected by policy",
        "abc1234: tests/test_core.py [ERR_SCOPE] (implementer): "
        "path outside allow_write ['src/**/*.py']",
    ]


@trace("REQ-POLICY")
def test_check_policy_commit_requires_reason_and_protected_paths_only() -> None:
    protected = (".policy/**",)
    reason = "policy(arch): raise limit\n\nReason: core grew with merge gate\n"
    assert check_policy_commit([".policy/arch.toml"], reason, protected) == []
    assert check_policy_commit([".policy/arch.toml"], "policy(arch): x\n", protected) == [
        "working-tree [ERR_POLICY_REASON]: missing 'Reason: <why>' line"
    ]
    assert check_policy_commit(["src/a.py"], reason, protected, label="abc1234") == [
        "abc1234: src/a.py [ERR_SCOPE] (policy): path outside protected_paths"
    ]


@trace("REQ-POLICY")
@pytest.mark.parametrize(
    ("toml_text", "expected_match"),
    [
        ("unknown = 1\n", "unknown keys in workflow.toml"),
        ('workflow = "bad"\n', "'workflow' must be a table"),
        (
            '[workflow]\nprotected_paths = ["../outside"]\n'
            '[[stages]]\nid = "s"\ncommit_prefix = "p"\nallow_write = ["src/**"]\n',
            "glob must be a relative path pattern",
        ),
        ("[workflow]\n", "'stages' must be a non-empty list of tables"),
        (
            '[[stages]]\nid = "s"\ncommit_prefix = "p"\nallow_write = []\n',
            "must not be empty",
        ),
        (
            '[[stages]]\nid = "s"\ncommit_prefix = "p"\nallow_write = ["src/**"]\n'
            '[[stages]]\nid = "s"\ncommit_prefix = "q"\nallow_write = ["tests/**"]\n',
            "duplicate stage id 's'",
        ),
        (
            '[[stages]]\nid = "s1"\ncommit_prefix = "p"\nallow_write = ["src/**"]\n'
            '[[stages]]\nid = "s2"\ncommit_prefix = "p"\nallow_write = ["tests/**"]\n',
            "duplicate stage commit_prefix 'p'",
        ),
        (
            '[orchestrator]\nagent_command = ["echo"]\nmax_retries = 0\n'
            '[[stages]]\nid = "s"\ncommit_prefix = "p"\nallow_write = ["src/**"]\n',
            "max_retries must be a positive integer",
        ),
        (
            '[workflow]\ncommit_types = ["p"]\n'
            '[[stages]]\nid = "s"\ncommit_prefix = "p"\nallow_write = ["src/**"]\n',
            "'p' is a stage commit_prefix",
        ),
        (
            '[workflow]\ncommit_types = ["Fix"]\n'
            '[[stages]]\nid = "s"\ncommit_prefix = "p"\nallow_write = ["src/**"]\n',
            "invalid commit type 'Fix'",
        ),
        (
            '[[stages]]\nid = "s"\ncommit_prefix = "p"\nallow_write = ["src/**"]\n'
            "fail_exit_codes = [0]\n",
            "must be a non-empty list of exit codes",
        ),
        (
            '[workflow]\npolicy_commit_type = "p"\n'
            '[[stages]]\nid = "s"\ncommit_prefix = "p"\nallow_write = ["src/**"]\n',
            "policy_commit_type: invalid or duplicate 'p'",
        ),
    ],
)
def test_load_workflow_rejects_invalid_configs(
    tmp_path: Path, toml_text: str, expected_match: str
) -> None:
    cfg = tmp_path / "workflow.toml"
    cfg.write_text(toml_text, encoding="utf-8")
    with pytest.raises(ValueError, match=expected_match):
        load_workflow(cfg)


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


@trace("REQ-ORCHESTRATOR")
def test_fill_replaces_known_placeholders_and_keeps_other_braces() -> None:
    template = 'Do {target} in {stage}; emit {"k": 1} and {unknown}'
    assert _fill(template, target="REQ-{stage}", stage="s") == (
        'Do REQ-{stage} in s; emit {"k": 1} and {unknown}'
    )


@trace("REQ-POLICY")
def test_cli_returns_exit_2_on_missing_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--config", str(tmp_path / "missing.toml"), "check"])
    assert code == 2
    assert "[ERR_CONFIG]" in capsys.readouterr().err


@trace("REQ-STAGE")
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
        pytest.param(
            "",
            "",
            "exit code 1",
            id="empty_stdout_and_stderr_fallback_edge",
        ),
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
