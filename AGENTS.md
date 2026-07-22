# perf CLI — agent instructions

**Always run `perf` with `--json` and parse that output.** The pretty
terminal view (sparklines, color, human confirmation text) is lossy and NOT a
stable contract — it may change without notice. Never parse the pretty view;
only the `--json` payload (`schema_version`-carrying) is machine-safe.

`perf run` is persist-only: it exits `0` on success, `2` on a usage error,
and `3` on any runtime/tooling failure. It never exits `1` (that code is
reserved for `compare`/`budget-check` regressions).

## Project skills

- `.claude/skills/perf-cli-standards/SKILL.md` — the coding contract for this
  repo (hexagonal layering, frozen dataclasses + `Protocol` ports, SQLite
  transaction discipline, subprocess safety, `--json` contract, exit codes,
  TDD testing layout, dependency policy, hard boundaries). Load this before
  writing or editing any Python under `src/perf/` or `tests/`.
