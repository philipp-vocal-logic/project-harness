"""CLI entrypoint for project-harness with zero-noise output and POSIX exit codes."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from project_harness.core import (
    Workflow,
    check_commit_file,
    check_merge,
    check_stage,
    load_workflow,
    run_commands,
    run_workflow,
)


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser for the harness CLI."""
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Enforce .policy rules and configurable Git TDD workflows.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(".policy/workflow.toml"),
        help="Path to workflow TOML file (default: .policy/workflow.toml).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="Run stage, commit, or merge checks.")
    group = check_parser.add_mutually_exclusive_group()
    group.add_argument("--stage", help="Stage id to validate against current working tree.")
    group.add_argument("--commit-msg", type=Path, help="Commit message file for staged commit.")
    group.add_argument("--base", help="Base git ref for branch history and merge check.")
    check_parser.add_argument(
        "--staged", action="store_true", help="Inspect only staged files during --stage check."
    )

    run_parser = subparsers.add_parser("run", help="Run orchestrator TDD workflow for target.")
    run_parser.add_argument("target", help="Target node id (e.g. REQ-1).")
    return parser


def _resolve_root(config_path: Path) -> Path:
    resolved = config_path.resolve()
    return resolved.parent.parent if resolved.parent.name == ".policy" else resolved.parent


def _dispatch(args: argparse.Namespace, root: Path, workflow: Workflow) -> list[str]:
    if args.command == "run":
        return run_workflow(root, workflow, args.target)
    if args.commit_msg is not None:
        return check_commit_file(root, workflow, args.commit_msg)
    if args.stage is not None:
        return check_stage(root, workflow, args.stage, staged_only=args.staged)
    if args.base is not None:
        return check_merge(root, workflow, args.base)
    return run_commands(root, workflow.checks)


def main(argv: Sequence[str] | None = None) -> int:
    """Run harness CLI and return POSIX exit code (0=pass, 1=violations, 2=config error)."""
    args = build_parser().parse_args(argv)
    try:
        workflow = load_workflow(args.config)
        violations = _dispatch(args, _resolve_root(args.config), workflow)
    except (OSError, ValueError) as error:
        print(f"{args.config} [ERR_CONFIG]: {error}", file=sys.stderr)
        return 2
    for violation in violations:
        print(violation)
    return 1 if violations else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
