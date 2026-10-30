# project-harness (`harness`)

Minimal, zero-noise CLI project harness that enforces `.policy/` rules and configurable Git TDD workflows using only the Python standard library and existing checker CLIs (`trace-check`, `architecture`, `glossary`, `symbol`, `ruff`, `pytest`).

## Usage

```bash
# Run all configured project checks in .policy/workflow.toml
uv run harness check

# Validate working tree (or --staged) against a specific TDD workflow stage
uv run harness check --stage red-test
uv run harness check --stage implementer --staged

# Validate staged commit via commit-msg file (infers stage from commit prefix)
uv run harness check --commit-msg .git/COMMIT_EDITMSG

# Validate branch commit history (plan -> test -> feat -> review), scopes, and merge checks
uv run harness check --base origin/main

# Execute automated orchestrator TDD workflow across all stages for a target node
uv run harness run REQ-1
```

### Exit Codes

- `0`: All policy, scope, history, and command checks pass (silent `stdout`).
- `1`: One or more violations found (printed to `stdout` as single-line diagnostics).
- `2`: Configuration or Git execution error (printed to `stderr` with `[ERR_CONFIG]`).

## `.policy/` Configuration

All project policy files live in `.policy/`:

- `.policy/workflow.toml`: Defines protected paths, merge checks, orchestrator command, and ordered TDD stages (`planner`, `red-test`, `implementer`, `reviewer`).
- `.policy/traceability.toml`: `trace-check` schema chain (`Story -> Requirement -> TestCase`), statuses, and rules.
- `.policy/arch.toml`: `architecture` module line limits and forbidden import rules.
- `.policy/glossary.toml`: `glossary` canonical terms and forbidden synonyms.
- `.policy/symbols.toml`: `symbol` forbidden subwords and canonical replacements.

## Commit Types

| Commit | Allowed changes | Checks |
|---|---|---|
| Stage (`plan`, `test`, `feat`, `review`) `(<target>)` required | Stage `allow_write` globs, never protected paths | Stage `checks`; red-test `fail_checks` must exit with `fail_exit_codes` (`[1, 2]`, so a collection error for a not-yet-implemented symbol counts as red) |
| `workflow.commit_types` (`fix`, `docs`, `chore`, …) | Anything except protected paths | `workflow.checks` |
| `policy(<target>): <summary>` | **Only** protected paths | `workflow.checks`; message body must contain a `Reason: <why>` line |
| `Merge …` / `Revert …` | Skipped by commit-msg; merge commits skipped by the merge gate | — |

Policy update example (no `--no-verify` needed):

```bash
git add .policy/arch.toml
git commit -m "policy(arch): raise core.py line limit" -m "Reason: merge gate grew core.py"
git log --grep '^Reason:' -- .policy/   # audit trail of policy changes
```

The merge gate (`--base`) requires the full stage sequence only when a branch contains stage commits; branches with only non-stage or policy commits skip it.

## Development

```bash
git config core.hooksPath .githooks

uv run harness check
uv run pytest
```
