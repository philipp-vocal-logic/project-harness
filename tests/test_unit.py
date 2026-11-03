"""Atomic unit tests for project-harness globs, morphology, configs, checkers, and commands."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

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
    split_subwords,
    subword_forms,
)
from project_harness.symbol import check_symbol_text
from project_harness.traceability import check_trace


def git_init(repo: Path) -> None:
    """Make repo a git work tree so harness can list its files."""
    subprocess.run(["git", "init", "-q", str(repo)], check=True)


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


def test_matching_paths_returns_sorted_unique_matches() -> None:
    paths = ["src/b.py", ".policy/a.toml", "src/a.py", "src/b.py", "README.md"]
    assert matching_paths(paths, ["src/**", ".policy/**"]) == [
        ".policy/a.toml",
        "src/a.py",
        "src/b.py",
    ]


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


def test_split_subwords_and_subword_forms_handle_camel_case_acronyms_and_plurals() -> None:
    assert split_subwords("HTTPResponses_and_URLs") == ("http", "responses", "and", "urls")
    assert subword_forms("policy") == frozenset({"policy", "policys", "policies"})
    assert subword_forms("branch") == frozenset({"branch", "branchs", "branches"})


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


def test_load_workflow_reads_file(tmp_path: Path) -> None:
    config_path = tmp_path / "workflow.toml"
    config_path.write_text('[workflow]\nchecks = ["true"]\n', encoding="utf-8")
    assert load_workflow(config_path).checks == ("true",)


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
    assert check_glossary_text("doc.md", "our times_ops here\n", glossary) != []
    assert check_glossary_text("doc.md", "our multiply here\n", glossary) == []

    symbols = parse_symbols(
        '[settings]\nforbidden_subwords = ["temp"]\n[replacements]\nload = ["fetch"]\n'
    )
    code = (
        "def fetch_temps[temp_type](x: dict[str, int]) -> int:\n"
        "    match x:\n"
        "        case {**temp_rest}:\n"
        "            return 1\n"
    )
    symbol_hits = check_symbol_text("src/app.py", code, symbols)
    assert len(symbol_hits) == 4 and all("[ERR_SYMBOL]" in hit for hit in symbol_hits)

    git_init(tmp_path)
    (tmp_path / "stories").mkdir()
    (tmp_path / "reqs").mkdir()
    (tmp_path / "stories" / "US-1.toml").write_text('status = "APPROVED"\n', encoding="utf-8")
    (tmp_path / "reqs" / "REQ-1.toml").write_text(
        'status = "APPROVED"\nlinks = ["MISSING"]\n', encoding="utf-8"
    )
    trace_config = parse_traceability(
        '[schema]\nchain = ["Story", "Requirement"]\n'
        '[statuses]\nStory = ["APPROVED"]\nRequirement = ["APPROVED"]\n'
        '[locations]\nStory = "stories/*.toml"\nRequirement = "reqs/*.toml"\n'
    )
    trace_hits = check_trace(tmp_path, trace_config)
    assert any("[ERR_DANGLING_LINK]" in hit for hit in trace_hits)


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
    command = f"{Path(sys.executable).as_posix()} {script.as_posix()} --config .policy/arch.toml"
    assert run_commands(tmp_path, [command]) == []


def test_cli_returns_exit_2_on_missing_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    git_init(tmp_path)
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
        pytest.param("Would reformat: src/a.py\n", "", "Would reformat: src/a.py", id="first_line"),
        pytest.param("", "", "exit code 1", id="empty_stdout_and_stderr_fallback_edge"),
    ],
)
def test_run_commands_parses_failure_details(
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
    command = f"{Path(sys.executable).as_posix()} {script.as_posix()}"
    violations = run_commands(tmp_path, [command])
    assert violations == [f"{command} [ERR_CHECK]: {expected_detail}"]


TEST_LINK_TRACE_TOML = (
    '[schema]\nchain = ["Story", "Requirement"]\n'
    '[statuses]\nStory = ["APPROVED"]\nRequirement = ["APPROVED"]\n'
    '[locations]\nStory = "stories/*.toml"\nRequirement = "reqs/*.toml"\n'
    '[[rules]]\nid = "REQ-NEEDS-TEST"\nsource_type = "Requirement"\n'
    'when_status = "APPROVED"\ntarget_type = "Test"\n'
)


def test_trace_checks_requirement_side_test_links(tmp_path: Path) -> None:
    git_init(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text(
        "class TestCalc:\n    def test_add(self) -> None:\n        pass\n", encoding="utf-8"
    )
    (tmp_path / "stories").mkdir()
    (tmp_path / "stories" / "US-1.toml").write_text('status = "APPROVED"\n', encoding="utf-8")
    (tmp_path / "reqs").mkdir()
    requirements = {
        "REQ-1": '["tests/test_calc.py::TestCalc::test_add"]',
        "REQ-2": '["tests/test_calc.py::test_sub", "tests/missing.py::test_add"]',
        "REQ-3": "[]",
    }
    for requirement_id, tests in requirements.items():
        (tmp_path / "reqs" / f"{requirement_id}.toml").write_text(
            f'status = "APPROVED"\ntests = {tests}\n', encoding="utf-8"
        )
    trace_config = parse_traceability(TEST_LINK_TRACE_TOML)
    assert check_trace(tmp_path, trace_config) == [
        "reqs/REQ-2.toml: REQ-2 [ERR_DANGLING_TEST]: "
        "test 'tests/test_calc.py::test_sub' does not exist",
        "reqs/REQ-2.toml: REQ-2 [ERR_DANGLING_TEST]: "
        "test 'tests/missing.py::test_add' does not exist",
        "reqs/REQ-3.toml: REQ-3 [ERR_MISSING_LINK] (REQ-NEEDS-TEST): requires >= 1 test",
    ]

    (tmp_path / "reqs" / "REQ-3.toml").write_text(
        'status = "APPROVED"\ntests = ["tests/test_calc.py"]\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="relative path"):
        check_trace(tmp_path, trace_config)


@pytest.mark.parametrize(
    ("rule_tail", "expected_match"),
    [
        ('target_type = "Test"\ntarget_status = ["APPROVED"]\n', "target_status is not allowed"),
        ('target_type = "Tests"\n', "must be chain types"),
    ],
)
def test_parse_traceability_rejects_invalid_test_rules(rule_tail: str, expected_match: str) -> None:
    text = TEST_LINK_TRACE_TOML.replace('target_type = "Test"\n', rule_tail)
    with pytest.raises(ValueError, match=expected_match):
        parse_traceability(text)


def test_symbol_checker_rejects_non_python_files_and_multi_word_rules() -> None:
    with pytest.raises(ValueError, match=r"only \.py files"):
        check_symbol_text("src/calc.rs", "fn main() {}\n", parse_symbols(""))
    with pytest.raises(ValueError, match="single subwords"):
        parse_symbols('[settings]\nforbidden_subwords = ["temp_val"]\n')
    with pytest.raises(ValueError, match="single subwords"):
        parse_symbols('[replacements]\nload = ["read_config"]\n')


def test_parse_glossary_rejects_unused_similarity_threshold() -> None:
    with pytest.raises(ValueError, match=r"unknown keys in glossary\.toml"):
        parse_glossary("[settings]\nsimilarity_threshold = 0.65\n")


def test_parse_arch_rejects_rule_without_max_lines_or_forbid() -> None:
    with pytest.raises(ValueError, match="needs max_lines or forbid"):
        parse_arch('[[rules]]\nfiles = ["src/*.py"]\nfix = "slim"\n')
