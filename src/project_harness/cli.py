"""CLI entrypoint for project-harness with zero-noise output and POSIX exit codes."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from project_harness.config import load_workflow
from project_harness.core import check_merge, run_commands


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser for the harness CLI."""
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Enforce .policy rules and check branches against a stable base.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(".policy/workflow.toml"),
        help="Path to workflow TOML file (default: .policy/workflow.toml).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check", help="Run workflow checks or the merge check.")
    check_parser.add_argument(
        "--base", help="Base git ref; check HEAD against it using the base workflow rules."
    )
    check_parser.add_argument(
        "--branch", help="Branch name for policy/* and refactor/* exceptions (default: current)."
    )
    return parser


def _resolve_root(config_path: Path) -> Path:
    resolved = config_path.resolve()
    return resolved.parent.parent if resolved.parent.name == ".policy" else resolved.parent


def _dispatch(args: argparse.Namespace) -> list[str]:
    root = _resolve_root(args.config)
    if args.base is not None:
        config_rel = args.config.resolve().relative_to(root).as_posix()
        return check_merge(root, config_rel, args.base, args.branch)
    return run_commands(root, load_workflow(args.config).checks)


def main(argv: Sequence[str] | None = None) -> int:
    """Run harness CLI and return POSIX exit code (0=pass, 1=violations, 2=config error)."""
    args = build_parser().parse_args(argv)
    try:
        violations = _dispatch(args)
    except (OSError, ValueError) as error:
        print(f"{args.config} [ERR_CONFIG]: {error}", file=sys.stderr)
        return 2
    for violation in violations:
        print(violation)
    return 1 if violations else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
