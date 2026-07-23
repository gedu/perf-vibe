# Specification: `compare` capability (Current — Phase 2 SHIPPED, compare-only slice)

**Consolidated from**: SDD artifacts `docs/specs/compare/{proposal,spec,design,tasks}.md` (all Rev 3), verified and merged to main in PR-A (`ba95f42`), PR-B (`75fc1c3`), PR-C (`f6de697`).

**Status**: Phase 2 (compare-only slice) COMPLETE. Capability ships with the following behavior as the authoritative current spec. Future SDD changes (`budget-check`, `--calibrate`) reference and compare against this spec.

## Purpose

`perf compare <flow>` reads `run`'s stored data (writes NOTHING new — no schema migration to `run`'s tables) and computes + SHOWS a per-metric, direction-aware regression verdict for the latest run against recent history: a median-by-commit baseline over the prior N commits, 4-state classification (`improvement | stable | regression | insufficient-data`), a config sanity label, pretty sparkline output, and a versioned `--json` contract. This slice is **compare-only and purely informational**.

**Critical invariant**: `compare` is show-only. It exits `0` (verdict shown, regardless of value — including `regression`), `2` (usage error), or `3` (runtime error). It SHALL NEVER exit `1`. Exit `1` is reserved exclusively for the future `budget-check` CI gate.

## Scope

| Concern | This capability | Status |
|---|---|---|
| Verdict computation: latest p50/p90 vs median-by-commit baseline | compare | SHIPPED ✓ |
| Baseline hygiene: same flow+metric+device_key+mode, exclude dev bundles + current commit | compare | SHIPPED ✓ |
| 4-state classification, direction-aware, min-n gating (never silent "stable") | compare | SHIPPED ✓ |
| Threshold (`threshold_pct`) + absolute floor gating, both configurable | compare | SHIPPED ✓ |
| Warm-up discard K=1 for Flashlight/`system_sample` metrics only | compare | SHIPPED ✓ |
| Config sanity label (pretty + `--json`), never affects exit code/verdict | compare | SHIPPED ✓ |
| Pretty output (sparklines) + versioned `--json` (`schema_version=1`) | compare | SHIPPED ✓ |
| Exit codes `0`/`2`/`3`, NEVER `1` | compare | SHIPPED ✓ |
| Hexagonal boundary: pure domain, `Analyzer` Protocol port | compare | SHIPPED ✓ |
| Bounded/indexed baseline performance (O(1) SQL statement count) | compare | SHIPPED ✓ |
| Corner-case handling (C1–C9 matrix) | compare | SHIPPED ✓ |
| `budget-check` CI gate (exit `1` on regression) | **budget-check** (future) | **DEFERRED** |
| `perf compare --calibrate` sweep mode | compare (future slice) | **DEFERRED** |
| Per-metric threshold override; variance/reliability flag | compare (future slice) | **DEFERRED** |
| Warm-up discard for marker/`measure` metrics | N/A by design (no iteration ordinal) | Documented policy, not a gap |
| `perf run` auto-invoking compare | future CLI-layer seam | **DEFERRED** |

## Requirements

### Requirement: Verdict Computation

**Status**: SHIPPED ✓

For each metric on the latest run, the system SHALL compute the latest percentile (p50/p90, from `run_metric_summary`) and a BASELINE = median-by-commit over the prior N commits (default 10, configurable), scoped to the same flow+metric+`device_key`, matching warm/cold `mode`, excluding dev-bundle runs and the current commit. The system SHALL classify the result, direction-aware, into `improvement | stable | regression | insufficient-data`.

#### Scenario: Verdict computed for latest run
- GIVEN a flow with 12 prior release-bundle commits on the same device and mode
- WHEN `perf compare <flow>` runs
- THEN each metric gets a p50/p90 latest value, a median-by-commit baseline, and one of the four classifications

### Requirement: Baseline Correctness

**Status**: SHIPPED ✓

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
- THEN the two baselines differ (C1 is over-weighted in the naive window) and the system uses the median-by-commit result, not the naive one

### Requirement: Direction-Aware Classification

**Status**: SHIPPED ✓

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

**Status**: SHIPPED ✓

Classification SHALL require BOTH `threshold_pct` (relative) AND an absolute floor (both configurable) to be exceeded before flagging `improvement`/`regression`; below the floor is always `stable` regardless of percentage delta. Per-metric override is deferred.

Shipped conservative defaults, all overridable via `perf.toml` or CLI flags:

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

**Status**: SHIPPED ✓

`perf compare <flow>` SHALL evaluate the ACTIVE `threshold_pct`/floor configuration against the flow's STORED history and SHALL surface a sanity label (`reasonable | too-loose | too-strict`) in BOTH the pretty output and the `--json` payload. The label is informational and SHALL NOT change the exit code or any per-metric verdict.

`too-loose` is defined as evidence of suppression — a concrete walk-forward step whose `|delta_pct| >= threshold_pct` (the % threshold would have flagged it) but whose `|delta_abs| < floor` (the floor actually suppressed it) — NOT merely "0 of N runs flagged." A calm, healthy baseline that never crosses `threshold_pct` correctly grades `reasonable`, never `too-loose`.

#### Scenario: Reasonable config
- GIVEN a config where some but not all historical runs would flag under the active threshold/floor
- WHEN the sanity label is computed
- THEN it reports "reasonable" with a count, e.g. "2 of 12 runs would flag"

#### Scenario: Too loose (degenerate) — floor suppresses a significant step
- GIVEN a walk-forward history step whose `|delta_pct|` meets or exceeds the active `threshold_pct` (the percentage threshold would have flagged it) but whose `|delta_abs|` is below the active floor (the floor suppresses it)
- WHEN the sanity label is computed
- THEN it warns "too loose", evidenced by that concrete suppressed step — not merely because the floor exceeds the history's maximum observed delta

#### Scenario: Zero flags with a normal floor is not a false "too loose"
- GIVEN 0 of N historical runs would flag under the active config, but no concrete step's `|delta_pct| >= threshold_pct` was suppressed by the floor
- WHEN the sanity label is computed
- THEN it reports "reasonable" with the 0-of-N count, and does NOT warn "too loose"

#### Scenario: Too strict (degenerate)
- GIVEN the active threshold is below typical run-to-run variance, so nearly every historical run would flag
- WHEN the sanity label is computed
- THEN it warns "too strict" (normal noise looks like a regression)

#### Scenario: Label never changes exit code or verdicts
- GIVEN the sanity label warns "too loose" or "too strict"
- WHEN `perf compare <flow>` completes
- THEN the exit code and every per-metric verdict are unaffected by the label

### Requirement: Insufficient-Data Classification

**Status**: SHIPPED ✓

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

**Status**: SHIPPED ✓

Warm-up discard (default K=1) SHALL apply ONLY to Flashlight/`system_sample` metrics, which carry an iteration `idx`. Marker/`measure` metrics attach to the run with no ordinal; warm-up discard is N/A for them by design, not silently skipped or misapplied.

#### Scenario: K applies to Flashlight metrics only
- GIVEN a run with FPS (Flashlight) and a marker duration metric
- WHEN warm-up discard K=1 is applied
- THEN iteration 0 is dropped from the FPS stats and the marker metric is unaffected

### Requirement: Output Contract

**Status**: SHIPPED ✓

`perf compare <flow>` SHALL render a pretty per-metric verdict with sparklines for humans, AND a versioned `--json` payload (`schema_version=1`) as the stable, parseable contract; pretty output is lossy and never parsed by tooling. A non-TTY invocation prints a stderr nudge toward `--json`.

#### Scenario: Both outputs available
- GIVEN a completed comparison
- WHEN run without flags vs with `--json`
- THEN pretty output shows sparklines per metric, and `--json` emits a `schema_version`-tagged payload with the same verdicts

#### Scenario: Non-TTY nudges toward --json
- GIVEN stdout is not a TTY
- WHEN `perf compare <flow>` runs without `--json`
- THEN a stderr message suggests `--json`

### Requirement: Exit-Code Discipline

**Status**: SHIPPED ✓

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

**Status**: SHIPPED ✓

Verdict math SHALL live in a pure domain module with no I/O (`domain/regression.py`, `domain/statistics.py`, `domain/calibration.py`); the store/analyzer SHALL be accessed only behind the `Analyzer` Protocol port (`domain/ports.py`, `typing.Protocol`); `domain/` SHALL import no adapter module. `adapters/analyzer_sql.py` is the sole `Analyzer` implementation and contains zero raw SQL — all SQL lives in `adapters/store_sqlite.py`.

#### Scenario: Domain has no adapter imports
- GIVEN `domain/regression.py`, `domain/statistics.py`, `domain/calibration.py`
- WHEN static import analysis runs
- THEN none imports from `adapters/`

### Requirement: Bounded Compare Performance (NFR)

**Status**: SHIPPED ✓

`perf compare <flow>` SHALL compute the full verdict AND the config sanity label using BOUNDED, INDEXED queries over ONLY the baseline window (the most recent `baseline_n` commits, default 10). Aggregation is pushed to SQL (reusing the `run_metric_summary` view for the `measure` family); Python median/percentile math runs ONLY over the small windowed row set. The sanity label reuses the SAME windowed rows the baseline read returns (no second heavy pass over history — "one query, two consumers"). The number of SQL statements issued per invocation SHALL be a small constant that does NOT grow with the number of commits, runs, or metrics (no N+1 — no per-commit and no per-metric query fan-out).

An additive index, `idx_run_baseline ON run(flow_id, device_id, mode, started_at)` (`db/migrations/0002_compare_baseline_index.sql`, mirrored in `db/schema.sql`), lets SQLite seek directly to the `(flow_id, device_id, mode)` partition. No table, column, or row was changed; `run`'s write path is untouched.

#### Scenario: Verdict over bounded window, not full history
- GIVEN a flow with a large stored history (hundreds of runs across dozens of commits, both warm and cold)
- WHEN `perf compare <flow>` runs
- THEN the baseline reads only the most recent `baseline_n` commits for the matching flow+metric+`device_key`+`mode`, via an indexed access path

#### Scenario: Sanity label adds no second heavy pass
- GIVEN the baseline window rows have been read for a metric
- WHEN the config sanity label is computed
- THEN it is derived from those SAME already-read per-run rows, issuing no additional per-run/per-commit history query

#### Scenario: SQL statement count is bounded and does not grow with history
- GIVEN two flows, one with a small history and one with a large history and many metrics
- WHEN `perf compare <flow>` runs against each
- THEN the count of executed SQL statements is a small constant of the same order of magnitude for both (empirically: 5 statements, budget 8, at ~5101 seeded runs / 301 commits)

#### Scenario: Wall-clock stays under budget at scale
- GIVEN a seeded large history (~5101 runs across 301 distinct commits, multiple metrics, warm and cold)
- WHEN `perf compare <flow>` runs
- THEN it returns the correct verdict and completes well under budget (measured 46.6ms vs a 150ms budget)

### Requirement: Pretty-Output UX

**Status**: SHIPPED ✓

The pretty (human) output SHALL render, per metric, a line showing: the metric name, the latest value vs the baseline value, a delta arrow with the signed percentage, and the classification, with `regression` VISUALLY EMPHASIZED (color path: bold/red; color-off path: a plain-text marker such as `!`/`REGRESSION`, never relying on color alone). The config sanity label SHALL be a footer line, never interleaved mid-metric. The renderer honors `--no-color`, `NO_COLOR`, and non-TTY stdout (no ANSI escapes emitted). The `--json` payload is unaffected by any pretty-output or color choice.

#### Scenario: Per-metric line content
- GIVEN a completed comparison for a metric
- WHEN pretty output renders
- THEN the metric's line shows the metric name, latest vs baseline, a delta arrow + signed %, and the classification word/glyph

#### Scenario: Regression is visually emphasized
- GIVEN a metric classifies as `regression`
- WHEN pretty output renders (color on)
- THEN that metric's line is visually emphasized relative to `stable`/`improvement` lines, and with color OFF the emphasis degrades to a plain-text marker

#### Scenario: Color disabled paths emit no ANSI
- GIVEN `--no-color`, or `NO_COLOR` set, or a non-TTY stdout
- WHEN pretty output renders
- THEN no ANSI escape sequences are emitted

#### Scenario: JSON unaffected by color/pretty choices
- GIVEN any combination of color flags or TTY state
- WHEN `--json` is requested
- THEN the payload is byte-identical to the color-agnostic contract (color state changes nothing in `--json`)

### Requirement: Corner-Case Handling

**Status**: SHIPPED ✓

`perf compare <flow>` SHALL handle every degenerate-history corner case gracefully: it SHALL NEVER crash and SHALL NEVER exit `1`.

| # | Corner case | Expected behavior |
|---|---|---|
| C1 | No history / first-ever run of a KNOWN flow | `insufficient-data`; exit `0`; never `1` |
| C2 | Unknown flow (no rows at all) | usage error, exit `2`; never `1` |
| C3 | Single baseline commit (fewer than `min_baseline_commits`) | `insufficient-data`; never `stable` |
| C4 | All-equal values (zero variance, incl. baseline==0) | `stable`; no divide-by-zero |
| C5 | Metric in the LATEST run absent from the baseline (new metric) | `insufficient-data` for that metric; no crash |
| C6 | Metric in the BASELINE absent from the latest run (dropped metric) | skipped / noted; no crash |
| C7 | Device or mode never seen before | empty baseline ⇒ `insufficient-data` |
| C8 | Warm-only vs cold-only history (mode split) | `insufficient-data` |
| C9 | Dev-bundle-only history | `insufficient-data` |

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

## Architecture Decisions

- **Median location**: median-by-commit is computed in pure `statistics.py` (SQLite has no `MEDIAN`); SQL pulls per-commit rows only, Python does the two-level median.
- **Ports as `typing.Protocol`**: `Analyzer.compare_latest(flow_name, device_key, mode) -> Optional[CompareResult]` (`CompareResult(verdicts, calibration)`, one additive carrier, one method — no port fan-out).
- **Pure domain, zero I/O**: `domain/regression.py`, `domain/statistics.py`, `domain/calibration.py` contain no adapter imports; `adapters/analyzer_sql.py` is the sole `Analyzer` implementation and contains zero raw SQL.
- **One query, two consumers**: the baseline read-model returns per-RUN rows (pre-collapse), batched per metric-family (one query for all `measure` metrics, one for all `system_sample` metrics); the SAME rows feed both `regression.classify` (baseline) and `calibration.grade_all` (sanity label) — no divergent second query.
- **Absolute floor per unit**: `floors` keyed by `metric.unit` (ms/MB/%/fps) — a single scalar floor across units would be semantically wrong.
- **Additive index, no schema break**: `idx_run_baseline` is the ONLY migration in this slice — no table/column/row change; `run`'s ingestion path and `schema.sql` fresh-DB shape are untouched.

## Known Limitations & Future Work

### Deferred (correctly NOT implemented — not gaps)

- **`budget-check` CI gate** (exit `1` on regression) — planned follow-up SDD change (master-design §18 Phase 3). `compare` NEVER exits `1`; that code is reserved for `budget-check`.
- **`perf compare --calibrate`** sweep mode — `CalibrationReport` is shaped so a future sweep can call `calibration.grade()` N times, but the sweep itself is not implemented.
- **Per-metric threshold override; variance/reliability flag** — deferred.
- **Warm-up discard for marker/`measure` metrics** — N/A by design (no iteration ordinal), documented policy, not a gap.

### Carried forward from verify report (0 CRITICAL, 2 WARNING, 2 SUGGESTION)

- **WARNING #1 — RESOLVED during this archive pass**: task 4.3 (updating `openspec/specs/perf-run.md`'s "COMPARE PLANNED" row) was left unchecked at verify time — a reader of the canonical `perf-run` spec would have incorrectly believed `compare` was still unimplemented. `openspec/specs/perf-run.md:19` now reads `SHIPPED ✓ — see openspec/specs/compare.md`, closing the doc-sync gap.
- **WARNING #2 (empirical, accepted)**: `store_sqlite.py`'s `eligible` CTE (baseline queries in `baseline_measure_points`/`baseline_system_sample_points`) technically scans the full `(flow_id, device_id, mode)`-indexed partition before the `recent` CTE's `LIMIT baseline_n` narrows the window — it is index-bounded (uses `idx_run_baseline`, not a full `run`-table scan) but technically O(partition size) rather than strictly O(baseline_n). Empirically proven fast (46.6ms at ~5101 runs / 301 commits, well under the 150ms budget); documented in-code with the scale test (`tests/integration/test_compare_perf.py`) acting as a regression tripwire. Not rewritten per explicit review instruction — watch, do not treat as urgent.
- **SUGGESTION**: `ruff`/`mypy` are not wired into `pyproject.toml` (no `[tool.ruff]`/`[tool.mypy]` sections) — pre-existing project-wide gap, not introduced by `compare`. Non-blocking; candidate for a follow-up housekeeping change.
- **SUGGESTION**: `Metric.unit` still always defaults to `'ms'` at `run`-ingestion time (inherited from Phase 1, `perf-run.md` WARNING-2). `compare`'s `_compare_measure_family` correctly reads whatever unit `run` persisted — not a `compare`-introduced bug, an inherited upstream fidelity gap.

## Testing

- **Full suite**: 328 passing, 0 failed (`./.venv/bin/pytest -q`), including the 197 pre-existing Phase 1 tests.
- **Layers**: unit (hypothesis-driven pure math), integration (real temp SQLite, no live devices), contract (`--json schema_version=1` shape), golden (pretty output, color forced off), performance (scale test: ~5101 runs / 301 commits).
- **Corner cases covered**: C1–C9 matrix, each with a dedicated RED-then-GREEN test.

## Files & Configuration

- **CLI entry**: `src/perf/cli/commands/compare.py` (typer command, registered in `cli/main.py`)
- **Core composition**: `src/perf/adapters/analyzer_sql.py` (`SqlAnalyzer`)
- **Domain**: `src/perf/domain/regression.py`, `src/perf/domain/statistics.py`, `src/perf/domain/calibration.py`, `src/perf/domain/model.py` (`Verdict` 4-state + `CompareResult`)
- **Adapters**: `src/perf/adapters/store_sqlite.py` (baseline read-models), `src/perf/adapters/registry.py` (`build_analyzer`)
- **Output**: `src/perf/cli/output/compare_pretty.py`, `src/perf/contracts/compare_v1.py`
- **Database**: `src/perf/db/migrations/0002_compare_baseline_index.sql`, mirrored in `src/perf/db/schema.sql`
- **Config**: `src/perf/config/loader.py` (`threshold_pct`, `floors`, `min_baseline_commits`, `warmup_k`, `baseline_n`)
- **Example**: `examples/demo-compare/`
- **Tests**: `tests/{unit,integration,contract,golden}` — compare-scoped files across all layers

## Delivery History

- **PR-A** (`compare/pr-a-domain`, `ba95f42`): pure domain math + config defaults + domain corner cases
- **PR-B** (`compare/pr-b-store-analyzer`, `75fc1c3`): additive index migration + batched baseline read-model + `SqlAnalyzer` + registry, plus 4 post-merge review fixes (NULL `p90_ms` crash, warm-up full-drop test, `baseline_n` clamp, documented scan-cost residual)
- **PR-C** (`compare/pr-c-cli`, `f6de697`): CLI command + renderer + `--json` contract + e2e wiring + perf scale test + UX goldens + CLI corner cases, plus 1 CRITICAL fix (dishonest `too-loose` label) + 1 WARNING fix (weak calibration assertions), both caught and fixed in the same PR-C review cycle
- **All merged to main** on 2026-07-23
- **Verification**: PASS, 0 CRITICAL, 2 WARNING, 2 SUGGESTION (verify report `docs/specs/compare/verify-report.md`)

---

**Consolidated**: 2026-07-23 · archive phase — moving Phase 2 compare-only slice to canonical spec in `openspec/specs/` for future change comparison (`budget-check`, `--calibrate`).
