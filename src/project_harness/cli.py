"""Unified CLI entrypoint for project-harness with zero-noise output and POSIX exit codes."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from project_harness.arch import check_arch
from project_harness.config import (
    load_arch,
    load_glossary,
    load_symbols,
    load_traceability,
    resolve_root,
)
from project_harness.core import check_local, check_merge
from project_harness.glossary import check_glossary
from project_harness.symbol import check_symbols
from project_harness.traceability import check_trace

DEFAULT_CONFIGS: dict[str, Path] = {
    "check": Path(".policy/workflow.toml"),
    "arch": Path(".policy/arch.toml"),
    "glossary": Path(".policy/glossary.toml"),
    "symbol": Path(".policy/symbols.toml"),
    "trace": Path(".policy/traceability.toml"),
}


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser for the unified harness CLI."""
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Enforce .policy rules and check branches against a stable base.",
    )
    parser.add_argument("--config", type=Path, default=None, help="Path to policy TOML file.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="Run policy checks or the merge check.")
    check_parser.add_argument(
        "--config", type=Path, default=argparse.SUPPRESS, help="Path to workflow TOML file."
    )
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
        sub = subparsers.add_parser(cmd, help=desc)
        sub.add_argument("--config", type=Path, default=argparse.SUPPRESS, help=desc)

    return parser


def _config_path(args: argparse.Namespace) -> Path:
    return args.config if args.config is not None else DEFAULT_CONFIGS[args.command]


def _dispatch(args: argparse.Namespace, config_path: Path) -> list[str]:
    root = resolve_root(config_path)
    if args.command == "check":
        if args.base is not None:
            config_rel = config_path.resolve().relative_to(root).as_posix()
            return check_merge(root, config_rel, args.base, args.branch)
        return check_local(root, config_path)
    if args.command == "arch":
        return check_arch(root, load_arch(config_path))
    if args.command == "glossary":
        return check_glossary(root, load_glossary(config_path))
    if args.command == "symbol":
        return check_symbols(root, load_symbols(config_path))
    return check_trace(root, load_traceability(config_path))


def main(argv: Sequence[str] | None = None) -> int:
    """Run harness CLI and return POSIX exit code (0=pass, 1=violations, 2=config error)."""
    args = build_parser().parse_args(argv)
    config_path = _config_path(args)
    try:
        violations = _dispatch(args, config_path)
    except (OSError, ValueError, SyntaxError) as error:
        print(f"{config_path} [ERR_CONFIG]: {error}", file=sys.stderr)
        return 2
    for violation in violations:
        print(violation)
    return 1 if violations else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
