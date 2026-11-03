"""Atomic unit tests for project-harness globs, morphology, configs, checkers, and commands."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from project_harness import trace
from project_harness.arch import check_arch_text
from project_harness.cli import main
from project_harness.config import (
    load_workflow,
    parse_arch,
    parse_glossary,
    parse_symbols,
    parse_traceability,
    parse_workflow,
)
from project_harness.core import run_commands
from project_harness.glossary import check_glossary_text
from project_harness.model import (
    has_reason,
    matches_any,
    matches_glob,
    matching_paths,
    normalize_subwords,
)
from project_harness.symbol import check_symbol_text
from project_harness.traceability import check_trace


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
def test_normalize_subwords_handles_camel_case_acronyms_and_plurals() -> None:
    assert normalize_subwords("HTTPResponses_and_matrices") == (
        "http",
        "response",
        "and",
        "matrix",
    )


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
def test_builtin_checkers_detect_arch_glossary_symbol_and_trace_violations(
    tmp_path: Path,
) -> None:
    arch_rules = parse_arch(
        '[[rules]]\nfiles = ["src/*.py"]\nmax_lines = 1\nforbid = ["print"]\nfix = "slim"\n'
    )
    arch_hits = check_arch_text("src/a.py", "x = 1\nprint(x)\n", arch_rules[0])
    assert len(arch_hits) == 2 and all("[ERR_ARCH]" in hit for hit in arch_hits)

    glossary = parse_glossary(
        '[[terms]]\nterm = "multiply"\ndefinition = "product"\nforbidden_terms = ["times_op"]\n'
    )
    assert check_glossary_text("doc.md", "our times_op here\n", glossary) != []
    assert check_glossary_text("doc.md", "times_op # glossary: ignore[times_op]\n", glossary) == []

    symbols = parse_symbols(
        '[settings]\nforbidden_subwords = ["temp"]\n[replacements]\nload = ["fetch"]\n'
    )
    sym_hits = check_symbol_text("src/app.py", "def fetch_temp():\n    return 1\n", symbols)
    assert len(sym_hits) == 2 and all("[ERR_SYMBOL]" in hit for hit in sym_hits)

    (tmp_path / "stories").mkdir()
    (tmp_path / "reqs").mkdir()
    (tmp_path / "stories" / "US-1.toml").write_text('status = "APPROVED"\n', encoding="utf-8")
    (tmp_path / "reqs" / "REQ-1.toml").write_text(
        'status = "APPROVED"\nlinks = ["MISSING"]\n', encoding="utf-8"
    )
    trace_cfg = parse_traceability(
        '[schema]\nchain = ["Story", "Requirement"]\n'
        '[statuses]\nStory = ["APPROVED"]\nRequirement = ["APPROVED"]\n'
        '[locations]\nStory = "stories/*.toml"\nRequirement = "reqs/*.toml"\n'
    )
    trace_hits = check_trace(tmp_path, trace_cfg)
    assert any("[ERR_DANGLING_LINK]" in hit for hit in trace_hits)


@trace("REQ-POLICY")
def test_run_commands_passes_dot_policy_args_without_copying_to_root(tmp_path: Path) -> None:
    policy_dir = tmp_path / ".policy"
    policy_dir.mkdir()
    (policy_dir / "arch.toml").write_text("# arch\n", encoding="utf-8")
    script = tmp_path / "verify_args.py"
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "assert sys.argv[1:] == ['--config', '.policy/arch.toml']\n"
        "assert not Path('arch.toml').exists()\n",
        encoding="utf-8",
    )
    cmd = f"{Path(sys.executable).as_posix()} {script.as_posix()} --config .policy/arch.toml"
    assert run_commands(tmp_path, [cmd]) == []


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
