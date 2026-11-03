"""Architecture policy checker for file line limits, forbidden patterns, and forbidden files."""

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
        if any(regex.search(line) for regex in rule.forbid):
            violations.append(
                f"{rel_path}:{line_no}:1 [ERR_ARCH]: {line.strip()} (Fix: {rule.fix})"
            )
    return violations


def _resolve_rule_files(root: Path, rule: ArchRule) -> list[Path]:
    matched = walk_files(root, rule.files, rule.ignore)
    if rule.forbids_files:
        return matched
    rel_paths = [path.relative_to(root.resolve()).as_posix() for path in matched]
    for pattern in rule.files:
        if not any(matches_glob(rel, pattern) for rel in rel_paths):
            raise ValueError(f"arch.toml files glob matches nothing: {pattern!r}")
    return matched


def check_arch_rule(root: Path, rule: ArchRule) -> list[str]:
    """Check a single architecture rule across all matching files under root."""
    base = root.resolve()
    paths = _resolve_rule_files(base, rule)
    violations: list[str] = []
    for path in paths:
        rel = path.relative_to(base).as_posix()
        if rule.forbids_files:
            violations.append(f"{rel}:1:1 [ERR_ARCH]: forbidden file (Fix: {rule.fix})")
        else:
            text = path.read_text(encoding="utf-8")
            violations.extend(check_arch_text(rel, text, rule))
    return violations


def check_arch(root: Path, rules: Sequence[ArchRule]) -> list[str]:
    """Check all architecture rules across root and return violation lines."""
    violations: list[str] = []
    for rule in rules:
        violations.extend(check_arch_rule(root, rule))
    return violations
