"""End-to-end test exercising the full TDD orchestrator and merge gate with all .policy tools."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from trace_check import trace

from project_harness.cli import main

PYTHON_BIN = sys.executable

AGENT_SCRIPT = """from pathlib import Path
import sys

stage, target, prompt = sys.argv[1], sys.argv[2], sys.argv[3]
root = Path.cwd()

if stage == "planner":
    req = root / "trace" / "requirements" / f"{target}.toml"
    req.write_text('status = "DRAFT"\\nlinks = ["US-1"]\\n', encoding="utf-8")
elif stage == "red-test":
    test_file = root / "tests" / "test_calc.py"
    test_file.write_text(
        "from trace_check import trace\\n"
        "from src.calc import multiply\\n\\n"
        f"@trace('{target}')\\n"
        "def test_multiply() -> None:\\n"
        "    assert multiply(3, 4) == 12\\n",
        encoding="utf-8",
    )
elif stage == "implementer":
    calc = root / "src" / "calc.py"
    if "Violations:" not in prompt:
        # First attempt fails test to exercise orchestrator retry feedback loop
        calc.write_text("def multiply(a: int, b: int) -> int:\\n    return -1\\n", encoding="utf-8")
    else:
        body = "def multiply(a: int, b: int) -> int:\\n    return a * b\\n"
        calc.write_text(body, encoding="utf-8")
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
TestCase = "tests/test_*.py"

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
files = ["src/**/*.py"]
max_lines = 100
fix = "Keep src modules concise."
"""

GLOSSARY_TOML = """[settings]
paths = ["src", "tests", "trace"]
exclude = ["glossary.toml", "symbols.toml"]

[[terms]]
term = "multiply"
definition = "Compute the arithmetic product of two integers."
forbidden_terms = ["times_op"]
"""

SYMBOLS_TOML = """[settings]
include = ["src/**/*.py", "tests/**/*.py"]
forbidden_subwords = ["temp", "mgr"]
"""


def _build_workflow_toml(agent_script: Path) -> str:
    checks = (
        f'"{PYTHON_BIN} -m glossary_checker_cli.cli --config .policy/glossary.toml", '
        f'"{PYTHON_BIN} -m symbol_checker.cli --config .policy/symbols.toml", '
        f'"{PYTHON_BIN} -m archcheck --config .policy/arch.toml", '
        f'"{PYTHON_BIN} -m trace_check.cli .policy/traceability.toml"'
    )
    agent_cmd = (
        f'["{PYTHON_BIN}", "{agent_script.as_posix()}", "{{stage}}", "{{target}}", "{{prompt}}"]'
    )
    return f"""[workflow]
protected_paths = [".policy/**"]
checks = [{checks}, "{PYTHON_BIN} -m pytest -q"]

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
allow_write = ["tests/**/*.py"]
checks = [{checks}, "{PYTHON_BIN} -m pytest --collect-only -q"]
fail_checks = ["{PYTHON_BIN} -m pytest -q"]
prompt = "Red test {{target}}"

[[stages]]
id = "implementer"
commit_prefix = "feat"
allow_write = ["src/**/*.py"]
checks = [{checks}, "{PYTHON_BIN} -m pytest -q"]
prompt = "Implement {{target}}"

[[stages]]
id = "reviewer"
commit_prefix = "review"
allow_write = ["trace/**/*.toml"]
checks = [{checks}, "{PYTHON_BIN} -m pytest -q"]
prompt = "Review {{target}}"
"""


def _setup_e2e_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    agent_script = tmp_path / "mock_agent.py"
    agent_script.write_text(AGENT_SCRIPT, encoding="utf-8")

    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "a@b.c"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Agent"], check=True)
    (repo / ".gitignore").write_text(
        "__pycache__/\n*.py[cod]\n.pytest_cache/\n.ruff_cache/\n", encoding="utf-8"
    )

    policy = repo / ".policy"
    policy.mkdir()
    cfg = policy / "workflow.toml"
    cfg.write_text(_build_workflow_toml(agent_script), encoding="utf-8")
    (policy / "traceability.toml").write_text(TRACEABILITY_TOML, encoding="utf-8")
    (policy / "arch.toml").write_text(ARCH_TOML, encoding="utf-8")
    (policy / "glossary.toml").write_text(GLOSSARY_TOML, encoding="utf-8")
    (policy / "symbols.toml").write_text(SYMBOLS_TOML, encoding="utf-8")

    (repo / "src").mkdir()
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "calc.py").write_text(
        "def multiply(a: int, b: int) -> int:\n    return 0\n", encoding="utf-8"
    )
    (repo / "tests").mkdir()
    (repo / "trace" / "stories").mkdir(parents=True)
    (repo / "trace" / "requirements").mkdir(parents=True)
    (repo / "trace" / "stories" / "US-1.toml").write_text('status = "APPROVED"\n', encoding="utf-8")

    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "chore(init): seed"], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "feat/req-calc"], check=True)
    return repo, cfg


@trace("REQ-ORCHESTRATOR", "REQ-MERGE")
def test_e2e_orchestrator_tdd_workflow_and_merge_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, cfg = _setup_e2e_repo(tmp_path)

    # 1. Execute full 4-stage TDD workflow via orchestrator (including retry on implementer)
    assert main(["--config", str(cfg), "run", "REQ-CALC"]) == 0
    assert capsys.readouterr().out == ""

    # 2. Verify 4 stage commits were recorded in order on the feature branch
    log_proc = subprocess.run(
        ["git", "-C", str(repo), "log", "--reverse", "--format=%s", "main..HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert log_proc.stdout.splitlines() == [
        "plan(REQ-CALC): planner",
        "test(REQ-CALC): red-test",
        "feat(REQ-CALC): implementer",
        "review(REQ-CALC): reviewer",
    ]

    # 3. Verify local/GitHub branch merge check passes with 0 output
    assert main(["--config", str(cfg), "check", "--base", "main"]) == 0
    assert capsys.readouterr().out == ""

    # 4. Verify plain `harness check` passes with 0 output
    assert main(["--config", str(cfg), "check"]) == 0
    assert capsys.readouterr().out == ""
