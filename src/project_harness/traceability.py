"""Traceability policy checker for TOML node graphs with requirement-side test links."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

from project_harness.config import walk_files
from project_harness.model import (
    DEPRECATED_STATUS,
    NODE_ID_PATTERN,
    TEST_TYPE,
    Node,
    TraceabilityConfig,
    TraceabilityRule,
)

ALLOWED_NODE_KEYS = frozenset({"id", "type", "status", "title", "description", "links", "tests"})


def _validate_node_id(node_id: str, scope: str) -> str:
    if not NODE_ID_PATTERN.match(node_id):
        raise ValueError(f"{scope}: invalid node id '{node_id}'")
    return node_id


def _parse_string_list(raw_list: Any, relative_path: str, key: str) -> tuple[str, ...]:
    if not isinstance(raw_list, list) or not all(isinstance(entry, str) for entry in raw_list):
        raise ValueError(f"{relative_path}: '{key}' must be a list of strings")
    cleaned = tuple(entry.strip() for entry in raw_list)
    if any(not entry for entry in cleaned) or len(set(cleaned)) != len(cleaned):
        raise ValueError(f"{relative_path}: empty or duplicate '{key}' entries in {raw_list}")
    return cleaned


def _is_test_link(test_link: str) -> bool:
    file_part, separator, name_part = test_link.partition("::")
    posix = PurePosixPath(file_part)
    return (
        bool(file_part and separator)
        and all(name_part.split("::"))
        and not posix.is_absolute()
        and ".." not in posix.parts
    )


def _parse_test_links(raw_tests: Any, relative_path: str) -> tuple[str, ...]:
    test_links = _parse_string_list(raw_tests, relative_path, "tests")
    for test_link in test_links:
        if not _is_test_link(test_link):
            raise ValueError(
                f"{relative_path}: test '{test_link}' must be '<relative path>::<test name>'"
            )
    return test_links


def _load_toml_node(path: Path, expected_type: str, relative_path: str) -> Node:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    unknown = sorted(set(raw) - ALLOWED_NODE_KEYS)
    if unknown:
        raise ValueError(f"{relative_path}: unknown keys {unknown}")
    raw_id = str(raw.get("id", path.stem)).strip()
    if raw_id != path.stem:
        raise ValueError(f"{relative_path}: id '{raw_id}' does not match filename '{path.stem}'")
    node_type = str(raw.get("type", expected_type)).strip()
    if node_type != expected_type:
        raise ValueError(f"{relative_path}: type '{node_type}' does not match '{expected_type}'")
    status = str(raw.get("status", "")).strip()
    if not status:
        raise ValueError(f"{relative_path}: missing 'status'")
    return Node(
        _validate_node_id(raw_id, relative_path),
        node_type,
        status,
        _parse_string_list(raw.get("links", []), relative_path, "links"),
        relative_path,
        _parse_test_links(raw.get("tests", []), relative_path),
    )


def _discover_nodes(root: Path, locations: dict[str, str]) -> dict[str, Node]:
    base = root.resolve()
    discovered: dict[str, Node] = {}
    for node_type, pattern in locations.items():
        for file_path in walk_files(base, (pattern,)):
            relative_path = file_path.relative_to(base).as_posix()
            if file_path.suffix != ".toml":
                raise ValueError(f"{relative_path}: traceability nodes must be .toml files")
            node = _load_toml_node(file_path, node_type, relative_path)
            if node.id in discovered:
                raise ValueError(f"{relative_path}: duplicate node id '{node.id}'")
            discovered[node.id] = node
    if not discovered:
        raise ValueError("no traceability nodes discovered for configured locations")
    return discovered


def _build_adjacency(nodes: dict[str, Node], edges: set[frozenset[str]]) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    for node in nodes.values():
        for target_id in node.links:
            target = nodes.get(target_id)
            if target and frozenset((node.type, target.type)) in edges:
                adjacency[node.id].add(target_id)
                adjacency[target_id].add(node.id)
    return adjacency


def _format_violation(node: Node, code: str, message: str, rule_id: str | None = None) -> str:
    prefix = f"{node.location}: {node.id}" if node.location else node.id
    suffix = f" ({rule_id})" if rule_id else ""
    return f"{prefix} [{code}]{suffix}: {message}"


def _test_exists(root: Path, test_link: str) -> bool:
    relative_path, *names = test_link.split("::")
    file_path = root / relative_path
    if not file_path.is_file():
        return False
    text = file_path.read_text(encoding="utf-8")
    return all(re.search(rf"\b{re.escape(name)}\b", text) for name in names)


def _check_test_links(root: Path, node: Node) -> list[str]:
    return [
        _format_violation(node, "ERR_DANGLING_TEST", f"test '{test_link}' does not exist")
        for test_link in node.tests
        if not _test_exists(root, test_link)
    ]


def _check_node_links(node: Node, nodes: dict[str, Node], edges: set[frozenset[str]]) -> list[str]:
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
        elif node.status != DEPRECATED_STATUS and target.status == DEPRECATED_STATUS:
            violations.append(
                _format_violation(
                    node,
                    "ERR_DEPRECATED_LINK",
                    f"active {node.type} links to {DEPRECATED_STATUS} '{target_id}'",
                )
            )
    return violations


def _check_rule_for_node(
    node: Node, rule: TraceabilityRule, neighbors: set[str], nodes: dict[str, Node]
) -> list[str]:
    if node.status == DEPRECATED_STATUS or (node.type, node.status) != (
        rule.source_type,
        rule.when_status,
    ):
        return []
    if rule.target_type == TEST_TYPE:
        missing = _format_violation(node, "ERR_MISSING_LINK", "requires >= 1 test", rule.id)
        return [] if node.tests else [missing]
    targets = [
        nodes[target_id]
        for target_id in sorted(neighbors)
        if nodes[target_id].type == rule.target_type
        and nodes[target_id].status != DEPRECATED_STATUS
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


def check_traceability(root: Path, config: TraceabilityConfig) -> list[str]:
    """Discover traceability nodes under root and validate links, test links, and rules."""
    base = root.resolve()
    nodes = _discover_nodes(base, config.locations)
    edges = {
        frozenset((config.chain[index], config.chain[index + 1]))
        for index in range(len(config.chain) - 1)
    }
    adjacency = _build_adjacency(nodes, edges)
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
        violations.extend(_check_node_links(node, nodes, edges))
        violations.extend(_check_test_links(base, node))
        for rule in config.rules:
            violations.extend(_check_rule_for_node(node, rule, adjacency[node_id], nodes))
    return violations
