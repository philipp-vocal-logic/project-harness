"""Symbol naming policy checker for Python source files."""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass
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
    ast.TypeVar: ("name", "type_var"),
    ast.ParamSpec: ("name", "type_var"),
    ast.TypeVarTuple: ("name", "type_var"),
    ast.ExceptHandler: ("name", "variable"),
    ast.MatchAs: ("name", "variable"),
    ast.MatchStar: ("name", "variable"),
    ast.MatchMapping: ("rest", "variable"),
    ast.arg: ("arg", "parameter"),
    ast.alias: ("asname", "alias"),
}
CHILD_SCOPES: dict[type[ast.AST], str] = {
    ast.ClassDef: "attribute",
    ast.TypeAlias: "type_var",
    ast.FunctionDef: "variable",
    ast.AsyncFunctionDef: "variable",
    ast.Lambda: "variable",
    ast.ListComp: "variable",
    ast.SetComp: "variable",
    ast.DictComp: "variable",
    ast.GeneratorExp: "variable",
    ast.For: "variable",
    ast.AsyncFor: "variable",
    ast.With: "variable",
    ast.AsyncWith: "variable",
}


@dataclass(frozen=True, slots=True)
class _CompiledRules:
    forbidden_subwords: dict[str, str]
    replacements: dict[str, tuple[str, str]]
    rules: tuple[SymbolRule, ...]


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
        return node.name, ("method" if scope == "attribute" else "function")
    if not isinstance(getattr(node, "ctx", None), ast.Store):
        return None, ""
    if isinstance(node, ast.Name):
        return node.id, scope
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return (node.attr, "attribute") if node.value.id in ("self", "cls") else (None, "")
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
        symbols.append(Symbol(name, kind, file, node.lineno, node.col_offset + 1))
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
        symbols.append(Symbol(module_name, "module", file, 1, 1))
    _walk_ast(tree, file, "constant", symbols, seen)
    return symbols


def _compile_rules(schema: SymbolSchema) -> _CompiledRules:
    forbidden_subwords = {
        form: word for word in schema.forbidden_subwords for form in subword_forms(word)
    }
    replacements = {
        form: (raw_forbidden, entry.canonical)
        for entry in schema.replacements
        for raw_forbidden in entry.forbidden
        for form in subword_forms(raw_forbidden)
    }
    return _CompiledRules(
        forbidden_subwords=forbidden_subwords,
        replacements=replacements,
        rules=schema.rules,
    )


def _matches_rule_target(symbol: Symbol, rule: SymbolRule) -> bool:
    if symbol.kind not in rule.kinds:
        return False
    if rule.visibility == "all":
        return True
    is_private = symbol.name.startswith("_")
    return is_private if rule.visibility == "private" else not is_private


def _check_single_symbol(symbol: Symbol, rules: _CompiledRules) -> list[str]:
    rel_path = symbol.file.as_posix()
    violations: list[str] = []
    for word in dict.fromkeys(split_subwords(symbol.name)):
        if forbidden := rules.forbidden_subwords.get(word):
            violations.append(
                f"{rel_path}:{symbol.line}:{symbol.column} [ERR_SYMBOL]: Symbol '{symbol.name}' "
                f"contains forbidden subword '{forbidden}' "
                f"(Fix: replace '{forbidden}' with a domain noun)"
            )
        if replacement := rules.replacements.get(word):
            raw_forbidden, canonical = replacement
            violations.append(
                f"{rel_path}:{symbol.line}:{symbol.column} [ERR_SYMBOL]: Symbol '{symbol.name}' "
                f"uses non-canonical '{raw_forbidden}' (Fix: use '{canonical}')"
            )
    return violations


def _check_schema_rules(symbol: Symbol, file_rules: Sequence[SymbolRule]) -> list[str]:
    rel_path = symbol.file.as_posix()
    return [
        f"{rel_path}:{symbol.line}:{symbol.column} [ERR_SYMBOL]: {symbol.kind} "
        f"'{symbol.name}' violates '{rule.pattern.pattern}' (Fix: {rule.fix})"
        for rule in file_rules
        if _matches_rule_target(symbol, rule) and not rule.pattern.fullmatch(symbol.name)
    ]


def _check_file_symbols(rel_path: str, text: str, rules: _CompiledRules) -> list[str]:
    file = Path(rel_path)
    if file.suffix != ".py":
        raise ValueError(f"symbol checker supports only .py files: '{rel_path}'")
    file_rules = tuple(rule for rule in rules.rules if matches_any(rel_path, rule.files))
    violations: list[str] = []
    for symbol in _parse_python_symbols(text, file):
        violations.extend(_check_single_symbol(symbol, rules))
        violations.extend(_check_schema_rules(symbol, file_rules))
    return violations


def check_symbol_text(rel_path: str, text: str, schema: SymbolSchema) -> list[str]:
    """Parse symbols from Python source text and check each against schema."""
    return _check_file_symbols(rel_path, text, _compile_rules(schema))


def check_symbols(root: Path, schema: SymbolSchema) -> list[str]:
    """Check all tracked Python files under root."""
    base = root.resolve()
    files = walk_files(base, ("**/*.py",))
    if not files:
        raise ValueError("no .py files found under repository root")
    rules = _compile_rules(schema)
    violations: list[str] = []
    for file_path in files:
        rel_path = file_path.relative_to(base).as_posix()
        violations.extend(_check_file_symbols(rel_path, file_path.read_text("utf-8"), rules))
    return violations
