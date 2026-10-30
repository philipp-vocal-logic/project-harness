"""Multi-language E2E and integration tests for ANSI C (C89), C++, and Rust TDD workflows."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from trace_check import trace

from project_harness.cli import main

PYTHON_BIN = sys.executable

C89_AGENT_SCRIPT = """from pathlib import Path
import sys

stage, target, prompt = sys.argv[1], sys.argv[2], sys.argv[3]
root = Path.cwd()

if stage == "planner":
    req = root / "trace" / "requirements" / f"{target}.toml"
    req.write_text('status = "DRAFT"\\nlinks = ["US-1"]\\n', encoding="utf-8")
elif stage == "red-test":
    test_file = root / "tests" / "test_calc.c"
    test_file.write_text(
        "#include <assert.h>\\n"
        '#include "calc.h"\\n\\n'
        f'/* @trace("{target}") */\\n'
        "void test_multiply(void) {\\n"
        "    assert(multiply(3, 4) == 12);\\n"
        "}\\n\\n"
        "int main(void) {\\n"
        "    test_multiply();\\n"
        "    return 0;\\n"
        "}\\n",
        encoding="utf-8",
    )
elif stage == "implementer":
    calc = root / "src" / "calc.c"
    if "Violations:" not in prompt:
        calc.write_text(
            '#include "calc.h"\\nint multiply(int left_op, int right_op) { return -1; }\\n',
            encoding="utf-8",
        )
    else:
        calc.write_text(
            '#include "calc.h"\\n'
            "int multiply(int left_op, int right_op) { return left_op * right_op; }\\n",
            encoding="utf-8",
        )
elif stage == "reviewer":
    req = root / "trace" / "requirements" / f"{target}.toml"
    req.write_text('status = "APPROVED"\\nlinks = ["US-1"]\\n', encoding="utf-8")
"""

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


def _build_c89_workflow_toml(agent_script: Path, c_runner: Path) -> str:
    checks = (
        f'"{PYTHON_BIN} -m glossary_checker_cli.cli --config .policy/glossary.toml", '
        f'"{PYTHON_BIN} -m symbol_checker.cli --config .policy/symbols.toml", '
        f'"{PYTHON_BIN} -m archcheck --config .policy/arch.toml", '
        f'"{PYTHON_BIN} -m trace_check.cli .policy/traceability.toml"'
    )
    syntax_check = (
        '"cc -std=c89 -pedantic-errors -Wall -Wextra -Werror '
        '-Iinclude -fsyntax-only tests/test_calc.c"'
    )
    run_cmd = f'"{PYTHON_BIN} {c_runner.as_posix()}"'
    agent_cmd = (
        f'["{PYTHON_BIN}", "{agent_script.as_posix()}", "{{stage}}", "{{target}}", "{{prompt}}"]'
    )
    return f"""[workflow]
protected_paths = [".policy/**"]
checks = [{checks}, {run_cmd}]

[orchestrator]
max_retries = 3
agent_command = {agent_cmd}

[[stages]]
id = "planner"
commit_prefix = "plan"
allow_write = ["trace/**/*.toml"]
checks = [{checks}]
prompt = "Plan {{target}}"

[[stages]]
id = "red-test"
commit_prefix = "test"
allow_write = ["tests/**/*.c"]
checks = [{checks}, {syntax_check}]
fail_checks = [{run_cmd}]
fail_exit_codes = [1, 6, 134]
prompt = "Red test {{target}}"

[[stages]]
id = "implementer"
commit_prefix = "feat"
allow_write = ["src/**/*.c"]
checks = [{checks}, {run_cmd}]
prompt = "Implement {{target}}"

[[stages]]
id = "reviewer"
commit_prefix = "review"
allow_write = ["trace/**/*.toml"]
checks = [{checks}, {run_cmd}]
prompt = "Review {{target}}"
"""


def _setup_c89_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "c89_repo"
    repo.mkdir()
    agent_script = tmp_path / "c89_agent.py"
    agent_script.write_text(C89_AGENT_SCRIPT, encoding="utf-8")
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

    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "a@b.c"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Agent"], check=True)
    (repo / ".gitignore").write_text("build/\n*.o\n", encoding="utf-8")

    policy = repo / ".policy"
    policy.mkdir()
    cfg = policy / "workflow.toml"
    cfg.write_text(_build_c89_workflow_toml(agent_script, c_runner), encoding="utf-8")
    (policy / "traceability.toml").write_text(TRACEABILITY_TOML, encoding="utf-8")
    (policy / "arch.toml").write_text(ARCH_TOML, encoding="utf-8")
    (policy / "glossary.toml").write_text(GLOSSARY_TOML, encoding="utf-8")
    (policy / "symbols.toml").write_text(SYMBOLS_TOML, encoding="utf-8")

    for d in ("include", "src", "tests", "trace/stories", "trace/requirements"):
        (repo / d).mkdir(parents=True, exist_ok=True)
    (repo / "include" / "calc.h").write_text("int multiply(int left_op, int right_op);\n")
    (repo / "src" / "calc.c").write_text(
        '#include "calc.h"\nint multiply(int left_op, int right_op) { return 0; }\n'
    )
    (repo / "trace" / "stories" / "US-1.toml").write_text('status = "APPROVED"\n')

    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "chore(init): seed"], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "feat/req-c89"], check=True)
    return repo, cfg


@trace("REQ-ORCHESTRATOR", "REQ-MERGE", "REQ-STAGE")
def test_e2e_ansi_c89_orchestrator_tdd_workflow_and_policy_failure_cases(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, cfg = _setup_c89_repo(tmp_path)

    # Failure case 1: C++ // comment in ANSI C89 mode or forbidden symbol subword fails red-test
    (repo / "tests" / "test_calc.c").write_text(
        '#include "calc.h"\n'
        '/* @trace("US-1") */\n'
        "void test_multiply(void) {\n"
        "    int bad_temp = multiply(2, 2);\n"
        "    (void)bad_temp;\n"
        "}\n"
        "int main(void) { return 0; }\n",
        encoding="utf-8",
    )
    assert main(["--config", str(cfg), "check", "--stage", "red-test"]) == 1
    out = capsys.readouterr().out
    assert "symbol_checker.cli" in out and "[ERR_CHECK]" in out
    (repo / "tests" / "test_calc.c").unlink()

    # Good case + retry case: Execute full 4-stage ANSI C89 TDD workflow via orchestrator
    assert main(["--config", str(cfg), "run", "REQ-C89"]) == 0
    assert capsys.readouterr().out == ""
    assert main(["--config", str(cfg), "check", "--base", "main"]) == 0
    assert main(["--config", str(cfg), "check"]) == 0
    assert capsys.readouterr().out == ""


@trace("REQ-STAGE")
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
        [PYTHON_BIN, "-m", "trace_check.cli", str(tmp_path / "traceability.toml")],
        [PYTHON_BIN, "-m", "symbol_checker.cli", "--config", str(tmp_path / "symbols.toml")],
        [PYTHON_BIN, "-m", "archcheck", "--config", str(tmp_path / "arch.toml")],
    ]:
        assert subprocess.run(cmd, cwd=tmp_path, check=False).returncode == 0
