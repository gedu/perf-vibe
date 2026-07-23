# perf CLI — agent instructions

**Always run `perfvibe` with `--json` and parse that output.** The pretty
terminal view (sparklines, color, human confirmation text) is lossy and NOT a
stable contract — it may change without notice. Never parse the pretty view;
only the `--json` payload (`schema_version`-carrying) is machine-safe.

`perfvibe run` is persist-only and `perfvibe compare` is show-only: both exit
`0` on success, `2` on a usage error, and `3` on any runtime/tooling failure.
**Neither ever exits `1`** — a `compare` regression still exits `0`, because
`compare` reports and does not gate. Exit `1` is reserved for a future
`budget-check` CI gate. Never treat a non-zero exit as "regression found";
read the verdict out of the `--json` payload.

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
- Setup: create a venv, `pip install -e '.[dev]'`, run the CLI via the
  `perfvibe` console script (entry point `perf.cli:main`; command renamed from
  `perf` to avoid colliding with the Linux kernel profiler). Public install is
  a `pipx`-based `curl | bash` one-liner (see `install.sh` / README).
- The local store (`*.db`), CodeGraph index (`.codegraph/`) and AI runtime
  state (`.atl/`) are gitignored — never commit them.

## Skill registry (local, optional)

`.atl/skill-registry.md` is a generated index of every skill visible on the
current machine, used by orchestrators to pick which skills to hand to a
sub-agent. It records **absolute paths into the current user's home
directory**, so it is per-checkout state and is deliberately not committed —
a registry generated on one machine is broken on every other one.

Generate your own with `gentle-ai skill-registry refresh`. If you do not have
that tool, you do not need it: the four project skills above are the portable
contract, they live in this repo, and the repo-relative paths in the
**Project skills** section are the only ones an agent should be handed.

## Persistent memory (optional)

The SDD workflow in `openspec/` can mirror its artifacts into Engram, a
persistent memory tool. `.engram/config.json` binds this repo to the project
name `perf-vibe`; it is the only file the engram binary reads to resolve the
project, which is why it is committed (it holds no machine-specific state).
`openspec/config.yaml` repeats the same name under `engram_project` for human
readers — keep both in sync if the project is ever renamed.

**Engram memories do not travel with a clone.** A fresh checkout has no
history for this project no matter what topic keys the docs mention. The
committed `openspec/` and `docs/specs/` files are the portable source of
truth; never block on a memory lookup that comes back empty.

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
