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
  `history`, `markers snippet`/`markers doctor`, and `reassure list` payloads are
  `schema_version = 1`; `init` is `schema_version = 2` (bumped when `flows_pruned`
  was added); `reassure import` is `schema_version = 3`. Branch on the field —
  don't assume a constant.
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
**median-by-commit baseline** — for each metric it reports the latest value, the
baseline, the delta, a direction arrow, a `stable`/`REGRESSION` classification (only a
regression is uppercased), a sparkline trend and the `min→max` range that gives that
sparkline its scale. It is **show-only**: a regression is informational and still exits
`0`. (The gate that fails the build is [`budget-check`](#budget-check--the-ci-gate).)

```text
┌─ perfvibe compare · demo · warm · Pixel 8 Pro
│
│      METRIC               LATEST     BASELINE          Δ  STATUS             TREND       min→max
│   ──────────────────────────────────────────────────────────────────────────────────────────────
│   ✗  checkout          1310.0 ms     812.0 ms   ↑ +61.3%  REGRESSION         ▁▁▁▁█      812→1310
│   ·  ttfp               421.0 ms     430.0 ms    ↓ -2.1%  stable             ████▁       421→430
│   ·  ram_peak_mb        205.0 mb     206.0 mb    ↓ -0.5%  stable             ████▁       205→206
│   ✗  total_time_ms     1310.0 ms     805.0 ms   ↑ +62.7%  REGRESSION         ▁▁▁▁█      805→1310
│   ·  fps_avg            58.1 fps     58.2 fps    ↓ -0.2%  stable             ████▁     58.1→58.2
│
│   ✓ reasonable — 0 of 4 runs would flag
│   note: 1 run(s) excluded from baseline: 1 on the current commit — commit your changes to grow history
└─
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
┌─ perfvibe history · demo · warm · unknown device · 3 run(s)
│
│   checkout (ms)   window ▁▁█
│
│    1310.0 ┤                 ██
│    1185.5 ┤                 ██
│    1061.0 ┤                 ██
│     936.5 ┤                 ██
│     812.0 ┤ ██      ██      ██
│             └────────────────────────
│             c1      c4      head
│
│   RUN     DATE        COMMIT          P50         P90       Δ P90
│   ───────────────────────────────────────────────────────────────
│   1       2020-01-01  c1            805.0       812.0           -
│   4       2020-01-01  c4            805.0       812.0     → +0.0%
│   5       2020-01-01  head         1285.0      1310.0    ↑ +61.3%
│
└─
```

The y-axis ticks carry the series **min and max**, which a sparkline cannot: `▁▁█`
says the last run was the worst and nothing about how much worse. The `Δ P90` column
is each run against the last run that had a value, and its color is direction-aware
(`fps_avg` rising is green; `checkout` rising is red) — the arrow and the percentage
are facts, the color is only the good/bad reading, so `--no-color` loses no data.
The chart and the table always show the **same** runs; when that truncates the
window the box says `charting the last 8 of N runs`. The header sparkline still spans
the whole window.

**`--json`** → history payload (`schema_version = 1`) — the per-flow historical
series. This is the export you feed a chart.

---

## `markers` — instrumentation snippet + logcat diagnostics

```
perfvibe markers snippet [--lang ts|js]
perfvibe markers doctor [<logcat line>]
perfvibe markers doctor            # or: <logcat dump> | perfvibe markers doctor
```

A command **group** (`snippet` and `doctor` are sub-commands of `markers`), read-only,
and never touches a device, `adb`, or the run history. See the README's
[Instrumenting your app with `[PERF]` markers](../README.md#instrumenting-your-app-with-perf-markers)
for a worked example of both.

### `markers snippet`

Prints a paste-ready TS/JS module — built on
[`react-native-performance`](https://github.com/oblador/react-native-performance) —
implementing a `markStart`/`markEnd`/`measureMark` trio that emits the exact
`[PERF] <name>: <n>ms` text form `perfvibe run` parses from logcat. `--lang ts`
(default) keeps parameter type annotations; `--lang js` is the identical module
with them stripped. Any other `--lang` value is a usage error (exit `2`). Pretty
output is the raw code only — no decoration that would break a copy-paste.

**`--json`** → snippet payload (`schema_version = 1`) with exactly `lang` and
`code` alongside `schema_version` — no other keys.

### `markers doctor`

Diagnoses a single logcat line (positional argument) or a piped capture (no
argument, non-TTY stdin) against the SAME parser `perfvibe run` uses — never a
second, independently-maintained tag/regex/JSON detector. Exactly one input
source is required: an argument **and** piped stdin together, or neither with
stdin left as an interactive TTY, is a usage error (exit `2`).

Every observed line is classified into exactly one category: a completed
marker, a bare `markStart` with no matching `markEnd`, a `[PERF-META]` context
line, a per-line parse failure (with the SPECIFIC reason — `malformed_text`,
`invalid_json`, `invalid_value`, or `oversized`), or an ignored (non-`[PERF]`)
line. An oversized line's echoed text is truncated to its first 120 characters
plus `…`. `doctor` is informational, never a gate: finding zero markers, or
every line failing to parse, still exits `0` — `coverage_ok` in the `--json`
payload reports whether anything parsed, but never changes the exit code.

```text
mode: line
lines scanned: 1
parsed: 1
  - checkout: 812.0ms
mark_start_without_end: 0
perf_meta: 0
ignored: 0
parse_failures: 0
coverage_ok: True
```

**`--json`** → doctor payload (`schema_version = 1`), ONE coherent shape shared
by both modes: `mode` (`"line"`/`"stdin"`), `input_summary.lines_scanned`, a
`breakdown` (`parsed`, `mark_start_without_end`, `perf_meta`, `parse_failures`,
`ignored`), `coverage_ok`, and `diagnostic`.

### Exit codes (both sub-commands)

`0` success (including `doctor` finding zero markers — that is a successful
diagnosis, not a failure) · `2` usage error (unknown `--lang`, ambiguous or
missing `doctor` input, unknown flag) · `3` runtime failure (a piped-stdin read
error). Like every other command, `markers` **never** exits `1`.

---

## `reassure` — persisted `@callstack/reassure` results

```
perfvibe reassure import [<path>] [--kind current|baseline|unknown]
perfvibe reassure list [--limit N]
```

A command **group** (`import`/`list`/… are sub-commands of `reassure`), read-only
except for `import`'s own persistence step. This page covers `import` and `list`
only — `entries`, `show`, `history`, `compare`, and `run` are added by their own
slices of this capability, once they ship.

The flat `perfvibe reassure-import <path>` form still works exactly as before
(same implementation, same `--json` payload) but is now **deprecated**: it is
hidden from `perfvibe --help`, and every invocation prints a one-line
`DeprecationWarning: The command 'reassure-import' is deprecated. use
\`perfvibe reassure import\` instead` notice to stderr — `--json` stdout stays
byte-pure. New scripts and agents should call `perfvibe reassure import`
directly; the alias is kept indefinitely, with no removal version planned.

### `reassure import`

Parses a `@callstack/reassure` `.perf` JSON-Lines file (default: the config's
`reassure_path`) and persists it idempotently, keyed by content hash — a
byte-identical re-import reports `already_imported: true` and writes nothing
new. `--kind` overrides the kind derived from the file's basename
(`current.perf` → `current`, `baseline.perf` → `baseline`, else `unknown`).
Stores data and prints a confirmation only: it judges, compares, and gates
nothing.

```text
┌─ perfvibe reassure-import · current · .reassure/current.perf
│
│   ✓  4 entries imported          content 3f2a1b9c2d4e
│      40 duration samples · 4 count samples
│   ⚠      1 entries with render issues
│      6 line(s) skipped — reasons on stderr
└─
```

**`--json`** → `reassure_import_v1` payload (`schema_version = 3`): `path`,
`content_hash`, `kind`, `already_imported`, `entries_imported`,
`entries_skipped`, `duration_samples_imported`, `count_samples_imported`,
`entries_with_render_issues`, `entries_dropped_duplicate_name`.

### `reassure list`

Reports the import roster, ordered newest-first by `created_date` — falling
back to `imported_at` when a file's header carried no `creationDate`.
`commit_hash`/`branch` are LABELS only: nothing keys, groups, or filters on
them, and two imports may legitimately share both. `--limit N` (default `50`,
matching `history` — not `compare`'s `baseline_n`) caps the roster. An empty
roster still exits `0`.

```text
┌─ perfvibe reassure list · 2 import(s)
│
│   IMPORT  DATE        BRANCH        COMMIT   ENTRIES
│   ──────────────────────────────────────────────────
│   2       2026-01-02  main          abc123d        4
│   1       2026-01-01  main          abc123d        0
│
└─
```

**`--json`** → `reassure_list_v1` payload (`schema_version = 1`):
`{"schema_version", "imports": [{"import_id", "imported_at", "created_date",
"branch", "commit_hash", "source_path", "entry_count"}, ...]}`. `ordering_key`/
`ordered_at` are deliberately absent from the payload — both are mechanically
derivable from `created_date`/`imported_at` already in each row.

### Exit codes (`reassure import` / `reassure list`)

`0` success (including an empty `list` roster, and an `import` of a readable
file that recovered zero entries) · `2` usage error (missing/unreadable
`.perf` path, invalid `--kind`) · `3` runtime/tooling failure
(store/transaction/render). Like every other command, `reassure` **never**
exits `1`.

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
