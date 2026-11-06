"""Unit tests for .policy/traceability.toml, requirement parsing, and bidirectional test checks."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from project_harness.config import parse_requirement, parse_traceability
from project_harness.traceability import check_requirement_changes, check_traceability


def _git_init(repository: Path) -> None:
    """Make repository a git work tree so harness can list its files."""
    subprocess.run(["git", "init", "-q", str(repository)], check=True)


def test_parse_traceability_validates_config() -> None:
    default_config = parse_traceability("")
    assert default_config.requirements == ("trace/*.toml",)
    assert default_config.tests == ("tests/**/test_*.py",)

    custom = parse_traceability(
        '[traceability]\nrequirements = ["specs/*.toml"]\ntests = ["tests/*.py"]\n'
    )
    assert custom.requirements == ("specs/*.toml",)
    assert custom.tests == ("tests/*.py",)

    for bad_toml in (
        "unknown = 1\n",
        'traceability = "bad"\n',
        "[traceability]\nrequirements = []\n",
        "[traceability]\ntests = []\n",
    ):
        with pytest.raises(ValueError):
            parse_traceability(bad_toml)


@pytest.mark.parametrize(
    ("toml_text", "expected_match"),
    [
        ('id = "REQ-1"\ntitle = "ok"\nacceptance = ["a"]\nunknown = 1\n', "unknown keys"),
        ('id = "OTHER"\ntitle = "ok"\nacceptance = ["a"]\n', "must match filename 'REQ-1'"),
        ('id = "REQ-1"\nacceptance = ["a"]\n', "'title' must be a non-empty string"),
        ('id = "REQ-1"\ntitle = "ok"\n', "acceptance"),
        ('id = "REQ-1"\ntitle = "ok"\nacceptance = []\n', "unique single-line strings"),
        ('id = "REQ-1"\ntitle = "ok"\nacceptance = ["a", "a"]\n', "unique single-line strings"),
        ('id = "REQ-1"\ntitle = "ok"\nacceptance = ["a\\nb"]\n', "unique single-line strings"),
        ('id = "REQ-1"\ntitle = "ok"\nacceptance = ["a"]\ntests = "bad"\n', "list of strings"),
        (
            'id = "REQ-1"\ntitle = "ok"\nacceptance = ["a"]\ntests = ["tests/test_a.py"]\n',
            "unique '<relative path>",
        ),
        (
            'id = "REQ-1"\ntitle = "ok"\nacceptance = ["a"]\ntests = ["tests/a.py::"]\n',
            "unique '<relative path>",
        ),
        (
            'id = "REQ-1"\ntitle = "ok"\nacceptance = ["a"]\ntests = ["../tests/a.py::test_x"]\n',
            "unique '<relative path>",
        ),
        (
            'id = "REQ-1"\ntitle = "ok"\nacceptance = ["a"]\n'
            'tests = ["tests/a.py::test_x", "tests/a.py::test_x"]\n',
            "unique '<relative path>",
        ),
    ],
)
def test_parse_requirement_rejects_invalid_fields(toml_text: str, expected_match: str) -> None:
    with pytest.raises(ValueError, match=expected_match):
        parse_requirement(toml_text, "trace/REQ-1.toml")


def test_check_traceability_verifies_bidirectional_ast_links(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text(
        "# test_comment_only is not a real AST test\n"
        "class TestCalc:\n"
        "    def test_add(self) -> None:\n"
        "        pass\n\n"
        "async def test_untraced() -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "trace").mkdir()
    requirements = {
        "REQ-1": 'title = "Add"\nacceptance = ["a"]\n'
        'tests = ["tests/test_calc.py::TestCalc::test_add"]',
        "REQ-2": 'title = "Sub"\nacceptance = ["a"]\n'
        'tests = ["tests/test_calc.py::test_comment_only"]',
        "REQ-3": 'title = "Empty"\nacceptance = ["a"]\ntests = []',
    }
    for requirement_id, body in requirements.items():
        (tmp_path / "trace" / f"{requirement_id}.toml").write_text(
            f'id = "{requirement_id}"\n{body}\n', encoding="utf-8"
        )

    violations = check_traceability(tmp_path, parse_traceability(""))
    assert violations == [
        "trace/REQ-2.toml: REQ-2 [ERR_DANGLING_TEST]: "
        "test 'tests/test_calc.py::test_comment_only' does not exist "
        "(Fix: define 'tests/test_calc.py::test_comment_only' or remove the stale link "
        "on a 'policy/*' branch)",
        "trace/REQ-3.toml: REQ-3 [ERR_MISSING_TEST]: requirement has no linked tests "
        "(Fix: add at least one '<path>::<test_name>' entry to 'tests')",
        "tests/test_calc.py:6:1 [ERR_UNTRACED_TEST]: "
        "test 'tests/test_calc.py::test_untraced' is not linked to any requirement "
        "(Fix: add 'tests/test_calc.py::test_untraced' to a requirement's 'tests' list in trace/)",
    ]

    (tmp_path / "trace" / "notes.txt").write_text("bad\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"must be \.toml files"):
        check_traceability(
            tmp_path, parse_traceability('[traceability]\nrequirements = ["trace/*"]')
        )

    (tmp_path / "trace" / "sub").mkdir()
    (tmp_path / "trace" / "sub" / "REQ-1.toml").write_text(
        'id = "REQ-1"\ntitle = "Dup"\nacceptance = ["a"]\ntests = []\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="duplicate requirement id 'REQ-1'"):
        check_traceability(
            tmp_path, parse_traceability('[traceability]\nrequirements = ["trace/**/*.toml"]')
        )


def test_check_traceability_discovers_c_unity_tests(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.c").write_text(
        "/* void test_comment_only(void) {} */\n"
        "// void test_line_comment_only(void) {}\n"
        'const char *banner = "void test_banner_only(void) {}";\n'
        "static void test_add(void);\n"
        "static void test_helper(int value) {}\n"
        "void setUp(void) {}\n"
        "void tearDown(void) {}\n"
        "static void test_add(void) {}\n"
        "int test_untraced() {}\n"
        "int main(void) { return 0; }\n",
        encoding="utf-8",
    )
    (tmp_path / "trace").mkdir()
    (tmp_path / "trace" / "REQ-1.toml").write_text(
        'id = "REQ-1"\ntitle = "Add"\nacceptance = ["a"]\n'
        'tests = ["tests/test_calc.c::test_add"]\n',
        encoding="utf-8",
    )
    (tmp_path / "trace" / "REQ-2.toml").write_text(
        'id = "REQ-2"\ntitle = "Comment"\nacceptance = ["a"]\n'
        'tests = ["tests/test_calc.c::test_comment_only"]\n',
        encoding="utf-8",
    )
    c_config = parse_traceability('[traceability]\ntests = ["tests/**/test_*.c"]\n')
    assert check_traceability(tmp_path, c_config) == [
        "trace/REQ-2.toml: REQ-2 [ERR_DANGLING_TEST]: "
        "test 'tests/test_calc.c::test_comment_only' does not exist "
        "(Fix: define 'tests/test_calc.c::test_comment_only' or remove the stale link "
        "on a 'policy/*' branch)",
        "tests/test_calc.c:9:1 [ERR_UNTRACED_TEST]: "
        "test 'tests/test_calc.c::test_untraced' is not linked to any requirement "
        "(Fix: add 'tests/test_calc.c::test_untraced' to a requirement's 'tests' list in trace/)",
    ]

    (tmp_path / "tests" / "test_bad.txt").write_text("bad\n", encoding="utf-8")
    bad_config = parse_traceability('[traceability]\ntests = ["tests/test_bad.txt"]\n')
    with pytest.raises(ValueError, match="unsupported test file"):
        check_traceability(tmp_path, bad_config)


def test_check_requirement_changes_detects_weakened_requirements() -> None:
    base = {
        "REQ-1": parse_requirement(
            'id = "REQ-1"\ntitle = "Keep"\nacceptance = ["c1"]\n'
            'tests = ["tests/test_a.py::test_one"]\n',
            "trace/REQ-1.toml",
        ),
        "REQ-2": parse_requirement(
            'id = "REQ-2"\ntitle = "Delete"\nacceptance = ["c2"]\n'
            'tests = ["tests/test_a.py::test_two"]\n',
            "trace/REQ-2.toml",
        ),
    }
    additive_head = {
        "REQ-1": parse_requirement(
            'id = "REQ-1"\ntitle = "Keep"\nacceptance = ["c1", "c1_extra"]\n'
            'tests = ["tests/test_a.py::test_renamed", "tests/test_a.py::test_extra"]\n',
            "trace/REQ-1.toml",
        ),
        "REQ-2": parse_requirement(
            'id = "REQ-2"\ntitle = "Delete"\nacceptance = ["c2"]\n'
            'tests = ["tests/test_a.py::test_two_renamed"]\n',
            "trace/REQ-2.toml",
        ),
        "REQ-3": parse_requirement(
            'id = "REQ-3"\ntitle = "New"\nacceptance = ["c3"]\n'
            'tests = ["tests/test_a.py::test_three"]\n',
            "trace/REQ-3.toml",
        ),
    }
    assert check_requirement_changes(base, additive_head) == []

    weakened_head = {
        "REQ-1": parse_requirement(
            'id = "REQ-1"\ntitle = "Keep"\nacceptance = ["c1"]\n'
            'tests = ["tests/test_a.py::test_one"]\n',
            "trace/moved/REQ-1.toml",
        ),
    }
    assert check_requirement_changes(base, weakened_head) == [
        "trace/REQ-1.toml",
        "trace/REQ-2.toml",
        "trace/moved/REQ-1.toml",
    ]
    for variant_toml in (
        'id = "REQ-1"\ntitle = "Retitled"\nacceptance = ["c1"]\n'
        'tests = ["tests/test_a.py::test_one"]\n',
        'id = "REQ-1"\ntitle = "Keep"\nacceptance = ["c1_changed"]\n'
        'tests = ["tests/test_a.py::test_one"]\n',
        'id = "REQ-1"\ntitle = "Keep"\nacceptance = ["c1"]\ntests = []\n',
    ):
        head = {
            "REQ-1": parse_requirement(variant_toml, "trace/REQ-1.toml"),
            "REQ-2": base["REQ-2"],
        }
        assert check_requirement_changes(base, head) == ["trace/REQ-1.toml"]
