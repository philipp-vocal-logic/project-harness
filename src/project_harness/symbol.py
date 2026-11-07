"""Symbol naming policy checker for Python and C source files."""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Sequence
from pathlib import Path

from project_harness.config import walk_files
from project_harness.model import (
    Symbol,
    SymbolRule,
    SymbolSchema,
    blank_c_source,
    matches_any,
    split_subwords,
    subword_forms,
)

INTRINSIC_NAMES: frozenset[str] = frozenset({"", "_", "self", "cls"})
C_INTRINSIC_CALLS: frozenset[str] = frozenset(
    {"sizeof", "_Alignas", "_Static_assert", "static_assert", "typeof"}
)
C_DEFINE_PATTERN = re.compile(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
C_IGNORED_SYNTAX_PATTERN = re.compile(
    r"^[ \t]*#(?:\\\n|[^\n])*|\bextern\s+\{|\b__attribute__\s*\(\([^;{}]*?\)\)", re.MULTILINE
)
C_TAG_DEFINITION_PATTERN = re.compile(r"\b(?:struct|union|enum)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{")
C_ENUM_BODY_PATTERN = re.compile(r"\benum(?:\s+[A-Za-z_][A-Za-z0-9_]*)?\s*\{([^{}]*)\}")
C_FUNCTION_POINTER_PATTERN = re.compile(r"\(\s*\*\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*\(")
C_DECLARATOR_END_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:\[[^\]]*\])*\s*;\s*$")
C_CALL_HEAD_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(")
C_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
C_SYMBOL_TOKEN_PATTERN = re.compile(r"(?<![.>])\b[A-Za-z_][A-Za-z0-9_]*\b(?!\s*\()")
C_PARENTHESIZED_PATTERN = re.compile(r"\([^()]*\)")
C_ENUMERATOR_DEFAULT_PATTERN = re.compile(r"=[^,]*")
C_STATIC_PATTERN = re.compile(r"\bstatic\b")
C_TYPEDEF_PATTERN = re.compile(r"\btypedef\b")
C_BRACE_BLOCK_PATTERN = re.compile(r"\{[^{}]*\}")
C_STATEMENT_PATTERN = re.compile(r"[^;]+;")
C_BARE_TYPE_HEAD_PATTERN = re.compile(
    r"^\s*(?:struct|union|enum)(?:\s+([A-Za-z_][A-Za-z0-9_]*))?\s*;\s*$"
)
CHILD_SCOPES: dict[type[ast.AST], str] = {
    ast.ClassDef: "class",
    ast.FunctionDef: "local",
    ast.AsyncFunctionDef: "local",
}
FUNCTION_KINDS: dict[str, str] = {"module": "function", "class": "method"}
DIRECT_BINDINGS: dict[type[ast.AST], tuple[str, str]] = {
    ast.ClassDef: ("name", "class"),
    ast.TypeVar: ("name", "symbol"),
    ast.ParamSpec: ("name", "symbol"),
    ast.TypeVarTuple: ("name", "symbol"),
    ast.ExceptHandler: ("name", "symbol"),
    ast.MatchAs: ("name", "symbol"),
    ast.MatchStar: ("name", "symbol"),
    ast.MatchMapping: ("rest", "symbol"),
    ast.arg: ("arg", "symbol"),
    ast.alias: ("asname", "symbol"),
}


def _is_intrinsic(name: str | None) -> bool:
    return (
        not name
        or name in INTRINSIC_NAMES
        or (len(name) > 4 and name.startswith("__") and name.endswith("__"))
    )


def _binding(node: ast.AST, scope: str) -> tuple[str | None, str]:
    if spec := DIRECT_BINDINGS.get(type(node)):
        return getattr(node, spec[0]), spec[1]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name, FUNCTION_KINDS.get(scope, "symbol")
    if not isinstance(getattr(node, "ctx", None), ast.Store):
        return None, ""
    if isinstance(node, ast.Name):
        return node.id, "symbol"
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return (node.attr, "symbol") if node.value.id in ("self", "cls") else (None, "")
    return None, ""


def _walk_ast(
    node: ast.AST,
    file: Path,
    scope: str,
    symbols: list[Symbol],
    seen: set[tuple[str, str]],
) -> None:
    name, kind = _binding(node, scope)
    if name is not None and not _is_intrinsic(name) and (name, kind) not in seen:
        seen.add((name, kind))
        symbols.append(
            Symbol(name, kind, file, node.lineno, node.col_offset + 1, name.startswith("_"))
        )
    child_scope = CHILD_SCOPES.get(type(node), scope)
    for child in ast.iter_child_nodes(node):
        _walk_ast(child, file, child_scope, symbols, seen)


def _parse_python_symbols(text: str, file: Path) -> list[Symbol]:
    tree = ast.parse(text, filename=str(file))
    symbols: list[Symbol] = []
    seen: set[tuple[str, str]] = set()
    module_name = file.parent.name if file.stem == "__init__" else file.stem
    if not _is_intrinsic(module_name):
        seen.add((module_name, "module"))
        symbols.append(Symbol(module_name, "module", file, 1, 1, module_name.startswith("_")))
    _walk_ast(tree, file, "module", symbols, seen)
    return symbols


def _blank_matches(pattern: re.Pattern[str], text: str) -> str:
    return pattern.sub(
        lambda match: "".join("\n" if char == "\n" else " " for char in match.group(0)), text
    )


def _replace_brace_block(match: re.Match[str]) -> str:
    blanked = "".join("\n" if char == "\n" else " " for char in match.group(0))
    if match.string[: match.start()].rstrip().endswith(")"):
        return f"{blanked[:-1]};"
    return blanked


def _flatten_c_code(code: str) -> str:
    while C_BRACE_BLOCK_PATTERN.search(code):
        code = C_BRACE_BLOCK_PATTERN.sub(_replace_brace_block, code)
    return code.replace("}", " ")


def _parse_enum_body(
    body: str, base_offset: int, is_source_file: bool
) -> list[tuple[int, str, str, bool]]:
    cleaned = _blank_matches(
        C_ENUMERATOR_DEFAULT_PATTERN, _blank_matches(C_PARENTHESIZED_PATTERN, body)
    )
    return [
        (base_offset + match.start(), match.group(0), "enumerator", is_source_file)
        for match in C_IDENTIFIER_PATTERN.finditer(cleaned)
    ]


def _parse_c_function_name(before_equals: str) -> re.Match[str] | None:
    for match in C_CALL_HEAD_PATTERN.finditer(before_equals):
        if match.group(1) not in C_INTRINSIC_CALLS:
            return match
    return None


def _find_c_declarator(
    head: str, before_equals: str, is_source_file: bool
) -> tuple[re.Match[str] | None, str, bool]:
    is_static = C_STATIC_PATTERN.search(before_equals) is not None
    pointer_declarator = C_FUNCTION_POINTER_PATTERN.search(head)
    if C_TYPEDEF_PATTERN.search(before_equals):
        return (
            pointer_declarator or C_DECLARATOR_END_PATTERN.search(head),
            "typedef",
            is_source_file,
        )
    if pointer_declarator is not None:
        return pointer_declarator, "variable", is_static
    if "(" in before_equals:
        return _parse_c_function_name(before_equals), "function", is_static
    trimmed = before_equals.rstrip(" \t\n;")
    return C_DECLARATOR_END_PATTERN.search(f"{trimmed};"), "variable", is_static


def _parse_c_head(head: str, start: int, is_source_file: bool) -> list[tuple[int, str, str, bool]]:
    if bare_type := C_BARE_TYPE_HEAD_PATTERN.match(head):
        tag = bare_type.group(1)
        return [(start + bare_type.start(1), tag, "type", is_source_file)] if tag else []
    declarator, kind, is_private = _find_c_declarator(head, head.partition("=")[0], is_source_file)
    if declarator is None:
        return []
    return [(start + declarator.start(1), declarator.group(1), kind, is_private)]


def _parse_c_definitions(
    code: str, blanked: str, is_source_file: bool
) -> list[tuple[int, str, str, bool]]:
    entries: list[tuple[int, str, str, bool]] = [
        (match.start(1), match.group(1), "macro", is_source_file)
        for match in C_DEFINE_PATTERN.finditer(blanked)
    ]
    for match in C_TAG_DEFINITION_PATTERN.finditer(code):
        entries.append((match.start(1), match.group(1), "type", is_source_file))
    for match in C_ENUM_BODY_PATTERN.finditer(code):
        entries.extend(_parse_enum_body(match.group(1), match.start(1), is_source_file))
    for match in C_STATEMENT_PATTERN.finditer(_flatten_c_code(code)):
        entries.extend(_parse_c_head(match.group(0), match.start(), is_source_file))
    return entries


def _parse_c_symbols(text: str, file: Path) -> list[Symbol]:
    blanked = blank_c_source(text)
    code = _blank_matches(C_IGNORED_SYNTAX_PATTERN, blanked)
    entries = _parse_c_definitions(code, blanked, file.suffix == ".c")
    defined_names = {name for _, name, _, _ in entries}
    for match in C_SYMBOL_TOKEN_PATTERN.finditer(code):
        if (name := match.group(0)) not in defined_names:
            defined_names.add(name)
            entries.append((match.start(), name, "symbol", False))
    symbols: list[Symbol] = []
    seen: set[tuple[str, str]] = set()
    if not _is_intrinsic(file.stem):
        seen.add((file.stem, "module"))
        symbols.append(Symbol(file.stem, "module", file, 1, 1, file.stem.startswith("_")))
    for offset, name, kind, is_private in sorted(entries, key=lambda entry: entry[0]):
        if not _is_intrinsic(name) and (name, kind) not in seen:
            seen.add((name, kind))
            line = text.count("\n", 0, offset) + 1
            column = offset - text.rfind("\n", 0, offset)
            symbols.append(Symbol(name, kind, file, line, column, is_private))
    return symbols


SYMBOL_PARSERS: dict[str, Callable[[str, Path], list[Symbol]]] = {
    ".c": _parse_c_symbols,
    ".h": _parse_c_symbols,
    ".py": _parse_python_symbols,
}


def _compile_subwords(schema: SymbolSchema) -> dict[str, str]:
    fixes = {
        form: f"contains forbidden subword '{word}' (Fix: replace '{word}' with a domain noun)"
        for word in schema.forbidden_subwords
        for form in subword_forms(word)
    }
    return fixes | {
        form: f"uses non-canonical '{raw_forbidden}' (Fix: use '{canonical}')"
        for canonical, forbidden_list in schema.replacements.items()
        for raw_forbidden in forbidden_list
        for form in subword_forms(raw_forbidden)
    }


def _matches_rule_target(symbol: Symbol, rule: SymbolRule) -> bool:
    if symbol.kind not in rule.kinds:
        return False
    if rule.visibility == "all":
        return True
    return symbol.is_private if rule.visibility == "private" else not symbol.is_private


def _check_single_symbol(symbol: Symbol, subword_fixes: dict[str, str]) -> list[str]:
    relative_path = symbol.file.as_posix()
    return [
        f"{relative_path}:{symbol.line}:{symbol.column} [ERR_SYMBOL]: Symbol '{symbol.name}' {fix}"
        for word in dict.fromkeys(split_subwords(symbol.name))
        if (fix := subword_fixes.get(word))
    ]


def _check_schema_rules(symbol: Symbol, file_rules: Sequence[SymbolRule]) -> list[str]:
    relative_path = symbol.file.as_posix()
    return [
        f"{relative_path}:{symbol.line}:{symbol.column} [ERR_SYMBOL]: {symbol.kind} "
        f"'{symbol.name}' violates '{rule.pattern.pattern}' (Fix: {rule.fix})"
        for rule in file_rules
        if _matches_rule_target(symbol, rule) and not rule.pattern.fullmatch(symbol.name)
    ]


def _check_file_symbols(
    relative_path: str, text: str, subword_fixes: dict[str, str], rules: Sequence[SymbolRule]
) -> list[str]:
    file = Path(relative_path)
    if (parser := SYMBOL_PARSERS.get(file.suffix)) is None:
        raise ValueError(
            f"symbol checker supports only .py files and C (.c, .h) files: '{relative_path}'"
        )
    file_rules = tuple(rule for rule in rules if matches_any(relative_path, rule.files))
    violations: list[str] = []
    for symbol in parser(text, file):
        violations.extend(_check_single_symbol(symbol, subword_fixes))
        violations.extend(_check_schema_rules(symbol, file_rules))
    return violations


def check_symbol_text(relative_path: str, text: str, schema: SymbolSchema) -> list[str]:
    """Parse symbols from Python or C source text and check each against schema."""
    return _check_file_symbols(relative_path, text, _compile_subwords(schema), schema.rules)


def check_symbols(root: Path, schema: SymbolSchema) -> list[str]:
    """Check all tracked files matching schema.files under root."""
    base = root.resolve()
    subword_fixes = _compile_subwords(schema)
    violations: list[str] = []
    for file_path in walk_files(base, schema.files):
        relative_path = file_path.relative_to(base).as_posix()
        violations.extend(
            _check_file_symbols(
                relative_path, file_path.read_text("utf-8"), subword_fixes, schema.rules
            )
        )
    return violations
