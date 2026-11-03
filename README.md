# project-harness (`harness`)

Minimal, zero-noise CLI that enforces `.policy/` rules (`arch`, `glossary`, `symbol`, `traceability`, `workflow`) and Git TDD merge gates using only the Python standard library.

The stable base branch (`main`) is trusted; a branch is checked against it before merging, using the rules stored **on the base branch**, so a branch cannot loosen the rules that judge it.

## Usage

Run `harness` anywhere inside a git work tree. It always reads `<work-tree root>/.policy/*.toml` and checks the files git knows about: tracked files plus untracked files not excluded by `.gitignore`.

```bash
# Run all built-in .policy/*.toml checks in-process and workflow.toml commands
harness check

# Merge check of HEAD against the stable base (CI runs this on pull requests)
harness check --base origin/main --branch "$BRANCH"

# Run individual built-in policy checkers
harness arch
harness glossary
harness symbol
harness trace
```

`--branch` defaults to the current branch; CI passes it because pull requests check out a detached merge commit.

### Merge check (`--base`)

Reads `.policy/*.toml` from the base ref, diffs `merge-base..HEAD` (`-z --no-renames`, so renames and non-ASCII paths are seen), then:

| Rule | Passes when | Exception |
|---|---|---|
| Protected paths | no file matching `protected_paths` changed | `policy/*` branch **and** every commit touching those paths has a `Reason: <why>` line |
| Red check | if `source_paths` changed: `test_command` on the branch tests with the base source exits in `red_exit_codes` | `refactor/*` branch **and** every commit touching those paths has a `Reason: <why>` line |
| Built-in policies & checks | all `.policy/*.toml` checkers and `workflow.checks` exit 0 on HEAD | — |

Protected paths and the red check always use the base rules. Built-in policies and checks use the base `.policy/` too, unless the branch's approved protected changes include `.policy/` files: then the branch is judged by its own `.policy/`, so policy format changes can merge.

The working tree must be clean. Every exception is traceable in git history:

```bash
git log --grep '^Reason:' -- .policy/ .github/   # why protected paths changed
git log --format='%h %an %s' -- .policy/ .github/ .githooks/ pyproject.toml
```

### Exit codes

- `0`: all checks pass (silent `stdout`).
- `1`: one or more violations (single-line diagnostics on `stdout`).
- `2`: configuration or git error (`stderr`, `[ERR_CONFIG]`).

## Adopting harness in a project

harness is installed as a tool; the project only holds `.policy/` (and optionally `trace/`).

**1. Add `.policy/workflow.toml`.** `checks` and `test_command` are plain commands, so the merge gate works for any language:

```toml
[workflow]
protected_paths = [".policy/**", ".github/**"]
checks = ["uv run pytest -q"]
source_paths = ["src/**"]
test_command = "uv run pytest -q"
red_exit_codes = [1, 2]
```

Add `arch.toml`, `glossary.toml`, `symbols.toml`, or `traceability.toml` only when you need them; missing files are skipped.

**2. Run the merge check in CI with a pinned harness version.** The pin lives in `.github/`, which is protected, so a branch cannot swap in a looser harness:

```yaml
# .github/workflows/harness.yml
name: harness
on:
  pull_request:
permissions:
  contents: read
jobs:
  merge-check:
    runs-on: ubuntu-26.04
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
      - uses: astral-sh/setup-uv@v10.3.3
      - name: Merge check against the stable base
        env:
          HEAD_REF: ${{ github.head_ref }}
          HARNESS: git+https://github.com/philipp-vocal-logic/project-harness@<tag-or-commit>
        run: uvx --from "$HARNESS" harness check --base "origin/$GITHUB_BASE_REF" --branch "$HEAD_REF"
```

**3. Configure the repository.** GitHub runs `pull_request` workflows from the branch under review, so a branch could edit or drop the job above. These settings are required for the gate to hold:

- Require the merge check through a ruleset ("Require workflows to pass before merging") that points at a workflow on a protected ref, or at least require its status check on `main`.
- Block force pushes to `main`.
- Use merge commits (or keep the `Reason:` line in squash messages) so the history stays intact.

## `.policy/` configuration

- `.policy/workflow.toml`: `protected_paths`, `checks`, and the red check (`source_paths`, `test_command`, `red_exit_codes`).
- `.policy/traceability.toml`: `harness trace` node chain (e.g. `Story -> Requirement`), statuses, TOML node locations, and coverage rules. A rule with `target_type = "Test"` requires the node's `tests` list to be non-empty.
- `.policy/arch.toml`: `harness arch` rules; each rule sets `max_lines`, `forbid` line regexes, or both.
- `.policy/glossary.toml`: `harness glossary` canonical terms and forbidden synonyms.
- `.policy/symbols.toml`: `harness symbol` forbidden subwords, canonical replacements, and `[[rules]]` naming schema patterns by symbol kind and visibility in Python identifiers (`.py` files only).

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
