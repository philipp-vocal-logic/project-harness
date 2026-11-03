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
        "def fetch_temps[temp_type](x: dict[str, int]) -> int:\n"
        "    match x:\n"
        "        case {**temp_rest}:\n"
        "            return 1\n"
    )
    symbol_hits = check_symbol_text("src/app.py", code, symbols)
    assert len(symbol_hits) == 4 and all("[ERR_SYMBOL]" in hit for hit in symbol_hits)


def test_check_symbol_enforces_schema_rules_by_kind_visibility_and_files() -> None:
    symbols = parse_symbols(
        '[[rules]]\nfiles = ["src/*.py"]\nkinds = ["class", "type_var"]\n'
        'pattern = "^_?[A-Z][a-zA-Z0-9]*$"\nfix = "Use PascalCase."\n'
        '[[rules]]\nfiles = ["src/*.py"]\nkinds = ["constant"]\n'
        'pattern = "^[A-Z][A-Z0-9_]*$"\nfix = "Use UPPER_SNAKE_CASE."\n'
        '[[rules]]\nfiles = ["src/*.py"]\nkinds = ["function"]\nvisibility = "public"\n'
        'pattern = "^load_[a-z0-9_]+$"\nfix = "Prefix public functions with load_."\n'
        '[[rules]]\nfiles = ["src/*.py"]\nkinds = ["function"]\nvisibility = "private"\n'
        'pattern = "^_[a-z][a-z0-9_]*$"\nfix = "Use _snake_case for private functions."\n'
    )
    code = (
        "bad_const = 1\n"
        "GOOD_CONST = 2\n"
        "type bad_alias = int\n"
        "class bad_class:\n"
        "    def __init__(self) -> None:\n"
        "        self.attr = 1\n"
        "def bad_public() -> None:\n"
        "    pass\n"
        "def load_ok() -> None:\n"
        "    pass\n"
        "def _BadPrivate() -> None:\n"
        "    pass\n"
        "def _good_private() -> None:\n"
        "    pass\n"
    )
    hits = check_symbol_text("src/app.py", code, symbols)
    assert hits == [
        "src/app.py:1:1 [ERR_SYMBOL]: constant 'bad_const' violates '^[A-Z][A-Z0-9_]*$' "
        "(Fix: Use UPPER_SNAKE_CASE.)",
        "src/app.py:3:6 [ERR_SYMBOL]: type_var 'bad_alias' violates '^_?[A-Z][a-zA-Z0-9]*$' "
        "(Fix: Use PascalCase.)",
        "src/app.py:4:1 [ERR_SYMBOL]: class 'bad_class' violates '^_?[A-Z][a-zA-Z0-9]*$' "
        "(Fix: Use PascalCase.)",
        "src/app.py:7:1 [ERR_SYMBOL]: function 'bad_public' violates '^load_[a-z0-9_]+$' "
        "(Fix: Prefix public functions with load_.)",
        "src/app.py:11:1 [ERR_SYMBOL]: function '_BadPrivate' violates '^_[a-z][a-z0-9_]*$' "
        "(Fix: Use _snake_case for private functions.)",
    ]
    assert check_symbol_text("tests/app.py", code, symbols) == []


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
