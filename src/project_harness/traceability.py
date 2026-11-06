"""Bidirectional traceability checker linking requirement TOML files to discovered tests."""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from project_harness.config import parse_requirement, walk_files
from project_harness.model import Requirement, TraceabilityConfig

C_COMMENT_OR_LITERAL_PATTERN = re.compile(
    r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|/\*[\s\S]*?\*/|//[^\n]*'
)
C_TEST_PATTERN = re.compile(
    r"\b(?:static\s+)?(?:void|int)\s+(test_[A-Za-z0-9_]*)\s*\(\s*(?:void)?\s*\)\s*\{"
)


def _discover_requirements(root: Path, patterns: Sequence[str]) -> dict[str, Requirement]:
    base = root.resolve()
    discovered: dict[str, Requirement] = {}
    for file_path in walk_files(base, patterns):
        relative_path = file_path.relative_to(base).as_posix()
        if file_path.suffix != ".toml":
            raise ValueError(f"{relative_path}: traceability nodes must be .toml files")
        requirement = parse_requirement(file_path.read_text(encoding="utf-8"), relative_path)
        if requirement.id in discovered:
            raise ValueError(f"{relative_path}: duplicate requirement id '{requirement.id}'")
        discovered[requirement.id] = requirement
    return discovered


def _is_test_function(node: ast.AST) -> bool:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
        "test_"
    )


def _location(relative_path: str, node: ast.stmt) -> str:
    return f"{relative_path}:{node.lineno}:{node.col_offset + 1}"


def _parse_class_tests(relative_path: str, class_node: ast.ClassDef) -> dict[str, str]:
    return {
        f"{relative_path}::{class_node.name}::{child.name}": _location(relative_path, child)
        for child in class_node.body
        if _is_test_function(child)
    }


def _parse_file_tests(relative_path: str, text: str) -> dict[str, str]:
    tree = ast.parse(text, filename=relative_path)
    discovered: dict[str, str] = {}
    for statement in tree.body:
        if _is_test_function(statement):
            discovered[f"{relative_path}::{statement.name}"] = _location(relative_path, statement)
        elif isinstance(statement, ast.ClassDef) and statement.name.startswith("Test"):
            discovered.update(_parse_class_tests(relative_path, statement))
    return discovered


def _blank_match(match: re.Match[str]) -> str:
    return "\n" * match.group(0).count("\n")


def _parse_c_tests(relative_path: str, text: str) -> dict[str, str]:
    blanked = C_COMMENT_OR_LITERAL_PATTERN.sub(_blank_match, text)
    return {
        f"{relative_path}::{match.group(1)}": (
            f"{relative_path}:{blanked.count('\n', 0, match.start()) + 1}:1"
        )
        for match in C_TEST_PATTERN.finditer(blanked)
    }


TEST_PARSERS: dict[str, Callable[[str, str], dict[str, str]]] = {
    ".c": _parse_c_tests,
    ".py": _parse_file_tests,
}


def _discover_tests(root: Path, patterns: Sequence[str]) -> dict[str, str]:
    base = root.resolve()
    discovered: dict[str, str] = {}
    for file_path in walk_files(base, patterns):
        relative_path = file_path.relative_to(base).as_posix()
        if (parser := TEST_PARSERS.get(file_path.suffix)) is None:
            raise ValueError(f"{relative_path}: unsupported test file type '{file_path.suffix}'")
        discovered.update(parser(relative_path, file_path.read_text(encoding="utf-8")))
    return discovered


def _check_requirement_tests(
    requirement: Requirement, discovered_tests: Mapping[str, str]
) -> list[str]:
    prefix = f"{requirement.location}: {requirement.id}" if requirement.location else requirement.id
    if not requirement.tests:
        return [
            f"{prefix} [ERR_MISSING_TEST]: requirement has no linked tests "
            "(Fix: add at least one '<path>::<test_name>' entry to 'tests')"
        ]
    return [
        f"{prefix} [ERR_DANGLING_TEST]: test '{test_link}' does not exist "
        f"(Fix: define '{test_link}' or remove the stale link on a 'policy/*' branch)"
        for test_link in requirement.tests
        if test_link not in discovered_tests
    ]


def _check_untraced_tests(discovered_tests: Mapping[str, str], linked_tests: set[str]) -> list[str]:
    return [
        f"{location} [ERR_UNTRACED_TEST]: test '{test_link}' is not linked to any requirement "
        f"(Fix: add '{test_link}' to a requirement's 'tests' list in trace/)"
        for test_link, location in discovered_tests.items()
        if test_link not in linked_tests
    ]


def _is_requirement_weakened(
    base_requirement: Requirement, head_requirement: Requirement | None
) -> bool:
    return (
        head_requirement is None
        or head_requirement.location != base_requirement.location
        or head_requirement.title != base_requirement.title
        or not set(base_requirement.acceptance) <= set(head_requirement.acceptance)
        or len(head_requirement.tests) < len(base_requirement.tests)
    )


def check_requirement_changes(
    base_requirements: Mapping[str, Requirement],
    head_requirements: Mapping[str, Requirement],
) -> list[str]:
    """Return sorted requirement paths whose base specification or test links were weakened."""
    weakened: set[str] = set()
    for requirement_id, base_requirement in base_requirements.items():
        head_requirement = head_requirements.get(requirement_id)
        if _is_requirement_weakened(base_requirement, head_requirement):
            weakened.add(base_requirement.location)
            if head_requirement and head_requirement.location:
                weakened.add(head_requirement.location)
    return sorted(weakened)


def check_traceability(root: Path, config: TraceabilityConfig) -> list[str]:
    """Discover requirements and tests under root and verify bidirectional links."""
    base = root.resolve()
    requirements = _discover_requirements(base, config.requirements)
    discovered_tests = _discover_tests(base, config.tests)
    linked_tests: set[str] = set()
    violations: list[str] = []
    for requirement_id in sorted(requirements):
        requirement = requirements[requirement_id]
        linked_tests.update(requirement.tests)
        violations.extend(_check_requirement_tests(requirement, discovered_tests))
    violations.extend(_check_untraced_tests(discovered_tests, linked_tests))
    return violations
