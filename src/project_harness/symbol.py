"""Symbol naming policy checker for Python source files."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from project_harness.config import walk_files
from project_harness.model import (
    Symbol,
    SymbolSchema,
    split_subwords,
    subword_forms,
)

INTRINSIC_NAMES: frozenset[str] = frozenset({"", "_", "self", "cls"})
NAMED_BINDING_NODES = (
    ast.ClassDef,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.TypeVar,
    ast.ParamSpec,
    ast.TypeVarTuple,
    ast.ExceptHandler,
    ast.MatchAs,
    ast.MatchStar,
)


@dataclass(frozen=True, slots=True)
class _CompiledRules:
    forbidden_subwords: dict[str, str]
    replacements: dict[str, tuple[str, str]]


def _binding_name(node: ast.AST) -> str | None:
    if isinstance(node, NAMED_BINDING_NODES):
        return node.name
    if isinstance(node, ast.arg):
        return node.arg
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
        is_self = isinstance(node.value, ast.Name) and node.value.id in ("self", "cls")
        return node.attr if is_self else None
    if isinstance(node, ast.MatchMapping):
        return node.rest
    return node.asname if isinstance(node, ast.alias) else None


def _walk_ast(node: ast.AST, file: Path, symbols: list[Symbol], seen: set[str]) -> None:
    name = _binding_name(node)
    if name is not None and name not in INTRINSIC_NAMES and name not in seen:
        seen.add(name)
        symbols.append(Symbol(name, file, node.lineno, node.col_offset + 1))
    for child in ast.iter_child_nodes(node):
        _walk_ast(child, file, symbols, seen)


def _parse_python_symbols(text: str, file: Path) -> list[Symbol]:
    tree = ast.parse(text, filename=str(file))
    symbols: list[Symbol] = []
    seen: set[str] = set()
    module_name = file.parent.name if file.stem == "__init__" else file.stem
    if module_name not in INTRINSIC_NAMES:
        seen.add(module_name)
        symbols.append(Symbol(module_name, file, 1, 1))
    _walk_ast(tree, file, symbols, seen)
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
    return _CompiledRules(forbidden_subwords=forbidden_subwords, replacements=replacements)


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


def _check_file_symbols(rel_path: str, text: str, rules: _CompiledRules) -> list[str]:
    file = Path(rel_path)
    if file.suffix != ".py":
        raise ValueError(f"symbol checker supports only .py files: '{rel_path}'")
    violations: list[str] = []
    for symbol in _parse_python_symbols(text, file):
        violations.extend(_check_single_symbol(symbol, rules))
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
