# Command reference

Full per-command detail for `perfvibe`. For the big picture start at the
[README](../README.md); for flow configuration see
[`configuring-flows.md`](./configuring-flows.md).

## Conventions that apply to every command

- **Global flags go BEFORE the subcommand:** `perfvibe --json --config x.toml run demo`,
  never `perfvibe run demo --json`. The global flags are `--json`, `--no-color`
  (also honors `NO_COLOR` + TTY detection), `--db` (also honors `PERF_DB`), and
  `--config`.
- **Always parse `--json`, never the pretty view.** Every `--json` payload carries a
  `schema_version` integer. Today: `run`, `compare`, `compare-all`, `budget-check`,
  and `history` payloads are `schema_version = 1`; `init` is `schema_version = 2`
  (bumped when `flows_pruned` was added). Branch on the field — don't assume a
  constant.
- **Exit codes** are uniform: `0` success · `1` **`budget-check` gate only** · `2`
  usage error · `3` runtime/tooling failure. Only `budget-check` ever returns `1`.

---

## `run` — measure and persist

```
perfvibe run <flow> [n] [--restart] [--device <serial>]
```

Drives the flow's Maestro script `n` times (default from config), parses in-app
`[PERF]` markers from logcat and the Flashlight system samples (FPS/CPU/RAM), and
persists exactly one run to the local SQLite store. `--restart` cold-starts the app
between iterations; `--device` pins a specific serial. Persist-only: it **never**
exits `1`.

```text
✓ perf run complete — run #1
  flow:       demo
  device:     unknown|unknown|physical
  mode:       warm (n=2)
  source:     local:eduardograciano
  commit:     8317ae38a527f1f811936c3b5243ddca81dfa33f
  markers:
    checkout: n=2 avg=801.0ms
    ttfp: n=2 avg=417.5ms
  flashlight (per-iteration aggregates):
    fps avg: 57.4   ram peak: 222.5MB   cpu avg: 34.3%
```

**`--json`** → run payload (`schema_version = 1`) with the run's context (flow,
device key, mode, source `local:$USER` or `ci`, git commit), the marker measures, and
the Flashlight aggregates.

---

## `compare` — verdict vs. history

```
perfvibe compare <flow>...        # one or more flows
perfvibe compare --all            # every config-known flow, sorted
perfvibe compare                  # no args on a TTY: interactive picker
```

Reads the local store and shows a **per-metric, direction-aware** verdict against a
**median-by-commit baseline** — for each metric it reports the latest value vs. the
baseline, the delta, a direction arrow, a `STABLE`/`REGRESSION` classification, and a
sparkline trend. It is **show-only**: a regression is informational and still exits
`0`. (The gate that fails the build is [`budget-check`](#budget-check--the-ci-gate).)

```text
! checkout                 1310.0 vs 812.0      ms   ↑   +61.3%  REGRESSION       ▁▁▁▁█
  ttfp                       421.0 vs 430.0     ms   ↓    -2.1%  STABLE           ████▁
  ram_peak_mb                205.0 vs 206.0     mb   ↓    -0.5%  STABLE           ████▁
! total_time_ms            1310.0 vs 805.0      ms   ↑   +62.7%  REGRESSION       ▁▁▁▁█
  fps_avg                     58.1 vs 58.2      fps  ↓    -0.2%  STABLE           ████▁

✓ reasonable — 0 of 4 runs would flag
note: 1 run(s) excluded from baseline: 1 on the current commit — commit your changes to grow history
```

- **Multiple flows / `--all`:** a flow with no history is warned and skipped, not an
  error. `--all` compares every flow in `perfvibe.toml`, sorted.
- **Interactive picker:** with no flow args on an interactive terminal, an fzf-style
  picker opens — type to filter, `↑`/`↓` to move, `Tab` to multi-select, `Ctrl-A`
  to select all, `Enter` to run, `Esc` to cancel. In `--json` or non-interactive
  contexts you **must** name a flow or pass `--all`.
- **Config sanity label:** the `✓ reasonable — N of M runs would flag` footer (also
  in `--json` as `calibration`) tells you whether your thresholds are reasonable, too
  loose, or too strict. Informational only — it never changes the exit code.
- **`INSUFFICIENT-DATA` for every metric?** The baseline is per-**commit**, not
  per-run, and your own commit never backs itself — see the `excluded from
  baseline` note above. The full mechanics (the `-dirty` tag, `min_baseline_commits`,
  `baseline_n`, a worked walkthrough) are in
  [`docs/baselines-and-history.md`](./baselines-and-history.md).

**`--json`** → single flow emits a `compare` payload (`schema_version = 1`,
top-level keys `calibration`, `verdicts`, `schema_version`); 2+ flows or `--all` emit
a `compare-all` envelope. Each verdict entry keys the metric under `metric` (not
`metric_name`).

---

## `budget-check` — the CI gate

```
perfvibe budget-check <flow> [--strict] [--metric <name>] [--verbose] [--restart] [--device <serial>]
```

The one command that gates. It reuses `compare`'s verdict and applies **exactly one
rule: any `regression` fails the flow.** On a confirmed regression it exits `1` — that
exit code *is* the CI signal.

- **`--strict`** flips the fail-open default: an *insufficient-data* case (e.g. no
  baseline history) becomes a failure instead of a pass. It never changes an
  already-confirmed regression or a clean pass.
- **`--metric <name>`** shows a single-metric detail view (larger chart + git context
  on the offender).

```text
┌─ perfvibe budget-check · demo · HEAD 8317ae3 (main)
│   ✗  checkout          1310.0 ms     812.0 ms   ↑ +61.3%  REGRESSION         ▁▁▁▁█
│   ✓  ttfp               421.0 ms     430.0 ms    ↓ -2.1%  stable             ████▁
│   ✗  total_time_ms     1310.0 ms     805.0 ms   ↑ +62.7%  REGRESSION         ▁▁▁▁█
├──────────────────────────────────────────────────────────────────────────────────
│   ✗  GATE FAILED   ·   2 metrics regressed   ·   exit 1
└─
```

**`--json`** → budget-check payload (`schema_version = 1`) with top-level
`gate_status` (`"pass"`/`"fail"`), `offending_metrics` (a list), `strict`, and a flat
`verdicts[]` array where each gated metric's entry carries `"gated": true`. The pretty
banner text never appears in `--json`.

```json
{ "schema_version": 1, "gate_status": "fail",
  "offending_metrics": ["checkout", "total_time_ms"], "strict": false, "verdicts": [ … ] }
```

---

## `history` — machine-readable chart export

```
perfvibe history <flow> [--metric <name>] [--limit N] [--restart] [--device <serial>] [--device-key <key>]
```

Reads the local store and emits every persisted run for a flow, oldest → newest, with
each metric's `{p50, p90, n, unit}`, across both metric families (marker measures and
Flashlight system-sample aggregates). `--metric` narrows to one metric; `--limit N`
(default 50) takes the most recent N runs. Like `compare`, it is show-only —
`0` success, `2` usage (unknown flow/metric, or no history for the flow+device+mode),
`3` runtime failure, never `1`.

```text
demo — device=unknown|unknown|physical mode=warm — 5 run(s)

checkout (ms)  ▁▁▁▁█
  run      date         commit           p50        p90
  1        2020-01-01   c1             805.0      812.0
  4        2020-01-01   c4             805.0      812.0
  5        2020-01-01   head          1285.0     1310.0
```

**`--json`** → history payload (`schema_version = 1`) — the per-flow historical
series. This is the export you feed a chart.

---

## `init` — scaffold the flow config

```
perfvibe init <flows-dir> [--driver <d>] [--db <path>] [--bundle-id <id>] [--force] [--yes] [--prune-missing]
```

Generates or merges `perfvibe.toml`. See [`configuring-flows.md`](./configuring-flows.md)
for the complete behavior (subflows skipping, `bundle_id` detection, safe merge,
`--force`, `--prune-missing`, the comment-loss guard, and CI guidance).

**`--json`** → init payload (**`schema_version = 2`**) reporting `flows_added` /
`flows_pruned` and the resolved config target.
