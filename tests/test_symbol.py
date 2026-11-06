"""Unit tests for .policy/symbols.toml parsing and symbol naming schema enforcement."""

from __future__ import annotations

import pytest

from project_harness.config import parse_symbols
from project_harness.symbol import check_symbol_text


def test_check_symbol_detects_forbidden_subwords_and_replacements() -> None:
    symbols = parse_symbols(
        '[settings]\nforbidden_subwords = ["temp"]\n[replacements]\nload = ["fetch"]\n'
    )
    code = (
        "import os as temp_os\n"
        "class Box:\n"
        "    def set_items(self, other: Box) -> None:\n"
        "        self.temp_attr = 1\n"
        "        other.temp_ignored = 2\n"
        "def fetch_temps[temp_type, **temp_spec, *temp_tuple](x: dict[str, int]) -> int:\n"
        "    try:\n"
        "        match x:\n"
        "            case [*temp_star]:\n"
        "                return 0\n"
        "            case {**temp_rest}:\n"
        "                return 1\n"
        "            case temp_as:\n"
        "                return 2\n"
        "    except Exception as temp_error:\n"
        "        return 3\n"
    )
    violations = check_symbol_text("src/app.py", code, symbols)
    assert len(violations) == 11 and all("[ERR_SYMBOL]" in violation for violation in violations)


def test_check_symbol_enforces_schema_rules_by_kind_visibility_and_files() -> None:
    symbols = parse_symbols(
        '[[rules]]\nfiles = ["src/*.py"]\nkinds = ["module"]\n'
        'pattern = "^[a-z][a-z0-9_]*$"\nfix = "Use lower_snake_case."\n'
        '[[rules]]\nfiles = ["src/*.py"]\nkinds = ["class"]\n'
        'pattern = "^_?[A-Z][a-zA-Z0-9]*$"\nfix = "Use PascalCase."\n'
        '[[rules]]\nfiles = ["src/*.py"]\nkinds = ["method"]\nvisibility = "public"\n'
        'pattern = "^[a-z][a-z0-9_]*$"\nfix = "Use snake_case for methods."\n'
        '[[rules]]\nfiles = ["src/*.py"]\nkinds = ["function"]\nvisibility = "public"\n'
        'pattern = "^load_[a-z0-9_]+$"\nfix = "Prefix public functions with load_."\n'
        '[[rules]]\nfiles = ["src/*.py"]\nkinds = ["function"]\nvisibility = "private"\n'
        'pattern = "^_[a-z][a-z0-9_]*$"\nfix = "Use _snake_case for private functions."\n'
    )
    code = (
        "logger = 1\n"
        "type Alias = int\n"
        "class bad_class:\n"
        "    MAX_RETRIES = 3\n"
        "    def __init__(self) -> None:\n"
        "        self.attr = 1\n"
        "    def BadMethod(self) -> None:\n"
        "        pass\n"
        "def bad_public() -> None:\n"
        "    pass\n"
        "def load_ok() -> None:\n"
        "    def local_step() -> None:\n"
        "        pass\n"
        "    local_step()\n"
        "def _BadPrivate() -> None:\n"
        "    pass\n"
        "def _good_private() -> None:\n"
        "    pass\n"
    )
    violations = check_symbol_text("src/BadModule.py", code, symbols)
    assert violations == [
        "src/BadModule.py:1:1 [ERR_SYMBOL]: module 'BadModule' violates '^[a-z][a-z0-9_]*$' "
        "(Fix: Use lower_snake_case.)",
        "src/BadModule.py:3:1 [ERR_SYMBOL]: class 'bad_class' violates '^_?[A-Z][a-zA-Z0-9]*$' "
        "(Fix: Use PascalCase.)",
        "src/BadModule.py:7:5 [ERR_SYMBOL]: method 'BadMethod' violates '^[a-z][a-z0-9_]*$' "
        "(Fix: Use snake_case for methods.)",
        "src/BadModule.py:9:1 [ERR_SYMBOL]: function 'bad_public' violates '^load_[a-z0-9_]+$' "
        "(Fix: Prefix public functions with load_.)",
        "src/BadModule.py:15:1 [ERR_SYMBOL]: function '_BadPrivate' violates '^_[a-z][a-z0-9_]*$' "
        "(Fix: Use _snake_case for private functions.)",
    ]
    assert check_symbol_text("tests/BadModule.py", code, symbols) == []
    pkg_symbols = parse_symbols(
        '[[rules]]\nfiles = ["src/**/__init__.py"]\nkinds = ["module"]\n'
        'pattern = "^[a-z][a-z0-9_]*$"\nfix = "Use lower_snake_case."\n'
    )
    assert check_symbol_text("src/BadPkg/__init__.py", "", pkg_symbols) == [
        "src/BadPkg/__init__.py:1:1 [ERR_SYMBOL]: module 'BadPkg' violates '^[a-z][a-z0-9_]*$' "
        "(Fix: Use lower_snake_case.)"
    ]


@pytest.mark.parametrize(
    ("bad_toml", "expected_match"),
    [
        ('[settings]\nforbidden_subwords = ["temp_val"]\n', "single subwords"),
        ('[replacements]\nload = ["read_config"]\n', "single subwords"),
        ('rules = "bad"\n', "must be a list of tables"),
        ('[[rules]]\nkinds = ["unknown"]\npattern = "^a$"\nfix = "x"\n', "kinds in"),
        (
            '[[rules]]\nkinds = ["class"]\nvisibility = "bad"\npattern = "^A$"\nfix = "x"\n',
            "visibility must be in",
        ),
        ('[[rules]]\nkinds = ["class"]\npattern = "("\nfix = "x"\n', "invalid symbol regex"),
    ],
)
def test_symbol_checker_rejects_non_python_files_and_multi_word_rules(
    bad_toml: str, expected_match: str
) -> None:
    with pytest.raises(ValueError, match=r"only \.py files"):
        check_symbol_text("src/calc.rs", "fn main() {}\n", parse_symbols(""))
    with pytest.raises(ValueError, match=expected_match):
        parse_symbols(bad_toml)
