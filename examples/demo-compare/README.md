# Device-free `perf compare` demo

This directory lets you SEE `perf compare` compute a real, direction-aware
verdict end-to-end — seeded history -> `SqlAnalyzer` -> sparkline pretty
output + versioned `--json` -> exit code — **without a physical or
emulated device**, exactly like `examples/demo-run/`.

`seed.py` replays two recorded fixture pairs through the REAL `perf run`
pipeline (`ReplayDriver` -> marker/Flashlight parse -> `SqliteStore`) five
times, varying ONLY the commit (via a fake `RunContextProvider` — never
the analyzer):

- 4 baseline commits (`c1`..`c4`) replaying `fixtures/baseline-*` — a
  stable, low-noise history (`checkout` ~800ms, `ttfp` ~410-430ms).
- 1 latest commit (`head`) replaying `fixtures/regression-*` — a CLEAR
  `checkout` duration regression (~800ms -> ~1300ms), while `ttfp` and the
  Flashlight aggregates (`fps_avg`, `ram_avg_mb`, ...) stay stable.

## Seed it

From the repo root:

```sh
python examples/demo-compare/seed.py
```

(Or `./.venv/bin/python examples/demo-compare/seed.py` from the dev venv.)
This (re)creates `examples/demo-compare/perf.db` from scratch — safe to
re-run any time.

## Run it

Note the global flags (`--config`, `--json`, `--no-color`) go **before**
the `compare` subcommand — same convention as `perf run`:

```sh
# Pretty (human) output — sparklines + the config sanity label
perfvibe --config examples/demo-compare/perf.toml compare demo

# Machine --json contract (schema_version=1)
perfvibe --json --config examples/demo-compare/perf.toml compare demo
```

(Not installed yet? Use `python perfvibe-cli.py --config
examples/demo-compare/perf.toml compare demo` from the repo root, or
`./.venv/bin/perfvibe ...` from the dev venv.)

Both commands exit `0` — a regression verdict is INFORMATIONAL in this
slice (the CI-gating `budget-check` that exits `1` on regression is a
DEFERRED follow-up; see `openspec/specs/compare.md`).

## What you should see

- `checkout` classified `regression` (visually emphasized — a leading `!`
  and the `REGRESSION` word even with `--no-color`/non-TTY).
- `ttfp` and the Flashlight aggregates classified `stable`.
- A one-line config sanity label footer (`✓ reasonable — X of N runs would
  flag` / `⚠ too loose` / `⚠ too strict`) in BOTH the pretty output and the
  `--json` payload — informational only, it never changes the exit code.

No device, no `adb`, no `maestro`, no `flashlight` binary is invoked — only
the recorded fixture files under `fixtures/` are read, and `compare` itself
performs no device I/O at all (it only reads the local `.db`).
