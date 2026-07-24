# Device-free `perf budget-check` demo

This directory lets you SEE `perf budget-check` GATE on a real regression
end-to-end — seeded history -> `SqlAnalyzer` -> `domain/budget.evaluate` ->
own pretty banner + versioned `--json` -> exit code `1` — **without a
physical or emulated device**, exactly like `examples/demo-compare/`.

`seed.py` reuses `examples/demo-compare/seed.py`'s recorded fixtures and
`seed_into()` function verbatim (the SAME regression story), replayed into
this demo's OWN local `perf.db` file:

- 4 baseline commits (`c1`..`c4`) — a stable, low-noise history (`checkout`
  ~800ms, `ttfp` ~410-430ms).
- 1 latest commit (`head`) — a CLEAR `checkout` duration regression
  (~800ms -> ~1300ms), while `ttfp` and the Flashlight aggregates
  (`fps_avg`, `ram_avg_mb`, ...) stay stable.

## Seed it

From the repo root:

```sh
python examples/demo-budget-check/seed.py
```

(Or `./.venv/bin/python examples/demo-budget-check/seed.py` from the dev
venv.) This (re)creates `examples/demo-budget-check/perf.db` from scratch —
safe to re-run any time, and independent of `examples/demo-compare/perf.db`.

## Run it

Note the global flags (`--config`, `--json`, `--no-color`) go **before** the
`budget-check` subcommand — same convention as `perf run`/`perf compare`:

```sh
# Pretty (human) output — the gate banner, default fail-open mode
perfvibe --config examples/demo-budget-check/perf.toml budget-check demo
echo "exit code: $?"    # -> 1 (a confirmed regression FAILS the gate)

# --strict: the SAME confirmed regression still fails (strict never changes
# an already-confirmed regression or a clean pass — it only flips the
# insufficient-data-class fail-open default to fail-closed)
perfvibe --config examples/demo-budget-check/perf.toml budget-check demo --strict

# Machine --json contract (budget_check_v1, schema_version=1)
perfvibe --json --config examples/demo-budget-check/perf.toml budget-check demo

# Single-metric detail view (larger chart, git context on the regression)
perfvibe --config examples/demo-budget-check/perf.toml budget-check demo --metric checkout
```

(Not installed yet? Use `python perfvibe-cli.py --config
examples/demo-budget-check/perf.toml budget-check demo` from the repo root,
or `./.venv/bin/perfvibe ...` from the dev venv.)

## What you should see

- Pretty output: EVERY metric shown (not only the offender) — `checkout`
  marked `REGRESSION` (glyph `✗` + the uppercase status word, legible even
  with `--no-color`/non-TTY), `ttfp`/Flashlight aggregates `stable`. A final
  gate banner: `✗  GATE FAILED   ·   1 metric regressed   ·   exit 1`.
- `--json`: top-level `gate_status: "fail"`, `offending_metrics: ["checkout"]`,
  and a flat `verdicts[]` array where `checkout`'s entry carries
  `"gated": true` — the pretty banner text never appears in `--json` output.
- Exit code `1` in every invocation above except a hypothetical all-stable
  history (there is no all-stable demo here — see `examples/demo-compare/`
  for that story, which `budget-check` would gate `pass`/exit `0` on).

No device, no `adb`, no `maestro`, no `flashlight` binary is invoked — only
the recorded fixture files under `examples/demo-compare/fixtures/` are read
(via `seed_into()`), and `budget-check` itself performs no device I/O at all
(it only reads the local `.db`, plus one render-time `git log` call for the
commit subject in `--metric`/`--verbose` views — fail-graceful to sha-only
outside a git repository).

**A note on `--metric`'s HEAD marker/subject**: `seed.py` labels the seeded
"latest" run with the literal commit string `"head"` (a demo placeholder,
same as `examples/demo-compare/`) — it does NOT match your checkout's actual
git `HEAD` sha. At invocation time `budget-check` resolves the REAL current
git commit (so it works correctly on any machine/checkout, with or without a
device attached), so the `--metric checkout` detail chart's `x-axis`/`HEAD`
caret and the "baseline commits" exclusion line up against THAT real sha, not
the seeded `"head"` label — you'll see a real commit subject from THIS
repository's own history rather than the fixture story's. This is a property
of the demo's git-commit seeding, not a `budget-check` bug: the summary view,
`--strict`, and `--json` are all commit-label-independent and always match
the story above exactly.
