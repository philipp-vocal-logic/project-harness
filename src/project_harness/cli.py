"""Unified CLI entrypoint for project-harness with zero-noise output and POSIX exit codes."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from project_harness.arch import check_arch
from project_harness.config import (
    POLICY_DIR,
    git,
    load_arch,
    load_glossary,
    load_symbols,
    load_traceability,
)
from project_harness.core import check_local, check_merge
from project_harness.glossary import check_glossary
from project_harness.symbol import check_symbols
from project_harness.traceability import check_trace

POLICY_FILES: dict[str, str] = {
    "arch": "arch.toml",
    "glossary": "glossary.toml",
    "symbol": "symbols.toml",
    "trace": "traceability.toml",
}


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

    for cmd, desc in (
        ("arch", "Run architecture rules check (.policy/arch.toml)."),
        ("glossary", "Run glossary terminology check (.policy/glossary.toml)."),
        ("symbol", "Run symbol naming check (.policy/symbols.toml)."),
        ("trace", "Run traceability graph check (.policy/traceability.toml)."),
    ):
        subparsers.add_parser(cmd, help=desc)

    return parser


def _dispatch(args: argparse.Namespace) -> list[str]:
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
    if args.command == "check":
        if args.base is not None:
            return check_merge(root, args.base, args.branch)
        return check_local(root)
    policy_path = root / POLICY_DIR / POLICY_FILES[args.command]
    if args.command == "arch":
        return check_arch(root, load_arch(policy_path))
    if args.command == "glossary":
        return check_glossary(root, load_glossary(policy_path))
    if args.command == "symbol":
        return check_symbols(root, load_symbols(policy_path))
    return check_trace(root, load_traceability(policy_path))


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
