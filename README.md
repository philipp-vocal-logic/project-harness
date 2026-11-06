# project-harness (`harness`)

Minimal, zero-noise Python stdlib CLI that enforces Git TDD merge gates and `.policy/` rules (`architecture`, `symbol`, `traceability`, `workflow`).

`harness` checks each branch before merging against the `.policy/` rules stored **on the base branch**, so a branch cannot weaken the rules that judge it.

## Usage

Run `harness` anywhere inside a git work tree. It reads `<work-tree root>/.policy/*.toml` and checks all tracked files plus untracked files not excluded by `.gitignore`.

```bash
# Run all built-in .policy/*.toml checks in-process and workflow.toml commands
harness check

# Merge check of HEAD against the base branch (CI runs this on pull requests)
harness check --base origin/main --branch "$BRANCH"
```

`--branch` defaults to the current branch. CI passes `--branch` explicitly because pull requests check out a detached merge commit.

### Merge check (`--base`)

`harness` requires a clean working tree, reads `.policy/*.toml` from the base branch, and diffs `merge-base..HEAD` with `-z --no-renames` so renames and non-ASCII paths remain visible:

| Rule | Passes when | Exception |
|---|---|---|
| Protected paths & requirements | no file matching `protected_paths` changed, and no base requirement was deleted, renamed, or had `title`, `acceptance`, or test links weakened | `policy/*` branch **and** every commit touching those paths has a `Reason: <why>` line |
| Red check | when `source_paths` change, `test_command` runs the branch tests against the base source and exits in `red_exit_codes` | `refactor/*` or `policy/*` branch **and** every commit touching those paths has a `Reason: <why>` line |
| Built-in checks & `workflow.checks` | all built-in `.policy/*.toml` checks and `workflow.checks` commands exit 0 on HEAD | — |

Protected paths, the red check, and all built-in and workflow checks evaluate against the base `.policy/` by default. `harness` switches to the branch's own `.policy/` for built-in and workflow checks only when an approved `policy/*` branch modifies `.policy/`, so policy format changes can merge.

Every exception remains traceable in git history:

```bash
git log --grep '^Reason:' -- .policy/ .github/   # why protected paths changed
git log --format='%h %an %s' -- .policy/ .github/ .githooks/ pyproject.toml
```

### Exit codes

- `0`: all checks pass (silent `stdout`).
- `1`: one or more violations (single-line diagnostics on `stdout`).
- `2`: configuration or git error (`stderr`, `[ERR_CONFIG]`).

## Adopting harness in a project

Install `harness` as an external tool so the project only holds `.policy/` (and optionally `trace/`).

**1. Add `.policy/workflow.toml`.** `checks` and `test_command` run plain shell commands, so the merge gate works for any language:

```toml
[workflow]
protected_paths = [".policy/**", ".github/**"]
checks = ["uv run pytest -q"]
source_paths = ["src/**"]
test_command = "uv run pytest -q"
red_exit_codes = [1, 2]
```

Add `architecture.toml`, `symbols.toml`, or `traceability.toml` only when you need them because `harness` skips missing policy files.

**2. Run the merge check in CI with a pinned harness version.** Protecting `.github/` locks this pin so a branch cannot swap in a looser harness:

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
      - name: Merge check against the base branch
        env:
          HEAD_REF: ${{ github.head_ref }}
          HARNESS: git+https://github.com/philipp-vocal-logic/project-harness@<tag-or-commit>
        run: uvx --from "$HARNESS" harness check --base "origin/$GITHUB_BASE_REF" --branch "$HEAD_REF"
```

**3. Configure the repository.** GitHub runs `pull_request` workflows from the branch under review, so a branch could edit or drop the job above. Enforce these repository settings so the gate holds:

- Require the merge check on `main` via a status check or a ruleset that points to a protected workflow ("Require workflows to pass before merging").
- Block force pushes to `main`.
- Use merge commits—or keep the `Reason:` line when squash-merging—so the history stays intact.

## `.policy/` configuration

- `.policy/workflow.toml`: `protected_paths`, `checks`, and the red check (`source_paths`, `test_command`, `red_exit_codes`).
- `.policy/architecture.toml`: `[[rules]]` binding `files` globs to `max_lines`, `forbid` line regexes, or both, with a `fix` message:
  ```toml
  [[rules]]
  files = ["src/**/*.py"]
  max_lines = 300
  forbid = ["^\\s*(from|import)\\s+pytest\\b"]
  fix = "Keep source modules under 300 lines and free of test imports."
  ```
- `.policy/symbols.toml`: Python AST identifier checks for `.py` files across three sections:
  - `[settings]`: `forbidden_subwords` list.
  - `[replacements]`: `<canonical> = ["<forbidden>", ...]` mappings.
  - `[[rules]]`: binds `files`, `kinds`, and optional `visibility` to a regex `pattern` and `fix` message (`kinds` in `["module", "class", "function", "method"]` and `visibility` in `["all", "public", "private"]`):
  ```toml
  [settings]
  forbidden_subwords = ["data", "info", "temp", "util"]

  [replacements]
  config = ["cfg"]

  [[rules]]
  files = ["src/**/*.py"]
  kinds = ["function"]
  visibility = "public"
  pattern = "^(parse|load|check)_[a-z0-9_]+$"
  fix = "Prefix public functions with 'parse_', 'load_', or 'check_'."
  ```
- `.policy/traceability.toml`: `requirements` and `tests` glob lists for bidirectional requirement-to-test checking:
  ```toml
  [traceability]
  requirements = ["trace/*.toml"]
  tests = ["tests/**/test_*.py"]
  ```

### Traceability

Requirements are flat TOML files under `trace/` with an `id`, `title`, single-line `acceptance` criteria, and `tests`. Each requirement must link to at least one existing test, and at least one requirement must link every test in `tests/`. `harness` discovers both Python AST tests and C Unity tests (`def test_*`, `class Test*`, and `void test_*(void)`):

```toml
# trace/REQ-RED.toml
id = "REQ-RED"
title = "Require source changes to come with tests that fail against the base source"
acceptance = [
    "Require workflow.test_command to exit in workflow.red_exit_codes against merge-base source when workflow.source_paths change.",
    "Exempt refactor/* and policy/* branches when every commit touching workflow.source_paths contains a 'Reason: <why>' line.",
]
tests = ["tests/test_integration.py::test_merge_check_rejects_source_change_without_red_test"]
```

On `feat/*`, `fix/*`, and `refactor/*` branches, agents and developers can add new requirements, add `acceptance` criteria, and add or rename test links. Deleting a requirement, editing its `title`, removing or editing base `acceptance` criteria, or reducing its linked tests requires a `policy/*` branch with a `Reason: <why>` line.

## Agents and TDD

Tell agents to write a failing test first and to make it pass without editing tests. The red check enforces this workflow because source changes only merge when tests fail against the base source.

Run one `git worktree` per requirement when developing orthogonal requirements in parallel, and seed its `trace/REQ-*.toml` on the branch (`git worktree add -b feat/req-a ../wt-req-a main`). Rebase each subsequent worktree onto `main` before fast-forward merging (`git rebase main && harness check --base main`).

## Development

```bash
git config core.hooksPath .githooks   # optional local feedback; CI is the gate

uv run harness check
uv run pytest
```
