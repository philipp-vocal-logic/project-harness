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

## Development

```bash
git config core.hooksPath .githooks

uv run harness check
uv run pytest
```
