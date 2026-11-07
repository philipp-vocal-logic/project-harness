"""Unit tests for .policy/symbols.toml parsing and symbol naming schema enforcement."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from project_harness.config import parse_symbols
from project_harness.symbol import check_symbol_text, check_symbols


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
        ("[settings]\nfiles = []\n", "must not be empty"),
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


def test_check_symbol_parses_c_definitions_by_kind_and_visibility() -> None:
    symbols = parse_symbols(
        '[settings]\nfiles = ["include/**/*.h", "src/**/*.c"]\n'
        'forbidden_subwords = ["temp"]\n[replacements]\nbuffer = ["buf"]\n'
        '[[rules]]\nfiles = ["include/*.h", "src/*.c"]\nkinds = ["module"]\n'
        'pattern = "^[a-z][a-z0-9_]*$"\nfix = "Use lower_snake_case module names."\n'
        '[[rules]]\nkinds = ["macro", "enumerator"]\nvisibility = "public"\n'
        'pattern = "^UART_[A-Z0-9_]+$"\nfix = "Prefix public macros and enumerators with UART_."\n'
        '[[rules]]\nkinds = ["macro", "enumerator"]\nvisibility = "private"\n'
        'pattern = "^LOCAL_[A-Z0-9_]+$"\n'
        'fix = "Prefix private macros and enumerators with LOCAL_."\n'
        '[[rules]]\nkinds = ["typedef"]\n'
        'pattern = "^[a-z][a-z0-9_]*_t$"\nfix = "Suffix typedefs with _t."\n'
        '[[rules]]\nkinds = ["type"]\n'
        'pattern = "^uart_[a-z0-9_]+$"\nfix = "Prefix struct, union, and enum tags with uart_."\n'
        '[[rules]]\nkinds = ["function"]\nvisibility = "public"\n'
        'pattern = "^uart_[a-z0-9_]+$"\nfix = "Prefix public functions with uart_."\n'
        '[[rules]]\nkinds = ["function"]\nvisibility = "private"\n'
        'pattern = "^_[a-z0-9_]+$"\nfix = "Prefix static functions with _."\n'
        '[[rules]]\nkinds = ["variable"]\nvisibility = "public"\n'
        'pattern = "^g_[a-z0-9_]+$"\nfix = "Prefix public variables with g_."\n'
        '[[rules]]\nkinds = ["variable"]\nvisibility = "private"\n'
        'pattern = "^s_[a-z0-9_]+$"\nfix = "Prefix static variables with s_."\n'
    )
    header = (
        "#define BAD_MACRO 1\n"
        "struct bad_tag;\n"
        "typedef struct uart_frame { int temp_field; } bad_typedef;\n"
        "typedef void (*bad_callback)(int status);\n"
        "enum uart_mode { BAD_ENUM = (1 << 0), UART_OK = 2 };\n"
        "extern int bad_global;\n"
        "int bad_open(int baud);\n"
    )
    assert check_symbol_text("include/BadHeader.h", header, symbols) == [
        "include/BadHeader.h:1:1 [ERR_SYMBOL]: module 'BadHeader' violates '^[a-z][a-z0-9_]*$' "
        "(Fix: Use lower_snake_case module names.)",
        "include/BadHeader.h:1:9 [ERR_SYMBOL]: macro 'BAD_MACRO' violates '^UART_[A-Z0-9_]+$' "
        "(Fix: Prefix public macros and enumerators with UART_.)",
        "include/BadHeader.h:2:8 [ERR_SYMBOL]: type 'bad_tag' violates '^uart_[a-z0-9_]+$' "
        "(Fix: Prefix struct, union, and enum tags with uart_.)",
        "include/BadHeader.h:3:33 [ERR_SYMBOL]: Symbol 'temp_field' contains forbidden subword "
        "'temp' (Fix: replace 'temp' with a domain noun)",
        "include/BadHeader.h:3:47 [ERR_SYMBOL]: typedef 'bad_typedef' violates "
        "'^[a-z][a-z0-9_]*_t$' (Fix: Suffix typedefs with _t.)",
        "include/BadHeader.h:4:16 [ERR_SYMBOL]: typedef 'bad_callback' violates "
        "'^[a-z][a-z0-9_]*_t$' (Fix: Suffix typedefs with _t.)",
        "include/BadHeader.h:5:18 [ERR_SYMBOL]: enumerator 'BAD_ENUM' violates '^UART_[A-Z0-9_]+$' "
        "(Fix: Prefix public macros and enumerators with UART_.)",
        "include/BadHeader.h:6:12 [ERR_SYMBOL]: variable 'bad_global' violates '^g_[a-z0-9_]+$' "
        "(Fix: Prefix public variables with g_.)",
        "include/BadHeader.h:7:5 [ERR_SYMBOL]: function 'bad_open' violates '^uart_[a-z0-9_]+$' "
        "(Fix: Prefix public functions with uart_.)",
    ]
    source = (
        "#define BAD_LOCAL 1\n"
        "enum uart_step { BAD_STEP };\n"
        "static int temp_buf[4] = { 1, 2 };\n"
        "static void (*bad_handler)(int code) = 0;\n"
        "struct uart_frame g_active_frame;\n"
        "static int bad_static(int temp_arg) { int local_temp = temp_arg; return local_temp; }\n"
        "int uart_send(void) { return _ok_static(); }\n"
    )
    assert check_symbol_text("src/uart.c", source, symbols) == [
        "src/uart.c:1:9 [ERR_SYMBOL]: macro 'BAD_LOCAL' violates '^LOCAL_[A-Z0-9_]+$' "
        "(Fix: Prefix private macros and enumerators with LOCAL_.)",
        "src/uart.c:2:18 [ERR_SYMBOL]: enumerator 'BAD_STEP' violates '^LOCAL_[A-Z0-9_]+$' "
        "(Fix: Prefix private macros and enumerators with LOCAL_.)",
        "src/uart.c:3:12 [ERR_SYMBOL]: Symbol 'temp_buf' contains forbidden subword 'temp' "
        "(Fix: replace 'temp' with a domain noun)",
        "src/uart.c:3:12 [ERR_SYMBOL]: Symbol 'temp_buf' uses non-canonical 'buf' "
        "(Fix: use 'buffer')",
        "src/uart.c:3:12 [ERR_SYMBOL]: variable 'temp_buf' violates '^s_[a-z0-9_]+$' "
        "(Fix: Prefix static variables with s_.)",
        "src/uart.c:4:15 [ERR_SYMBOL]: variable 'bad_handler' violates '^s_[a-z0-9_]+$' "
        "(Fix: Prefix static variables with s_.)",
        "src/uart.c:6:12 [ERR_SYMBOL]: function 'bad_static' violates '^_[a-z0-9_]+$' "
        "(Fix: Prefix static functions with _.)",
        "src/uart.c:6:27 [ERR_SYMBOL]: Symbol 'temp_arg' contains forbidden subword 'temp' "
        "(Fix: replace 'temp' with a domain noun)",
        "src/uart.c:6:43 [ERR_SYMBOL]: Symbol 'local_temp' contains forbidden subword 'temp' "
        "(Fix: replace 'temp' with a domain noun)",
    ]


def test_check_symbol_c_ignores_comments_literals_disabled_blocks_and_extern_c() -> None:
    symbols = parse_symbols('[settings]\nforbidden_subwords = ["temp"]\n')
    header = (
        "/* int temp_comment(void); */\n"
        "// #define TEMP_LINE 1\n"
        'const char *g_banner = "int temp_literal;";\n'
        "#if 0\n"
        "int temp_disabled(void) { return 0; }\n"
        "#else\n"
        '#ifdef __cplusplus\nextern "C" {\n#endif\n'
        '_Static_assert(sizeof(int) == 4, "ok");\n'
        "int __attribute__((weak)) temp_active(void);\n"
        "#ifdef __cplusplus\n}\n#endif\n"
        "struct ok_after_extern { int x; };\n"
        "static int _use_external(struct ok_after_extern *box) {\n"
        "    struct ok_after_extern copy = { .temp_init = 0 };\n"
        "    return external_temp_call(box->temp_ptr + copy.temp_dot);\n"
        "}\n"
        "#endif\n"
    )
    assert check_symbol_text("include/uart.h", header, symbols) == [
        "include/uart.h:11:27 [ERR_SYMBOL]: Symbol 'temp_active' contains forbidden subword 'temp' "
        "(Fix: replace 'temp' with a domain noun)"
    ]


def test_check_symbols_scopes_discovery_with_settings_files(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "legacy.py").write_text('print "py2"\n', encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "uart.c").write_text("int temp_port = 0;\n", encoding="utf-8")
    symbols = parse_symbols(
        '[settings]\nfiles = ["src/**/*.[ch]"]\nforbidden_subwords = ["temp"]\n'
    )
    assert check_symbols(tmp_path, symbols) == [
        "src/uart.c:1:5 [ERR_SYMBOL]: Symbol 'temp_port' contains forbidden subword 'temp' "
        "(Fix: replace 'temp' with a domain noun)"
    ]
