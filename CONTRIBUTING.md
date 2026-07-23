# Contributing

## Setup

```bash
python3.11 -m venv .venv          # any Python 3.11+ works
./.venv/bin/pip install -e '.[dev]'
```

No `python3.11`? Try `python3.12`/`python3.13`, or `brew install python@3.11`
on macOS. `install.sh` does this discovery automatically.

## Before opening a PR

Run what CI runs:

```bash
./.venv/bin/ruff check .
./.venv/bin/ruff format --check .
./.venv/bin/mypy src/perf
./.venv/bin/pytest -q --cov=perf
```

`ruff format` (without `--check`) applies the formatting. Never hand-format.

Coverage has a floor of 93% — CI fails below it. Raise the floor when coverage
genuinely improves; don't lower it to turn a build green.

### If the compare scale test fails on timing

`tests/integration/test_compare_perf.py` asserts a wall-clock budget calibrated
for an idle machine. The same code measures ~45ms idle and 1600ms+ on a busy
one. If only that assertion fails, re-run it on a quiet machine or skip it with
`PERF_COMPARE_BUDGET_MS=0` — which is how CI runs it. The O(1) statement-count
assertion in the same test is deterministic and always enforced; **that** one
failing is a real regression.

## Conventions

- **Conventional commits** (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`,
  `build:`, `chore:`). No AI attribution or `Co-Authored-By` lines.
- **Tests come first.** This project runs strict TDD — write the failing test,
  watch it fail for the right reason, then make it pass. See
  `.claude/skills/python-testing/SKILL.md`.
- **Architecture is hexagonal** and enforced: `domain/` is pure (no I/O, no
  adapter imports) → `application/` → `adapters/` → `cli/`. Ports are
  `typing.Protocol` in `domain/ports.py`. See
  `.claude/skills/perf-cli-standards/SKILL.md`.
- **Dependencies are deliberate.** `typer` is the only runtime dependency;
  everything else is stdlib. Justify any addition.
- **`--json` is a contract**, the pretty output is not. Contract tests assert
  `schema_version` stability — don't break them casually.

## Exit codes

`0` success · `2` usage error · `3` runtime/tooling failure. Neither `run` nor
`compare` ever exits `1`: `compare` is show-only, so a regression still exits
`0`. Exit `1` is reserved for a future `budget-check` gate. Don't add an
exit-`1` path without changing that contract deliberately.

## Working with AI agents

[`AGENTS.md`](./AGENTS.md) is the entry point — it routes to the four project
skills in `.claude/skills/` by task. Agents should read it before writing code.
