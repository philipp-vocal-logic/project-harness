"""Architecture policy checker for file line limits and forbidden line patterns."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from project_harness.config import walk_files
from project_harness.model import ArchitectureRule


def check_architecture_text(relative_path: str, text: str, rule: ArchitectureRule) -> list[str]:
    """Check file content against a rule's max_lines limit and forbid regex patterns."""
    lines = text.removesuffix("\n").split("\n") if text else []
    violations: list[str] = []
    if rule.max_lines is not None and len(lines) > rule.max_lines:
        violations.append(
            f"{relative_path}:1:1 [ERR_ARCHITECTURE]: "
            f"{len(lines)}/{rule.max_lines} lines (Fix: {rule.fix})"
        )
    for line_number, line in enumerate(lines, start=1):
        if any(pattern.search(line) for pattern in rule.forbid):
            violations.append(
                f"{relative_path}:{line_number}:1 [ERR_ARCHITECTURE]: "
                f"{line.strip()} (Fix: {rule.fix})"
            )
    return violations


def check_architecture_rule(root: Path, rule: ArchitectureRule) -> list[str]:
    """Check a single architecture rule across all matching files under root."""
    base = root.resolve()
    violations: list[str] = []
    for path in walk_files(base, rule.files):
        relative_path = path.relative_to(base).as_posix()
        violations.extend(
            check_architecture_text(relative_path, path.read_text(encoding="utf-8"), rule)
        )
    return violations


def check_architecture(root: Path, rules: Sequence[ArchitectureRule]) -> list[str]:
    """Check all architecture rules across root and return violation lines."""
    violations: list[str] = []
    for rule in rules:
        violations.extend(check_architecture_rule(root, rule))
    return violations
