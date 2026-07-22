# perf CLI — agent instructions

**Always run `perf` with `--json` and parse that output.** The pretty
terminal view (sparklines, color, human confirmation text) is lossy and NOT a
stable contract — it may change without notice. Never parse the pretty view;
only the `--json` payload (`schema_version`-carrying) is machine-safe.

`perf run` is persist-only: it exits `0` on success, `2` on a usage error,
and `3` on any runtime/tooling failure. It never exits `1` (that code is
reserved for `compare`/`budget-check` regressions).

## Project skills

Load these before working on the matching concern (they are complementary, not
overlapping — one contract plus three craft guides):

- `.claude/skills/perf-cli-standards/SKILL.md` — **the hard-rule contract**
  (hexagonal layering, frozen dataclasses + `Protocol` ports, SQLite
  transaction discipline, subprocess + SQL-injection safety, `--json` contract,
  exit codes, TDD layout, dependency policy, hard boundaries). Load before
  editing any Python under `src/perf/` or `tests/`.
- `.claude/skills/python-architecture/SKILL.md` — **shape & structure**: keep
  behavior local (a bug fixable in one file), resist premature abstraction, the
  port is the only seam. Load before adding a class/abstraction/pattern or
  deciding where code goes.
- `.claude/skills/python-testing/SKILL.md` — **testing craft**: fakes over
  mocks, never monkeypatch the code under test, property-based for pure math,
  golden/contract discipline. Load before writing tests.
- `.claude/skills/python-style/SKILL.md` — **style & tooling**: ruff + mypy,
  typing, docstrings-explain-why, stdlib-first. Load when formatting or setting
  up lint/type-check.

## Dev environment

- Python **3.11+**, `src/` layout, single installable package `perf`.
- Setup: create a venv, `pip install -e '.[dev]'`, run the CLI via the `perf`
  console script (entry point `perf.cli:main`).
- The local store (`*.db`) and CodeGraph index (`.codegraph/`) are
  gitignored — never commit them.

## Testing

- `pytest -q` runs the suite (layout: `tests/unit`, `tests/integration`,
  `tests/contract`, `tests/golden`).
- Lint/type-check (once wired, see `python-style`): `ruff check .`,
  `ruff format --check .`, `mypy src/perf`.
- Adapters test against recorded fixtures — never a live device/adb/maestro.

## PR guidelines

- Conventional-commit messages; **no AI attribution / Co-Authored-By** lines.
- Split large work into sequential, reviewable PRs (this capability shipped as
  PR1 foundation → PR2 store+adapters → PR3 app+CLI).
- Every PR keeps the suite green and adds tests for new behavior; run an
  adversarial review lens before merge.
