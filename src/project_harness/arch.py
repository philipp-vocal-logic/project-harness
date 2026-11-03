"""Architecture policy checker for file line limits and forbidden line patterns."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from project_harness.config import walk_files
from project_harness.model import ArchRule, matches_glob


def check_arch_text(rel_path: str, text: str, rule: ArchRule) -> list[str]:
    """Check file content against a rule's max_lines limit and forbid regex patterns."""
    lines = text.removesuffix("\n").split("\n") if text else []
    violations: list[str] = []
    if rule.max_lines is not None and len(lines) > rule.max_lines:
        violations.append(
            f"{rel_path}:1:1 [ERR_ARCH]: {len(lines)}/{rule.max_lines} lines (Fix: {rule.fix})"
        )
    for line_no, line in enumerate(lines, start=1):
        if any(pattern.search(line) for pattern in rule.forbid):
            violations.append(
                f"{rel_path}:{line_no}:1 [ERR_ARCH]: {line.strip()} (Fix: {rule.fix})"
            )
    return violations


def _resolve_rule_files(root: Path, rule: ArchRule) -> list[Path]:
    matched = walk_files(root, rule.files)
    rel_paths = [path.relative_to(root.resolve()).as_posix() for path in matched]
    for pattern in rule.files:
        if not any(matches_glob(rel_path, pattern) for rel_path in rel_paths):
            raise ValueError(f"arch.toml files glob matches nothing: {pattern!r}")
    return matched


def check_arch_rule(root: Path, rule: ArchRule) -> list[str]:
    """Check a single architecture rule across all matching files under root."""
    base = root.resolve()
    violations: list[str] = []
    for path in _resolve_rule_files(base, rule):
        rel_path = path.relative_to(base).as_posix()
        violations.extend(check_arch_text(rel_path, path.read_text(encoding="utf-8"), rule))
    return violations


def check_arch(root: Path, rules: Sequence[ArchRule]) -> list[str]:
    """Check all architecture rules across root and return violation lines."""
    violations: list[str] = []
    for rule in rules:
        violations.extend(check_arch_rule(root, rule))
    return violations
