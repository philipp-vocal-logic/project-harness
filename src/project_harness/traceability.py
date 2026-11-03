"""Traceability policy checker for TOML node graphs with requirement-side test links."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

from project_harness.config import walk_files
from project_harness.model import NODE_ID_RE, TEST_TYPE, Node, TraceConfig, TraceRule

ALLOWED_NODE_KEYS = frozenset({"id", "type", "status", "title", "description", "links", "tests"})


def _validate_node_id(node_id: str, scope: str) -> str:
    if not NODE_ID_RE.match(node_id):
        raise ValueError(f"{scope}: invalid node id '{node_id}'")
    return node_id


def _str_list(raw_list: Any, rel_path: str, key: str) -> tuple[str, ...]:
    if not isinstance(raw_list, list) or not all(isinstance(entry, str) for entry in raw_list):
        raise ValueError(f"{rel_path}: '{key}' must be a list of strings")
    cleaned = tuple(entry.strip() for entry in raw_list)
    if any(not entry for entry in cleaned) or len(set(cleaned)) != len(cleaned):
        raise ValueError(f"{rel_path}: empty or duplicate '{key}' entries in {raw_list}")
    return cleaned


def _is_test_ref(ref: str) -> bool:
    file_part, sep, name_part = ref.partition("::")
    posix = PurePosixPath(file_part)
    return (
        bool(file_part and sep)
        and all(name_part.split("::"))
        and not posix.is_absolute()
        and ".." not in posix.parts
    )


def _parse_test_refs(raw_tests: Any, rel_path: str) -> tuple[str, ...]:
    refs = _str_list(raw_tests, rel_path, "tests")
    for ref in refs:
        if not _is_test_ref(ref):
            raise ValueError(f"{rel_path}: test '{ref}' must be '<relative path>::<test name>'")
    return refs


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
    return Node(
        _validate_node_id(raw_id, rel_path),
        node_type,
        status,
        _str_list(raw.get("links", []), rel_path, "links"),
        rel_path,
        _parse_test_refs(raw.get("tests", []), rel_path),
    )


def _discover_nodes(root: Path, locations: dict[str, str]) -> dict[str, Node]:
    base = root.resolve()
    discovered: dict[str, Node] = {}
    for node_type, pattern in locations.items():
        for file_path in walk_files(base, (pattern,), ()):
            rel_path = file_path.relative_to(base).as_posix()
            if file_path.suffix != ".toml":
                raise ValueError(f"{rel_path}: traceability nodes must be .toml files")
            node = _load_toml_node(file_path, node_type, rel_path)
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


def _test_exists(root: Path, ref: str) -> bool:
    rel_path, *names = ref.split("::")
    file_path = root / rel_path
    if not file_path.is_file():
        return False
    text = file_path.read_text(encoding="utf-8")
    return all(re.search(rf"\b{re.escape(name)}\b", text) for name in names)


def _check_test_refs(root: Path, node: Node) -> list[str]:
    return [
        _format_violation(node, "ERR_DANGLING_TEST", f"test '{ref}' does not exist")
        for ref in node.tests
        if not _test_exists(root, ref)
    ]


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
    if rule.target_type == TEST_TYPE:
        missing = _format_violation(node, "ERR_MISSING_LINK", "requires >= 1 test", rule.id)
        return [] if node.tests else [missing]
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
    """Discover traceability nodes under root and validate links, test links, and rules."""
    base = root.resolve()
    nodes = _discover_nodes(base, config.locations)
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
        violations.extend(_check_test_refs(base, node))
        for rule in config.rules:
            violations.extend(_check_rule_for_node(node, rule, adj[node_id], nodes, depr))
    return violations
