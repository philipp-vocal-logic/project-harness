"""Traceability policy checker for Story -> Requirement -> TestCase graphs."""

from __future__ import annotations

import ast
import re
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from project_harness.config import walk_files
from project_harness.model import (
    DEFAULT_ACTIVE_STATUS,
    NODE_ID_RE,
    Node,
    TraceConfig,
    TraceRule,
)

TRACE_MODULES = frozenset({"project_harness", "trace_check", "trace_check.checker"})
COMMENT_EXTS = frozenset({".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".rs"})
COMMENT_TRACE_RE = re.compile(r"(?://|/\*+)\s*@(trace\b[^\n*]*?)(?:\*/|\s*$)", re.MULTILINE)
TEST_DECL_RE = re.compile(
    r"\b(?:TEST(?:_F|_P)?\s*\(\s*([A-Za-z_]\w*)\s*,\s*([A-Za-z_]\w*)\s*\)"
    r"|fn\s+([A-Za-z_]\w*)\s*\("
    r"|(?:void|int|bool)\s+([A-Za-z_]\w*)\s*\()"
)
ALLOWED_NODE_KEYS = frozenset({"id", "type", "status", "title", "description", "links"})
DecoratedStmt = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


def _validate_node_id(node_id: str, scope: str) -> str:
    if not NODE_ID_RE.match(node_id):
        raise ValueError(f"{scope}: invalid node id '{node_id}'")
    return node_id


def _dedup_links(links: tuple[str, ...], scope: str) -> tuple[str, ...]:
    cleaned = tuple(link.strip() for link in links)
    if any(not link for link in cleaned) or len(set(cleaned)) != len(cleaned):
        raise ValueError(f"{scope}: empty or duplicate link targets in {list(links)}")
    return cleaned


def _parse_str_constant(expr: ast.expr, scope: str, label: str) -> str:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str) and expr.value.strip():
        return expr.value.strip()
    raise ValueError(f"{scope}: malformed @trace decorator: {label} must be a non-empty string")


def _parse_trace_kw(kw: ast.keyword, scope: str) -> Any:
    if kw.arg == "links":
        if not isinstance(kw.value, (ast.List, ast.Tuple)):
            raise ValueError(f"{scope}: malformed @trace decorator: 'links' must be a list")
        return tuple(_parse_str_constant(elt, scope, "link") for elt in kw.value.elts)
    if kw.arg in {"id", "status"}:
        return _parse_str_constant(kw.value, scope, f"'{kw.arg}'")
    raise ValueError(f"{scope}: malformed @trace decorator: unknown keyword '{kw.arg}'")


def _parse_trace_call(call: ast.expr, default_id: str, node_type: str, rel_path: str) -> Node:
    scope = f"{rel_path}:{call.lineno}"
    if not isinstance(call, ast.Call):
        raise ValueError(f"{scope}: malformed @trace decorator: expected @trace(...)")
    arg_links = tuple(_parse_str_constant(arg, scope, "link") for arg in call.args)
    kw_map = {kw.arg: _parse_trace_kw(kw, scope) for kw in call.keywords}
    if arg_links and "links" in kw_map:
        raise ValueError(f"{scope}: malformed @trace decorator: duplicate links specification")
    raw_links = arg_links or kw_map.get("links", ())
    node_id = _validate_node_id(kw_map["id"], scope) if "id" in kw_map else default_id
    status = kw_map.get("status", DEFAULT_ACTIVE_STATUS)
    return Node(node_id, node_type, status, _dedup_links(raw_links, scope), scope)


def _is_trace_decorator(expr: ast.expr) -> bool:
    target = expr.func if isinstance(expr, ast.Call) else expr
    return (isinstance(target, ast.Name) and target.id == "trace") or (
        isinstance(target, ast.Attribute) and target.attr == "trace"
    )


def _iter_decorated(
    parent: ast.AST, scope: tuple[str, ...]
) -> Iterator[tuple[DecoratedStmt, tuple[str, ...]]]:
    for child in ast.iter_child_nodes(parent):
        child_scope = (*scope, child.name) if isinstance(child, DecoratedStmt) else scope
        if isinstance(child, DecoratedStmt):
            yield child, child_scope
        yield from _iter_decorated(child, child_scope)


def _load_python_nodes(text: str, node_type: str, rel_path: str) -> list[Node]:
    tree = ast.parse(text, filename=rel_path)
    nodes: list[Node] = []
    for stmt, qualname in _iter_decorated(tree, ()):
        decs = [dec for dec in stmt.decorator_list if _is_trace_decorator(dec)]
        if not decs:
            continue
        scope = f"{rel_path}:{decs[0].lineno}"
        if isinstance(stmt, ast.ClassDef) or not stmt.name.startswith("test_"):
            raise ValueError(f"{scope}: @trace must decorate a test_* function, got '{stmt.name}'")
        if len(decs) > 1:
            raise ValueError(f"{scope}: duplicate @trace decorator on '{stmt.name}'")
        default_id = "::".join((rel_path, *qualname))
        nodes.append(_parse_trace_call(decs[0], default_id, node_type, rel_path))
    return nodes


def _load_comment_nodes(text: str, node_type: str, rel_path: str) -> list[Node]:
    matches = list(COMMENT_TRACE_RE.finditer(text))
    nodes: list[Node] = []
    seen: set[str] = set()
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        lineno = text.count("\n", 0, match.start()) + 1
        scope = f"{rel_path}:{lineno}"
        decl = TEST_DECL_RE.search(text, match.end(), next_start)
        if decl is None:
            raise ValueError(f"{scope}: @trace must precede a test function")
        name = (
            f"{decl.group(1)}_{decl.group(2)}"
            if decl.group(1)
            else (decl.group(3) or decl.group(4))
        )
        if name in seen:
            raise ValueError(f"{scope}: duplicate @trace decorator on '{name}'")
        seen.add(name)
        call = ast.parse(match.group(1).strip(), mode="eval").body
        call.lineno = lineno
        nodes.append(_parse_trace_call(call, f"{rel_path}::{name}", node_type, rel_path))
    return nodes


def _load_toml_node(path: Path, expected_type: str, rel_path: str) -> Node:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    unknown = sorted(set(raw) - ALLOWED_NODE_KEYS)
    if unknown:
        raise ValueError(f"{rel_path}: unknown keys {unknown}")
    raw_id = str(raw.get("id", path.stem)).strip()
    if raw_id != path.stem:
        raise ValueError(f"{rel_path}: id '{raw_id}' does not match filename '{path.stem}'")
    node_type = str(raw.get("type", expected_type)).strip()
    if node_type != expected_type:
        raise ValueError(f"{rel_path}: type '{node_type}' does not match '{expected_type}'")
    status = str(raw.get("status", "")).strip()
    if not status:
        raise ValueError(f"{rel_path}: missing 'status'")
    raw_links = raw.get("links", [])
    if not isinstance(raw_links, list) or not all(isinstance(x, str) for x in raw_links):
        raise ValueError(f"{rel_path}: 'links' must be a list of strings")
    return Node(
        _validate_node_id(raw_id, rel_path),
        node_type,
        status,
        _dedup_links(tuple(raw_links), rel_path),
        rel_path,
    )


def _load_file_nodes(file_path: Path, node_type: str, rel_path: str) -> list[Node]:
    if file_path.suffix == ".toml":
        return [_load_toml_node(file_path, node_type, rel_path)]
    text = file_path.read_text(encoding="utf-8-sig")
    if file_path.suffix.lower() in COMMENT_EXTS:
        return _load_comment_nodes(text, node_type, rel_path)
    return _load_python_nodes(text, node_type, rel_path)


def _discover_nodes(root: Path, locations: dict[str, str]) -> dict[str, Node]:
    base = root.resolve()
    discovered: dict[str, Node] = {}
    for node_type, pattern in locations.items():
        for file_path in walk_files(base, (pattern,), ()):
            rel_path = file_path.relative_to(base).as_posix()
            for node in _load_file_nodes(file_path, node_type, rel_path):
                if node.id in discovered:
                    raise ValueError(f"{rel_path}: duplicate node id '{node.id}'")
                discovered[node.id] = node
    if not discovered:
        raise ValueError("no traceability nodes discovered for configured locations")
    return discovered


def _build_adjacency(nodes: dict[str, Node], edges: set[frozenset[str]]) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    for node in nodes.values():
        for target_id in node.links:
            target = nodes.get(target_id)
            if target and frozenset((node.type, target.type)) in edges:
                adj[node.id].add(target_id)
                adj[target_id].add(node.id)
    return adj


def _format_violation(node: Node, code: str, message: str, rule_id: str | None = None) -> str:
    prefix = f"{node.location}: {node.id}" if node.location else node.id
    suffix = f" ({rule_id})" if rule_id else ""
    return f"{prefix} [{code}]{suffix}: {message}"


def _check_node_links(
    node: Node, nodes: dict[str, Node], edges: set[frozenset[str]], depr: str
) -> list[str]:
    violations: list[str] = []
    for target_id in node.links:
        target = nodes.get(target_id)
        if target is None:
            violations.append(
                _format_violation(node, "ERR_DANGLING_LINK", f"target '{target_id}' does not exist")
            )
        elif frozenset((node.type, target.type)) not in edges:
            violations.append(
                _format_violation(
                    node, "ERR_INVALID_EDGE", f"cannot link {node.type} to {target.type}"
                )
            )
        elif node.status != depr and target.status == depr:
            violations.append(
                _format_violation(
                    node, "ERR_DEPRECATED_LINK", f"active {node.type} links to {depr} '{target_id}'"
                )
            )
    return violations


def _check_rule_for_node(
    node: Node, rule: TraceRule, neighbors: set[str], nodes: dict[str, Node], depr: str
) -> list[str]:
    if node.status == depr or (node.type, node.status) != (rule.source_type, rule.when_status):
        return []
    targets = [
        nodes[tid]
        for tid in sorted(neighbors)
        if nodes[tid].type == rule.target_type and nodes[tid].status != depr
    ]
    if not targets:
        return [
            _format_violation(
                node, "ERR_MISSING_LINK", f"requires >= 1 active {rule.target_type}", rule.id
            )
        ]
    expected = list(rule.target_status)
    return [
        _format_violation(
            node,
            "ERR_TARGET_STATUS",
            f"linked {target.type} '{target.id}' is '{target.status}', expected {expected}",
            rule.id,
        )
        for target in targets
        if target.status not in rule.target_status
    ]


def check_trace(root: Path, config: TraceConfig) -> list[str]:
    """Discover traceability nodes under root and validate links and rules."""
    nodes = _discover_nodes(root, config.locations)
    edges = {
        frozenset((config.chain[index], config.chain[index + 1]))
        for index in range(len(config.chain) - 1)
    }
    adj = _build_adjacency(nodes, edges)
    depr = config.deprecated_status
    violations: list[str] = []
    for node_id in sorted(nodes):
        node = nodes[node_id]
        allowed_statuses = config.statuses.get(node.type, frozenset())
        if node.status not in allowed_statuses:
            violations.append(
                _format_violation(
                    node, "ERR_INVALID_STATUS", f"invalid status '{node.status}' for {node.type}"
                )
            )
            continue
        violations.extend(_check_node_links(node, nodes, edges, depr))
        for rule in config.rules:
            violations.extend(_check_rule_for_node(node, rule, adj[node_id], nodes, depr))
    return violations
