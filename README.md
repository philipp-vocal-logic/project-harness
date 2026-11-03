# project-harness (`harness`)

Minimal, zero-noise CLI that enforces `.policy/` rules (`arch`, `glossary`, `symbol`, `traceability`, `workflow`) and Git TDD merge gates using only the Python standard library.

The stable base branch (`main`) is trusted; a branch is checked against it before merging, using the rules stored **on the base branch**, so a branch cannot loosen the rules that judge it.

## Usage

```bash
# Run all built-in .policy/*.toml checks in-process and workflow.toml commands
uv run harness check

# Merge check of HEAD against the stable base (CI runs this on pull requests)
uv run harness check --base origin/main --branch "$BRANCH"

# Run individual built-in policy checkers
uv run harness arch
uv run harness glossary
uv run harness symbol
uv run harness trace
```

`--branch` defaults to the current branch; CI passes it because pull requests check out a detached merge commit.

### Merge check (`--base`)

Reads `.policy/*.toml` from the base ref, diffs `merge-base..HEAD` (`-z --no-renames`, so renames and non-ASCII paths are seen), then:

| Rule | Passes when | Exception |
|---|---|---|
| Protected paths | no file matching `protected_paths` changed | `policy/*` branch **and** every commit touching those paths has a `Reason: <why>` line |
| Red check | if `source_paths` changed: `test_command` on the branch tests with the base source exits in `red_exit_codes` | `refactor/*` branch **and** every commit touching those paths has a `Reason: <why>` line |
| Built-in policies & checks | all base `.policy/*.toml` checkers and `workflow.checks` exit 0 on HEAD | — |

The working tree must be clean. Every exception is traceable in git history:

```bash
git log --grep '^Reason:' -- .policy/ .github/   # why protected paths changed
git log --format='%h %an %s' -- .policy/ .github/ .githooks/ pyproject.toml
```

Use merge commits (or keep the `Reason:` line in squash messages) and disallow force pushes to `main` so the history stays intact.

### Exit codes

- `0`: all checks pass (silent `stdout`).
- `1`: one or more violations (single-line diagnostics on `stdout`).
- `2`: configuration or git error (`stderr`, `[ERR_CONFIG]`).

## `.policy/` configuration

- `.policy/workflow.toml`: `protected_paths`, `checks`, and the red check (`source_paths`, `test_command`, `red_exit_codes`).
- `.policy/traceability.toml`: `harness trace` node chain (e.g. `Story -> Requirement`), statuses, TOML node locations, and coverage rules. A rule with `target_type = "Test"` requires the node's `tests` list to be non-empty.
- `.policy/arch.toml`: `harness arch` module line limits, forbidden files, and forbidden line regexes.
- `.policy/glossary.toml`: `harness glossary` canonical terms and forbidden synonyms.
- `.policy/symbols.toml`: `harness symbol` forbidden subwords and canonical replacements in Python identifiers (`.py` files only).

### Traceability

Stories and requirements are TOML files. Requirements link to their tests, so test code needs no annotations and works in any language:

```toml
# trace/requirements/REQ-RED.toml
id = "REQ-RED"
type = "Requirement"
status = "APPROVED"
links = ["US-1"]
tests = ["tests/test_integration.py::test_merge_check_rejects_source_change_without_red_test"]
```

A test link `<path>::<name>[::<name>...]` resolves when the file exists and contains every name as a whole word; otherwise `harness trace` reports `[ERR_DANGLING_TEST]`.

## Agents and TDD

Tell agents to write a failing test first and to make it pass without editing tests. The red check is the backstop: source changes only merge with tests that fail against the base source.

## Development

```bash
git config core.hooksPath .githooks   # optional local feedback; CI is the gate

uv run harness check
uv run pytest
```
