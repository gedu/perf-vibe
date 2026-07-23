# Specification: `compare` capability (New — Phase 2, compare-only slice)

**Revision 3** — supersedes Rev 2. Adds three cross-cutting quality bars the user emphasized: a **bounded-performance NFR**, **pretty-output UX acceptance criteria**, and an explicit **corner-case matrix**. All Rev 2 content stands unchanged unless a Rev 3 requirement refines it. Rev 3 introduces ONE additive index migration (new index only, no table/data change) to keep compare's baseline access pattern indexed — see the Bounded Performance requirement and the design.

**Revision 2** — supersedes the version without the config sanity label. Adds pinned tuning defaults and the config sanity label requirement.

**Grounded in**: proposal `openspec/changes/compare/proposal.md`, decisions #53 (scope), #58 (calibration/defaults), and #39 (regression best practices), canonical spec `openspec/specs/perf-run.md`.

## Purpose

`perf compare <flow>` computes and SHOWS a per-metric, direction-aware regression verdict for the latest run against recent history. This slice is compare-only and purely informational: it reads `run`'s stored data, adds no schema migration, and never gates CI (exit `1`/budget-check is deferred to a follow-up change).

## Scope

| Concern | This spec | Deferred |
|---|---|---|
| Verdict computation, baseline, classification, output | YES | — |
| Config sanity label (pretty + `--json`) | YES | — |
| `budget-check` CI gate (exit `1` on regression) | — | Follow-up SDD change |
| Per-metric threshold override, variance/reliability flag | — | Follow-up |
| `perf compare --calibrate` sweep mode | — | Follow-up (planned, not implemented) |
| Warm-up discard for marker/`measure` metrics | N/A by design (documented, not a gap) | — |
| `run` schema/tables | Unchanged (compare only reads) | — |
| Additive index migration (new index only, no table/data change) | YES (Rev 3 — for the bounded-performance NFR) | — |
| Bounded, indexed baseline access (performance NFR) | YES (Rev 3) | — |
| Pretty-output UX acceptance criteria | YES (Rev 3) | — |
| Corner-case handling matrix | YES (Rev 3) | — |

## Requirements

### Requirement: Verdict Computation

For each metric on the latest run, the system SHALL compute the latest percentile (p50/p90, from `run_metric_summary`) and a BASELINE = median-by-commit over the prior N commits (default 10, configurable), scoped to the same flow+metric+`device_key`, matching warm/cold `mode`, excluding dev-bundle runs and the current commit. The system SHALL classify the result, direction-aware, into `improvement | stable | regression | insufficient-data`.

#### Scenario: Verdict computed for latest run
- GIVEN a flow with 12 prior release-bundle commits on the same device and mode
- WHEN `perf compare <flow>` runs
- THEN each metric gets a p50/p90 latest value, a median-by-commit baseline, and one of the four classifications

### Requirement: Baseline Correctness

The baseline SHALL be the median of one value PER COMMIT (repeated same-commit runs collapse to a single median point before baseline aggregation), SHALL exclude dev-bundle runs and the current commit, and SHALL keep warm and cold as separate series grouped by `device_key`.

#### Scenario: Repeated same-commit runs collapse
- GIVEN commit C has 3 recorded runs with differing values
- WHEN the baseline is computed
- THEN commit C contributes exactly one median value, not 3 separate points

#### Scenario: Dev bundle and current commit excluded
- GIVEN the last 10 commits include 2 dev-bundle runs and the run being evaluated is on commit HEAD
- WHEN the baseline is computed
- THEN dev-bundle runs and HEAD are excluded from the baseline set

#### Scenario: Warm/cold and device series never mix
- GIVEN history has both warm and cold runs on two devices
- WHEN the baseline is computed for a warm run on device A
- THEN only warm runs on device A contribute

#### Scenario: Naive last-10-RUNS window gives a different, wrong baseline
- GIVEN commit C1 has 4 runs and commit C2 has 1 run within the last 10 runs
- WHEN comparing a naive last-10-RUNS mean/median against the median-by-commit policy
- THEN the two baselines differ (C1 is over-weighted in the naive window) and the system SHALL use the median-by-commit result, not the naive one

### Requirement: Direction-Aware Classification

Classification SHALL use `metric.higher_is_better`: FPS metrics regress when they DROP; duration/RAM/CPU metrics regress when they RISE.

#### Scenario: FPS drop is a regression
- GIVEN latest FPS avg is below baseline by more than threshold+floor
- WHEN classified
- THEN the verdict is `regression` (a naive "bigger number = worse" rule would wrongly call this `improvement`)

#### Scenario: Duration rise is a regression
- GIVEN latest `total_time_ms` is above baseline by more than threshold+floor
- WHEN classified
- THEN the verdict is `regression`

### Requirement: Threshold and Absolute Floor

Classification SHALL require BOTH `threshold_pct` (relative) AND an absolute floor (both configurable) to be exceeded before flagging `improvement`/`regression`; below the floor SHALL always be `stable` regardless of percentage delta. Per-metric override is deferred.

The system SHALL ship conservative, low-noise defaults, ALL overridable via `perf.toml` or CLI flags:

| Setting | Default | Scope |
|---|---|---|
| `threshold_pct` | `5.0` | All metrics |
| Absolute floor | `ms: 5`, `mb: 5`, `cpu-pct: 3`, `fps: 2` | Per unit |
| Minimum baseline commits | `3` | Below this → `insufficient-data` |
| Warm-up discard `K` | `1` | Flashlight/`system_sample` metrics only |

#### Scenario: Below floor stays stable
- GIVEN a fast metric's delta exceeds `threshold_pct` but is below the absolute floor
- WHEN classified
- THEN the verdict is `stable`

#### Scenario: Defaults apply unless overridden
- GIVEN a project with no `perf.toml` tuning overrides
- WHEN `perf compare <flow>` runs
- THEN `threshold_pct=5.0`, the per-unit floors above, minimum baseline commits `3`, and warm-up `K=1` are used

#### Scenario: Config overrides take precedence
- GIVEN `perf.toml` or a CLI flag sets a non-default `threshold_pct` or floor
- WHEN `perf compare <flow>` runs
- THEN the overridden value is used instead of the shipped default

### Requirement: Config Sanity Label

`perf compare <flow>` SHALL evaluate the ACTIVE `threshold_pct`/floor configuration against the flow's STORED history and SHALL surface a sanity label in BOTH the pretty output and the `--json` payload. The label SHALL report how the config behaves against the observed delta distribution and SHALL WARN only on clearly-degenerate configs; it is informational and SHALL NOT change the exit code (still `0`/`2`/`3`, never `1`) or any per-metric verdict.

#### Scenario: Reasonable config
- GIVEN a config where some but not all historical runs would flag under the active threshold/floor
- WHEN the sanity label is computed
- THEN it reports "reasonable" with a count, e.g. "2 of 12 runs would flag"

#### Scenario: Too loose (degenerate)
- GIVEN the active floor EXCEEDS the maximum observed delta across the flow's history, so the config can NEVER flag any run
- WHEN the sanity label is computed
- THEN it warns "too loose" (regressions would be missed)

#### Scenario: Zero flags with a normal floor is not a false "too loose"
- GIVEN 0 of N historical runs would flag under the active config, but the floor does NOT exceed the maximum observed delta
- WHEN the sanity label is computed
- THEN it reports "reasonable" with the 0-of-N count, and SHALL NOT warn "too loose"

#### Scenario: Too strict (degenerate)
- GIVEN the active threshold is below typical run-to-run variance, so nearly every historical run would flag
- WHEN the sanity label is computed
- THEN it warns "too strict" (normal noise looks like a regression)

#### Scenario: Label never changes exit code or verdicts
- GIVEN the sanity label warns "too loose" or "too strict"
- WHEN `perf compare <flow>` completes
- THEN the exit code and every per-metric verdict are unaffected by the label

### Requirement: Insufficient-Data Classification

When baseline commits or post-warm-up iterations fall below `min_n`, the system SHALL classify `insufficient-data` and SHALL NEVER default to `stable`.

#### Scenario: Too few baseline commits
- GIVEN fewer than `min_n` qualifying baseline commits exist
- WHEN classified
- THEN the verdict is `insufficient-data`, not `stable`

#### Scenario: Too few post-warm-up iterations
- GIVEN the latest run has fewer than `min_n` iterations after warm-up discard
- WHEN classified
- THEN the verdict is `insufficient-data`

### Requirement: Warm-Up Discard Asymmetry (documented policy)

Warm-up discard (default K=1) SHALL apply ONLY to Flashlight/`system_sample` metrics, which carry an iteration `idx`. Marker/`measure` metrics attach to the run with no ordinal; warm-up discard SHALL be N/A for them by design, not silently skipped or misapplied.

#### Scenario: K applies to Flashlight metrics only
- GIVEN a run with FPS (Flashlight) and a marker duration metric
- WHEN warm-up discard K=1 is applied
- THEN iteration 0 is dropped from the FPS stats and the marker metric is unaffected, with the policy stated in output/docs

### Requirement: Output Contract

`perf compare <flow>` SHALL render a pretty per-metric verdict with sparklines for humans, AND a versioned `--json` payload (`schema_version`) as the stable, parseable contract; pretty output SHALL be treated as lossy and never parsed by tooling. A non-TTY invocation SHALL print a stderr nudge toward `--json`.

#### Scenario: Both outputs available
- GIVEN a completed comparison
- WHEN run without flags vs with `--json`
- THEN pretty output shows sparklines per metric, and `--json` emits a `schema_version`-tagged payload with the same verdicts

#### Scenario: Non-TTY nudges toward --json
- GIVEN stdout is not a TTY
- WHEN `perf compare <flow>` runs without `--json`
- THEN a stderr message suggests `--json`

### Requirement: Exit-Code Discipline

The tool SHALL exit `0` when the comparison ran and a verdict was shown (regardless of verdict value, including `regression`), `2` on usage error (unknown flow, no history), `3` on runtime error. The tool SHALL NEVER exit `1` in this slice.

#### Scenario: Regression still exits 0
- GIVEN a metric classifies as `regression`
- WHEN `perf compare <flow>` completes
- THEN exit code is `0`

#### Scenario: Unknown flow is a usage error
- GIVEN a flow name with no history
- WHEN `perf compare <flow>` runs
- THEN exit code is `2`

### Requirement: Hexagonal Boundary Enforcement

Verdict math SHALL live in a pure domain module with no I/O; the store/analyzer SHALL be accessed only behind the `Analyzer` Protocol port; `domain/` SHALL import no adapter module.

#### Scenario: Domain has no adapter imports
- GIVEN `domain/regression.py` and `domain/statistics.py`
- WHEN static import analysis runs
- THEN neither imports from `adapters/`

### Requirement: Bounded Compare Performance (NFR)

`perf compare <flow>` SHALL compute the full verdict AND the config sanity label using BOUNDED, INDEXED queries over ONLY the baseline window (the most recent `baseline_n` commits, default 10), and SHALL NEVER perform a full-history scan whose cost grows with total stored runs. Aggregation SHALL be pushed to SQL (reusing the `run_metric_summary` view for the `measure` family); Python median/percentile math SHALL run ONLY over the small windowed row set. The sanity label SHALL reuse the SAME windowed rows the baseline read returns (no second heavy pass over history). The number of SQL statements issued per invocation SHALL be a small constant that does NOT grow with the number of commits, runs, or metrics (NO N+1 — no per-commit and no per-metric query fan-out).

#### Scenario: Verdict over bounded window, not full history
- GIVEN a flow with a large stored history (hundreds of runs across dozens of commits, both warm and cold)
- WHEN `perf compare <flow>` runs
- THEN the baseline reads only the most recent `baseline_n` commits for the matching flow+metric+`device_key`+`mode`, via an indexed access path (not a full-table scan of `run`)

#### Scenario: Sanity label adds no second heavy pass
- GIVEN the baseline window rows have been read for a metric
- WHEN the config sanity label is computed
- THEN it is derived from those SAME already-read per-run rows, issuing no additional per-run/per-commit history query

#### Scenario: SQL statement count is bounded and does not grow with history
- GIVEN two flows, one with a small history and one with a large history and many metrics
- WHEN `perf compare <flow>` runs against each
- THEN the count of executed SQL statements is a small constant that is the same order of magnitude for both (no per-commit or per-metric query fan-out)

#### Scenario: Wall-clock stays under budget at scale
- GIVEN a seeded large history (≈800–1000 runs across 50+ distinct commits, multiple metrics, warm and cold)
- WHEN `perf compare <flow>` runs
- THEN it returns the correct verdict and completes well under a tight, named wall-clock budget constant (guarding against accidental O(history) work)

### Requirement: Pretty-Output UX

The pretty (human) output SHALL render, per metric, a line showing: the metric name, the latest value vs the baseline value, a delta arrow with the signed percentage, and the classification (`improvement | stable | regression | insufficient-data`) presented clearly, with `regression` VISUALLY EMPHASIZED. The config sanity label SHALL be placed sensibly (as a summary/footer line, not interleaved mid-metric). The renderer SHALL honor `--no-color`, the `NO_COLOR` environment variable, and a non-TTY stdout (no ANSI escapes emitted in any of those cases). The `--json` payload SHALL be unaffected by any pretty-output or color choice.

#### Scenario: Per-metric line content
- GIVEN a completed comparison for a metric
- WHEN pretty output renders
- THEN the metric's line shows the metric name, latest vs baseline, a delta arrow + signed %, and the classification word/glyph

#### Scenario: Regression is visually emphasized
- GIVEN a metric classifies as `regression`
- WHEN pretty output renders (color on)
- THEN that metric's line is visually emphasized relative to `stable`/`improvement` lines, and with color OFF the emphasis degrades to a plain-text marker (never relies on color alone)

#### Scenario: Color disabled paths emit no ANSI
- GIVEN `--no-color`, or `NO_COLOR` set, or a non-TTY stdout
- WHEN pretty output renders
- THEN no ANSI escape sequences are emitted

#### Scenario: JSON unaffected by color/pretty choices
- GIVEN any combination of color flags or TTY state
- WHEN `--json` is requested
- THEN the payload is byte-identical to the color-agnostic contract (color state changes nothing in `--json`)

### Requirement: Corner-Case Handling

`perf compare <flow>` SHALL handle every degenerate-history corner case gracefully: it SHALL NEVER crash and SHALL NEVER exit `1`. Each case SHALL classify or handle as specified below (insufficient-data where no valid baseline exists; `stable` for zero-variance history; skip-and-note for asymmetric metric sets).

| # | Corner case | Expected behavior |
|---|---|---|
| C1 | No history / first-ever run of a KNOWN flow (no prior baseline) | `insufficient-data`; exit `0` (verdict shown); never `1` |
| C2 | Unknown flow (no rows at all) | usage error, exit `2`; never `1` |
| C3 | Single baseline commit (fewer than `min_baseline_commits`) | `insufficient-data`; never `stable` |
| C4 | All-equal values (zero variance) | `stable`; no divide-by-zero (baseline==0 and zero-delta guarded) |
| C5 | Metric in the LATEST run absent from the baseline (new metric) | `insufficient-data` for that metric; no crash |
| C6 | Metric in the BASELINE absent from the latest run (dropped metric) | skipped / noted; no crash |
| C7 | Device or mode never seen before | empty baseline ⇒ `insufficient-data` |
| C8 | Warm-only vs cold-only history (mode split yields empty baseline for the evaluated mode) | `insufficient-data` |
| C9 | Dev-bundle-only history (all baseline candidates excluded) | `insufficient-data` |

#### Scenario: First-ever run classifies insufficient-data, not exit 1
- GIVEN a known flow whose only run is the one being evaluated (no prior baseline)
- WHEN `perf compare <flow>` runs
- THEN every metric is `insufficient-data` and the exit code is `0` (never `1`)

#### Scenario: Zero-variance history is stable with no divide-by-zero
- GIVEN a baseline where every commit's value is identical (and possibly zero)
- WHEN classified
- THEN the verdict is `stable` and no divide-by-zero occurs

#### Scenario: New metric with no baseline does not crash
- GIVEN a metric present in the latest run but absent from all baseline commits
- WHEN classified
- THEN that metric is `insufficient-data` and the run does not crash

#### Scenario: Dropped metric is skipped, not fatal
- GIVEN a metric present in the baseline but absent from the latest run
- WHEN comparing
- THEN that metric is skipped/noted and the run does not crash

#### Scenario: Mode-split empty baseline is insufficient-data
- GIVEN history contains only cold runs but the latest run is warm (or vice-versa)
- WHEN the baseline is computed for the evaluated mode
- THEN the baseline is empty and the verdict is `insufficient-data`, not a crash or `stable`

#### Scenario: Dev-bundle-only history yields insufficient-data
- GIVEN every prior run for the flow is a dev-bundle run
- WHEN the baseline is computed (dev bundles excluded)
- THEN the baseline is empty and the verdict is `insufficient-data`

#### Scenario: No corner case ever exits 1 or crashes
- GIVEN any of the corner cases C1–C9
- WHEN `perf compare <flow>` runs
- THEN it terminates cleanly with exit `0` (or `2` for the unknown-flow usage error), never `1`, and never raises an uncaught exception
