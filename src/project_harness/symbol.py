"""Symbol naming policy checker for Python source files."""

from __future__ import annotations

import ast
from collections.abc import Sequence
from pathlib import Path

from project_harness.config import walk_files
from project_harness.model import (
    Symbol,
    SymbolRule,
    SymbolSchema,
    matches_any,
    split_subwords,
    subword_forms,
)

INTRINSIC_NAMES: frozenset[str] = frozenset({"", "_", "self", "cls"})
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


def _binding(node: ast.AST, in_class: bool) -> tuple[str | None, str]:
    if spec := DIRECT_BINDINGS.get(type(node)):
        return getattr(node, spec[0]), spec[1]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name, ("method" if in_class else "function")
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
    in_class: bool,
    symbols: list[Symbol],
    seen: set[tuple[str, str]],
) -> None:
    name, kind = _binding(node, in_class)
    if name is not None and not _is_intrinsic(name) and (name, kind) not in seen:
        seen.add((name, kind))
        symbols.append(Symbol(name, kind, file, node.lineno, node.col_offset + 1))
    child_in_class = isinstance(node, ast.ClassDef)
    for child in ast.iter_child_nodes(node):
        _walk_ast(child, file, child_in_class, symbols, seen)


def _parse_python_symbols(text: str, file: Path) -> list[Symbol]:
    tree = ast.parse(text, filename=str(file))
    symbols: list[Symbol] = []
    seen: set[tuple[str, str]] = set()
    module_name = file.parent.name if file.stem == "__init__" else file.stem
    if not _is_intrinsic(module_name):
        seen.add((module_name, "module"))
        symbols.append(Symbol(module_name, "module", file, 1, 1))
    _walk_ast(tree, file, False, symbols, seen)
    return symbols


def _compile_subwords(schema: SymbolSchema) -> dict[str, str]:
    fixes = {
        form: f"contains forbidden subword '{word}' (Fix: replace '{word}' with a domain noun)"
        for word in schema.forbidden_subwords
        for form in subword_forms(word)
    }
    fixes.update(
        {
            form: f"uses non-canonical '{raw_forbidden}' (Fix: use '{canonical}')"
            for canonical, forbidden_list in schema.replacements.items()
            for raw_forbidden in forbidden_list
            for form in subword_forms(raw_forbidden)
        }
    )
    return fixes


def _matches_rule_target(symbol: Symbol, rule: SymbolRule) -> bool:
    if symbol.kind not in rule.kinds:
        return False
    if rule.visibility == "all":
        return True
    is_private = symbol.name.startswith("_")
    return is_private if rule.visibility == "private" else not is_private


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
    if file.suffix != ".py":
        raise ValueError(f"symbol checker supports only .py files: '{relative_path}'")
    file_rules = tuple(rule for rule in rules if matches_any(relative_path, rule.files))
    violations: list[str] = []
    for symbol in _parse_python_symbols(text, file):
        violations.extend(_check_single_symbol(symbol, subword_fixes))
        violations.extend(_check_schema_rules(symbol, file_rules))
    return violations


def check_symbol_text(relative_path: str, text: str, schema: SymbolSchema) -> list[str]:
    """Parse symbols from Python source text and check each against schema."""
    return _check_file_symbols(relative_path, text, _compile_subwords(schema), schema.rules)


def check_symbols(root: Path, schema: SymbolSchema) -> list[str]:
    """Check all tracked Python files under root."""
    base = root.resolve()
    subword_fixes = _compile_subwords(schema)
    violations: list[str] = []
    for file_path in walk_files(base, ("**/*.py",)):
        relative_path = file_path.relative_to(base).as_posix()
        violations.extend(
            _check_file_symbols(
                relative_path, file_path.read_text("utf-8"), subword_fixes, schema.rules
            )
        )
    return violations
