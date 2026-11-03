"""Multi-language tests: ANSI C (C89) merge check and C++/Rust policy checker integration."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from trace_check import trace

from project_harness.cli import main

PYTHON_BIN = sys.executable

C89_TEST = (
    "#include <assert.h>\n"
    '#include "calc.h"\n\n'
    '/* @trace("REQ-C89") */\n'
    "void test_multiply(void) {\n"
    "    assert(multiply(3, 4) == 12);\n"
    "}\n\n"
    "int main(void) {\n"
    "    test_multiply();\n"
    "    return 0;\n"
    "}\n"
)
C89_IMPL = (
    '#include "calc.h"\nint multiply(int left_op, int right_op) { return left_op * right_op; }\n'
)

TRACEABILITY_TOML = """[schema]
chain = ["Story", "Requirement", "TestCase"]

[statuses]
Story = ["DRAFT", "APPROVED", "DEPRECATED"]
Requirement = ["DRAFT", "APPROVED", "DEPRECATED"]
TestCase = ["DRAFT", "APPROVED", "DEPRECATED"]

[locations]
Story = "trace/stories/*.toml"
Requirement = "trace/requirements/*.toml"
TestCase = "tests/test_*.c"

[[rules]]
id = "APPROVED-REQ-NEEDS-TEST"
source_type = "Requirement"
when_status = "APPROVED"
target_type = "TestCase"
target_status = ["APPROVED"]

[[rules]]
id = "APPROVED-TEST-NEEDS-REQ"
source_type = "TestCase"
when_status = "APPROVED"
target_type = "Requirement"
target_status = ["DRAFT", "APPROVED"]
"""

ARCH_TOML = """[[rules]]
files = ["src/**/*.c"]
max_lines = 100
fix = "Keep C modules concise."
"""

GLOSSARY_TOML = """[settings]
paths = ["include", "src", "tests", "trace"]
exclude = ["glossary.toml", "symbols.toml"]

[[terms]]
term = "multiply"
definition = "Compute the arithmetic product of two integers."
forbidden_terms = ["times_op"]
"""

SYMBOLS_TOML = """[settings]
include = ["include/**/*.h", "src/**/*.c", "tests/**/*.c"]
forbidden_subwords = ["data", "temp", "mgr"]
"""


def _build_c89_workflow_toml(c_runner: Path) -> str:
    checks = (
        f'"{PYTHON_BIN} -m glossary_checker_cli.cli --config .policy/glossary.toml", '
        f'"{PYTHON_BIN} -m symbol_checker.cli --config .policy/symbols.toml", '
        f'"{PYTHON_BIN} -m archcheck --config .policy/arch.toml", '
        f'"{PYTHON_BIN} -m trace_check.cli --config .policy/traceability.toml"'
    )
    run_cmd = f"{PYTHON_BIN} {c_runner.as_posix()}"
    return f"""[workflow]
protected_paths = [".policy/**"]
checks = [{checks}, "{run_cmd}"]
source_paths = ["src/**/*.c"]
test_command = "{run_cmd}"
red_exit_codes = [1]
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _setup_c89_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "c89_repo"
    repo.mkdir()
    c_runner = tmp_path / "run_c89.py"
    c_runner.write_text(
        "import subprocess, sys\n"
        "from pathlib import Path\n"
        "Path('build').mkdir(exist_ok=True)\n"
        "cmd = ['cc', '-std=c89', '-pedantic-errors', '-Wall', '-Wextra', '-Werror',\n"
        "       '-Iinclude', 'src/calc.c', 'tests/test_calc.c', '-o', 'build/test_calc']\n"
        "res = subprocess.run(cmd)\n"
        "if res.returncode != 0:\n"
        "    sys.exit(res.returncode)\n"
        "run = subprocess.run(['./build/test_calc'])\n"
        "sys.exit(1 if run.returncode != 0 else 0)\n",
        encoding="utf-8",
    )

    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "a@b.c")
    _git(repo, "config", "user.name", "Agent")
    (repo / ".gitignore").write_text("build/\n*.o\n", encoding="utf-8")

    policy = repo / ".policy"
    policy.mkdir()
    cfg = policy / "workflow.toml"
    cfg.write_text(_build_c89_workflow_toml(c_runner), encoding="utf-8")
    (policy / "traceability.toml").write_text(TRACEABILITY_TOML, encoding="utf-8")
    (policy / "arch.toml").write_text(ARCH_TOML, encoding="utf-8")
    (policy / "glossary.toml").write_text(GLOSSARY_TOML, encoding="utf-8")
    (policy / "symbols.toml").write_text(SYMBOLS_TOML, encoding="utf-8")

    for d in ("include", "src", "tests", "trace/stories", "trace/requirements"):
        (repo / d).mkdir(parents=True, exist_ok=True)
    (repo / "include" / "calc.h").write_text("int multiply(int left_op, int right_op);\n")
    (repo / "src" / "calc.c").write_text(
        '#include "calc.h"\n'
        "int multiply(int left_op, int right_op) { (void)left_op; (void)right_op; return 0; }\n"
    )
    (repo / "trace" / "stories" / "US-1.toml").write_text('status = "APPROVED"\n')
    (repo / "trace" / "requirements" / "REQ-C89.toml").write_text(
        'status = "APPROVED"\nlinks = ["US-1"]\n'
    )

    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore(init): seed")
    _git(repo, "checkout", "-b", "feat/req-c89")
    return cfg


@trace("REQ-RED", "REQ-PROTECTED")
def test_ansi_c89_merge_check_requires_red_test_and_clean_policy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _setup_c89_repo(tmp_path)
    repo = cfg.parent.parent
    merge_check = ["--config", str(cfg), "check", "--base", "main", "--branch", "feat/req-c89"]

    # Failure case: implementation without a test that fails against the base source
    (repo / "src" / "calc.c").write_text(C89_IMPL, encoding="utf-8")
    (repo / "tests" / "test_calc.c").write_text(
        '#include "calc.h"\nint main(void) { return multiply(0, 0); }\n', encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feat: multiply without red test")
    assert main(merge_check) == 1
    assert "[ERR_RED]" in capsys.readouterr().out

    # Good case: test-first change passes the red check, policy tools, and the C runner
    _git(repo, "reset", "--hard", "-q", "main")
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_calc.c").write_text(C89_TEST, encoding="utf-8")
    (repo / "src" / "calc.c").write_text(C89_IMPL, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feat: multiply")
    assert main(merge_check) == 0
    assert capsys.readouterr().out == ""

    # Failure case: forbidden symbol subword fails the base policy checks
    (repo / "tests" / "test_calc.c").write_text(
        C89_TEST.replace("test_multiply", "temp_multiply"), encoding="utf-8"
    )
    _git(repo, "commit", "-qam", "test: rename")
    assert main(merge_check) == 1
    out = capsys.readouterr().out
    assert "symbol_checker.cli" in out and "[ERR_CHECK]" in out


@trace("REQ-POLICY")
@pytest.mark.parametrize(
    ("lang", "test_rel", "test_code", "src_rel", "src_code"),
    [
        pytest.param(
            "cpp",
            "tests/test_calc.cpp",
            '// @trace("REQ-1")\nTEST(CalcSuite, AddCase) {}\n',
            "src/calc.cpp",
            "namespace math_domain {\nint compute_sum(int left_op, int right_op) {\n"
            "    return left_op + right_op;\n}\n}\n",
            id="cpp_integration_good_case",
        ),
        pytest.param(
            "rust",
            "tests/test_calc.rs",
            '// @trace("REQ-1")\n#[test]\nfn add_works() {}\n',
            "src/calc.rs",
            "pub fn compute_sum(left_op: i32, right_op: i32) -> i32 {\n    left_op + right_op\n}\n",
            id="rust_integration_good_case",
        ),
    ],
)
def test_cpp_and_rust_policy_checkers_integration(
    tmp_path: Path, lang: str, test_rel: str, test_code: str, src_rel: str, src_code: str
) -> None:
    ext = ".cpp" if lang == "cpp" else ".rs"
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "trace" / "stories").mkdir(parents=True)
    (tmp_path / "trace" / "requirements").mkdir(parents=True)
    (tmp_path / "trace" / "stories" / "US-1.toml").write_text('status = "APPROVED"\n')
    (tmp_path / "trace" / "requirements" / "REQ-1.toml").write_text(
        'status = "APPROVED"\nlinks = ["US-1"]\n'
    )
    (tmp_path / test_rel).write_text(test_code, encoding="utf-8")
    (tmp_path / src_rel).write_text(src_code, encoding="utf-8")

    (tmp_path / "traceability.toml").write_text(
        TRACEABILITY_TOML.replace("tests/test_*.c", f"tests/test_*{ext}"), encoding="utf-8"
    )
    (tmp_path / "symbols.toml").write_text(
        f'[settings]\ninclude = ["src/**/*{ext}", "tests/**/*{ext}"]\n'
        'forbidden_subwords = ["data", "temp"]\n',
        encoding="utf-8",
    )
    (tmp_path / "arch.toml").write_text(
        ARCH_TOML.replace("src/**/*.c", f"src/**/*{ext}"), encoding="utf-8"
    )
    for cmd in [
        [PYTHON_BIN, "-m", "trace_check.cli", "--config", str(tmp_path / "traceability.toml")],
        [PYTHON_BIN, "-m", "symbol_checker.cli", "--config", str(tmp_path / "symbols.toml")],
        [PYTHON_BIN, "-m", "archcheck", "--config", str(tmp_path / "arch.toml")],
    ]:
        assert subprocess.run(cmd, cwd=tmp_path, check=False).returncode == 0
