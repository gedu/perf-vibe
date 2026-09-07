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
perfvibe reassure entries <import-id>
perfvibe reassure show <name> [--import <id>]
perfvibe reassure history <name>
perfvibe reassure compare <name>
perfvibe reassure run
```

A command **group** (`import`/`list`/`entries`/`show`/`history`/`compare`/
`run` are sub-commands of `reassure`), read-only except for `import`'s own
persistence step and `run`'s subprocess + persistence step.

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

### `reassure entries <import-id>`

Reports every measurement line for ONE import: its `name`, `entry_type`, the
`runs` count the file DECLARED, and its duration/count summaries. Duration and
count are two INDEPENDENTLY-reduced series (`durations` is outlier-filtered,
`counts` is the unfiltered post-warmup set) — their `n`s are never forced to
agree, and `runs` is never reconciled against either; a mismatch between them
is a real fact about the source file, not something this command repairs. An
entry with zero stored samples for one series reports that series absent
(`null` in `--json`, `-` in every column of its row in the pretty view) —
never a measured zero standing in for "no data". An unknown `import-id` is a
usage error (exit `2`); a real import with zero entries is a valid, distinct
state (exit `0`, an empty list) — the two are never conflated.

```text
┌─ perfvibe reassure entries · import 7 · 2 entrie(s)
│
│   NAME                                      TYPE      RUNS   DUR P50   DUR P90   DUR N   CNT P50   CNT P90   CNT N
│   ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
│   WidgetPanel renders correctly             render       8      10.2      10.6       6       1.0       2.0       8
│   NotificationBanner renders after dismiss  render       3         -         -       -       5.0       6.0       3
│
└─
```

**`--json`** → `reassure_entries_v1` payload (`schema_version = 1`):
`{"schema_version", "import_id", "entries": [{"name", "entry_type", "runs",
"duration", "count"}, ...]}`, where `duration`/`count` are each either `null`
or `{"p50", "p90", "n", "unit"}`. `entry_id` (the internal store row id) and
`initial_update_count` (a `reassure show`-only field, per D5) are deliberately
absent — see `contracts/reassure_entries_v1.py` for the full reasoning.

### `reassure show <name>`

Reports one `name`'s LATEST detail plus its `issues.initialUpdateCount`
diagnostic as a **state transition**, never a percentage. Defaults to the
entry matching `name` in the most recent import **overall** (by `created_date`/
`imported_at` — D8); `--import <id>` selects a specific import instead. There
is **no walk-back**: if `name` is missing from the target import, this exits
`2` — it never silently falls back to an older import that happens to contain
it (A14). The baseline for the state transition is always the immediately
preceding import that ALSO contains `name`; if none exists (or the diagnostic
was never measured on either side), the transition reports `unknown` — `NULL`
(never measured) and `0` (measured, clean) are never treated as the same
value.

```text
┌─ perfvibe reassure show · WidgetPanel renders correctly · import 7
│
│   entry type                      render
│   runs (declared)                      8
│   duration p50                   10.2 ms
│   duration p90                   10.6 ms
│   duration n                           6
│   count p50                    1.0 count
│   count p90                    2.0 count
│   count n                              8
│
│   ✗ extra mount render introduced (0 -> 1)
│
└─
```

The one D5 sentence line — never a table row, never an arrow-and-percentage —
takes one of six shapes for the five states:

| State | Line |
|---|---|
| `introduced` (`0 -> N > 0`) | `✗ extra mount render introduced (0 -> N)` |
| `resolved` (`N > 0 -> 0`) | `✓ extra mount render resolved (N -> 0)` |
| `changed` (different non-zero values) | `✗ mount render count changed (A -> B)` |
| `unchanged`, both `0` | *(no line printed — nothing to report)* |
| `unchanged`, both the same non-zero value | `· mount render count unchanged (N)` (dim) |
| `unknown` (either side never measured) | `· mount render diagnostics unavailable (not measured in one of the two imports)` (dim) |

A `declared runs N != stored n M (duration|count)` line appears — once per
series, only on disagreement — when the file's declared `runs` does not match
that series' actual stored sample count; this is surfaced, never repaired.

**`--json`** → `reassure_show_v1` payload (`schema_version = 1`), a FLAT dict
with exactly ten keys: `schema_version`, `import_id`, `name`, `entry_type`,
`runs` (declared), `duration`, `count` (each `null` or `{"p50", "p90", "n",
"unit"}`), `initial_update_count`, `baseline_initial_update_count` (both `int`
or `null` — `null` means "never measured", `0` means "measured, clean"; these
are different facts), and `initial_update_state` (one of `"introduced"`,
`"resolved"`, `"changed"`, `"unchanged"`, `"unknown"`). There is deliberately
**no** `*_delta_pct`/`*_pct` key anywhere for the update count — D5 is a state
transition, never a delta.

### `reassure history <name>`

Reports `name`'s FULL series — one point per import that contains it, oldest
first (D2: one **import** is one point, never a per-commit collapse; see
[`docs/baselines-and-history.md`](./baselines-and-history.md#reassure-one-point-per-import-not-per-commit)
for the contrast with the flow world's baseline rule). Each point carries its
own independently-reduced `duration`/`count` summaries — the two series are
drawn as TWO separate sections, each with its own chart and table, never one
combined chart (invariant I1, surfaced here). A `name` present in zero imports
is a usage error (exit `2`); a coverage gap within an otherwise non-empty
series — an import that never measured `name` — is not an error, it simply
contributes no point and shifts nothing.

```text
┌─ perfvibe reassure history · WidgetPanel renders correctly · 4 import(s)
│
│   duration (ms)   window ▁▂▁█
│
│     138.0 ┤                         ██
│     130.5 ┤                         ██
│     123.0 ┤                         ██
│     115.5 ┤                         ██
│     108.0 ┤ ██      ██      ██      ██
│             └────────────────────────────────
│             abcdef0 abcdef1 abcdef2 abcdef3
│
│   IMPORT    DATE        COMMIT          P50         P90       N
│   ─────────────────────────────────────────────────────────────
│   1000      2026-01-01  abcdef0       100.0       108.0       5
│   1001      2026-01-02  abcdef1       105.0       113.0       5
│   1002      2026-01-03  abcdef2       102.0       110.0       5
│   1003      2026-01-04  abcdef3       130.0       138.0       5
│
│   count (count)   window ▁▁██
│
│       2.0 ┤                 ██      ██
│       1.8 ┤                 ██      ██
│       1.5 ┤                 ██      ██
│       1.2 ┤                 ██      ██
│       1.0 ┤ ██      ██      ██      ██
│             └────────────────────────────────
│             abcdef0 abcdef1 abcdef2 abcdef3
│
│   IMPORT    DATE        COMMIT          P50         P90       N
│   ─────────────────────────────────────────────────────────────
│   1000      2026-01-01  abcdef0         1.0         1.0       5
│   1001      2026-01-02  abcdef1         1.0         1.0       5
│   1002      2026-01-03  abcdef2         2.0         2.0       5
│   1003      2026-01-04  abcdef3         2.0         2.0       5
│
└─
```

X-axis labels use the short `commit_hash` when present, else the date part of
`ordered_at`, else `#<import_id>` — never nothing. Unlike `history`
(the flow-world command), there is no `Δ` column and no direction-aware color:
`reassure` views only describe, they never judge (D3).

**`--json`** → `reassure_history_v1` payload (`schema_version = 1`):
`{"schema_version", "name", "points": [{"import_id", "ordered_at",
"ordering_key", "commit_hash", "duration", "count"}, ...]}`, where
`duration`/`count` are each either `null` or `{"p50", "p90", "n", "unit"}`.
`branch` is deliberately absent — nothing in this command reads it.

### `reassure compare <name>`

Compares `name`'s LATEST import against a baseline window of the
`baseline_n` (default **10**) PRIOR imports (A8: fetches `baseline_n + 1`
imports total via the same `reassure_series` method `history` uses, just at
this much smaller, config-driven limit) — reusing `regression.classify`,
`statistics.median`/`percentile` and the same D7 `[floors]` config, but
**never** `statistics.median_by_commit` (see
[`docs/baselines-and-history.md`](./baselines-and-history.md#reassure-one-point-per-import-not-per-commit)
for the full per-import-vs-per-commit contrast). `duration_ms` and
`render_count` are graded INDEPENDENTLY as two separate verdicts (invariant
I1 — never a combined score), both with `higher_is_better = false` (A6) and
`render_count`'s floor defaulting to exactly `0.0` (D7 — render counts are
deterministic, so `threshold_pct` alone is the correct guard). Below
`MIN_BASELINE_IMPORTS` (**3**) baseline imports, BOTH verdicts report an
explicit `insufficient-data` status — never a silent `stable`.

> ⚠️ **D3 — THE MOST IMPORTANT FACT ABOUT THIS COMMAND**: `reassure compare`
> **ALWAYS exits `0`**, including when a verdict reports `regression`. The
> exit code carries **no verdict information at all** — this command
> reports, it never gates. Read the `status` field inside each entry of the
> `--json` payload's `verdicts` array to learn the actual result. Treating a
> non-zero exit from this command as "regression found" is silently unsafe:
> it will **never** fire, no matter how bad the regression. `perfvibe
> budget-check` remains the only command in this tool that exits `1` on a
> confirmed regression, and it does not gate on `reassure` data at all (D3
> keeps `reassure` out of gating in v1).

```text
┌─ perfvibe reassure compare · WidgetPanel renders correctly · baseline 5 import(s)
│
│      METRIC               LATEST     BASELINE          Δ  STATUS             TREND
│   ────────────────────────────────────────────────────────────────────────────────
│   ·  duration_ms        100.0 ms     100.0 ms    → +0.0%  stable             ▅█▁▅
│   ✗  render_count      9.0 count    1.0 count  ↑ +800.0%  REGRESSION         ▁▁▁█
│
│   ✗ extra mount render introduced (0 -> 1)
└─
```

The D5 sentence below the table follows the exact same six-shape table as
`reassure show` (see above) — `d5_sentence` is the SAME shared function,
never a second copy of the wording.

**`--json`** → `reassure_compare_v1` payload (`schema_version = 1`): a FLAT
dict with exactly eight keys: `schema_version`, `name`, `latest_import_id`
(the import the verdicts were computed against), `baseline_import_n`
(**imports**, never commits — the honest name for the naming friction
`regression.classify`'s own `baseline_commit_n` parameter carries),
`verdicts` (a list, FIXED order — `duration_ms` then `render_count`, never
re-sorted; each entry has `metric`, `unit`, `direction`, `latest_value`,
`baseline_value`, `delta_pct`, `threshold_pct`, `floor`, `status`,
`sample_n`, `baseline_commit_n` — the SAME per-verdict shape `perf compare`
already uses), and the THREE flat D5 keys `initial_update_count`,
`baseline_initial_update_count` (both `int` or `null` — `null` means "never
measured", `0` means "measured, clean") and `initial_update_state`. There is
deliberately **no** `*_delta_pct`/`*_pct` key anywhere for the update
count — D5 is a state transition, never a delta.

### `reassure run`

Shells out to the project's configured reassure command (`reassure_command`
in `perfvibe.toml`, default `["npx", "reassure"]`) and, on a clean exit,
imports `reassure_path` through the SAME parse-then-store path
`reassure import` uses — reusing `reassure_import_v1` **verbatim** (no new
contract): `run` performs literally the same operation `import` does, just
sourcing its path from config instead of a CLI argument. Given the same
`.perf` file, `reassure run` and `reassure import <path>` produce the exact
same `--json` payload shape and exit code.

`run` is the **only** command in this whole capability that spawns an
external process. Every line the subprocess prints is relayed live to
**stderr only** — it never reaches stdout, so a noisy `npx reassure`
invocation (progress lines, warnings, jest output, even lines that
themselves look like JSON) can never corrupt `--json`'s
single-JSON-object stdout contract.

```bash
perfvibe reassure run --json   # runs the configured command, then imports
```

If the subprocess exits non-zero, `run` exits `3` and **no import is
attempted at all** — a failed measurement must never become a persisted
import. An invalid `reassure_command` (anything other than a non-empty TOML
array of strings — a bare string is rejected outright, never split into
argv) is a usage error caught at config-load time, before `run` even starts
(exit `2`). See [`configuring-flows.md`](./configuring-flows.md#the-reassure_path-setting)
for `reassure_path`/`reassure_command` configuration.

**`--json`** → the SAME `reassure_import_v1` payload `reassure import`
emits (`schema_version = 3`) — see the `reassure import` section above for
its full key set. There is no separate `reassure_run_v1` contract.

### Exit codes (`reassure import` / `reassure list` / `reassure entries` /
`reassure show` / `reassure history` / `reassure compare` / `reassure run`)

`0` success (including an empty `list` roster, an `import`/`run` of a
readable file that recovered zero entries, an `entries` call on a real
import with zero entries, **and a `compare` that reports a `regression` or
`insufficient-data` verdict — see the D3 warning above**) · `2` usage error
(missing/unreadable `.perf` path, invalid `--kind`, an unknown `entries
<import-id>`, `name` absent from the target import in `show`, `--import
<id>` naming an import `show` cannot find `name` in, `name` absent from
every import in `history`/`compare`, or an invalid `reassure_command`) · `3`
runtime/tooling failure (store/transaction/render, or `run`'s subprocess
itself exiting non-zero — in which case no import is attempted). Like every
other command, `reassure` **never** exits `1`.

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
