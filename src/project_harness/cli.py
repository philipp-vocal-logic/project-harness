"""Unified CLI entrypoint for project-harness with zero-noise output and POSIX exit codes."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from project_harness.config import git
from project_harness.core import check_local, check_merge


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser for the unified harness CLI."""
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Enforce .policy rules of the current git work tree and check branches "
        "against a stable base.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check", help="Run policy checks or the merge check.")
    check_parser.add_argument(
        "--base", help="Base git ref; check HEAD against it using the base policy rules."
    )
    check_parser.add_argument(
        "--branch", help="Branch name for policy/* and refactor/* exceptions (default: current)."
    )
    return parser


def _dispatch(args: argparse.Namespace) -> list[str]:
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
    if args.base is not None:
        return check_merge(root, args.base, args.branch)
    return check_local(root)


def main(argv: Sequence[str] | None = None) -> int:
    """Run harness CLI and return POSIX exit code (0=pass, 1=violations, 2=config error)."""
    args = build_parser().parse_args(argv)
    try:
        violations = _dispatch(args)
    except (OSError, ValueError, SyntaxError) as error:
        print(f"harness [ERR_CONFIG]: {error}", file=sys.stderr)
        return 2
    for violation in violations:
        print(violation)
    return 1 if violations else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
