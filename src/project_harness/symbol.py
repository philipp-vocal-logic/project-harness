"""Symbol naming policy checker for Python source files."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from project_harness.config import walk_files
from project_harness.model import (
    Replacement,
    Symbol,
    SymbolSchema,
    matches_subwords,
    normalize_subwords,
    singularize,
)

INTRINSIC_NAMES: frozenset[str] = frozenset({"_", "self", "cls"})
SCOPE_NODES = (
    ast.ClassDef,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


@dataclass(frozen=True, slots=True)
class _CompiledRules:
    forbidden_subwords: frozenset[str]
    replacements: tuple[tuple[tuple[str, ...], str, Replacement], ...]


def _is_intrinsic(name: str) -> bool:
    return (
        not name
        or name in INTRINSIC_NAMES
        or (len(name) > 4 and name.startswith("__") and name.endswith("__"))
    )


def _binding_name(node: ast.AST) -> str | None:
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name
    if isinstance(node, ast.arg):
        return node.arg
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
        is_self = isinstance(node.value, ast.Name) and node.value.id in ("self", "cls")
        return node.attr if is_self else None
    if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
        return node.name
    return node.asname if isinstance(node, ast.alias) else None


def _walk_ast(
    node: ast.AST, file: Path, symbols: list[Symbol], seen: set[str], cls_seen: set[str]
) -> None:
    name = _binding_name(node)
    if name is not None and not _is_intrinsic(name):
        target_seen = cls_seen if isinstance(node, ast.Attribute) else seen
        if name not in target_seen:
            target_seen.add(name)
            symbols.append(Symbol(name, file, node.lineno, node.col_offset + 1))
    child_seen = set() if isinstance(node, SCOPE_NODES) else seen
    child_cls = child_seen if isinstance(node, ast.ClassDef) else cls_seen
    for child in ast.iter_child_nodes(node):
        _walk_ast(child, file, symbols, child_seen, child_cls)


def _extract_python_symbols(text: str, file: Path) -> list[Symbol]:
    tree = ast.parse(text, filename=str(file))
    symbols: list[Symbol] = []
    seen: set[str] = set()
    mod_name = file.parent.name if file.stem == "__init__" else file.stem
    if not _is_intrinsic(mod_name):
        seen.add(mod_name)
        symbols.append(Symbol(mod_name, file, 1, 1))
    _walk_ast(tree, file, symbols, seen, set())
    return symbols


def _compile_rules(schema: SymbolSchema) -> _CompiledRules:
    replacements = tuple(
        (norm, raw, entry)
        for entry in schema.replacements
        for raw in entry.forbidden
        if (norm := normalize_subwords(raw))
    )
    return _CompiledRules(
        forbidden_subwords=frozenset(singularize(w) for w in schema.forbidden_subwords),
        replacements=replacements,
    )


def _check_single_symbol(symbol: Symbol, rules: _CompiledRules) -> list[str]:
    rel = symbol.file.as_posix()
    subwords = normalize_subwords(symbol.name)
    violations: list[str] = []
    for word in dict.fromkeys(subwords):
        if word in rules.forbidden_subwords:
            violations.append(
                f"{rel}:{symbol.line}:{symbol.column} [ERR_SYMBOL]: Symbol '{symbol.name}' "
                f"contains forbidden subword '{word}' (Fix: replace '{word}' with a domain noun)"
            )
    for forbidden_words, raw_forbidden, entry in rules.replacements:
        if matches_subwords(subwords, forbidden_words):
            violations.append(
                f"{rel}:{symbol.line}:{symbol.column} [ERR_SYMBOL]: Symbol '{symbol.name}' "
                f"uses non-canonical '{raw_forbidden}' (Fix: use '{entry.canonical}')"
            )
    return violations


def check_symbol_text(rel_path: str, text: str, schema: SymbolSchema) -> list[str]:
    """Extract symbols from Python source text and check each against schema."""
    file = Path(rel_path)
    if file.suffix != ".py":
        raise ValueError(f"symbol checker supports only .py files: '{rel_path}'")
    symbols = _extract_python_symbols(text, file)
    rules = _compile_rules(schema)
    violations: list[str] = []
    for symbol in symbols:
        violations.extend(_check_single_symbol(symbol, rules))
    return violations


def check_symbols(root: Path, schema: SymbolSchema) -> list[str]:
    """Check all tracked Python files under root."""
    base = root.resolve()
    files = walk_files(base, ("**/*.py",))
    if not files:
        raise ValueError("no .py files found under repository root")
    violations: list[str] = []
    for file_path in files:
        rel = file_path.relative_to(base).as_posix()
        violations.extend(check_symbol_text(rel, file_path.read_text(encoding="utf-8"), schema))
    return violations
