# Specification: `perf run` capability (Current — Phase 1 SHIPPED)

**Consolidated from**: SDD revision 2 (#32), verified and merged to main in PRs #1–#3.

**Status**: Phase 1 COMPLETE. Capability ships with the following behavior as the authoritative current spec. Future SDD changes reference and compare against this spec.

## Purpose

`perf run <flow> [n]` drives a performance flow N times using composable, config-selected sources, assembles run context, and persists one run in a single transaction. **Persist-only** — no verdict/auto-compare. The implementation makes `FlowDriver`/`SystemSampler`/`MarkerSource` independently optional with a minimum-measurement guarantee, correctly ingests Flashlight per-sample-time-series by aggregating per iteration and storing the raw file path, generalizes marker parsing to text + JSON with no hardcoded metric names, and records direction-aware + regression-enabling metadata so a future `compare` capability can implement classification without touching `run`.

## Scope (RUN vs COMPARE)

| Concern | Owner | Status |
|---|---|---|
| Composable sources, min-measurement guarantee, ManualDriver | RUN | SHIPPED ✓ |
| Flashlight per-iteration aggregation, `raw_report_path` | RUN | SHIPPED ✓ |
| Marker parsing (text + JSON), `markStart`/`markEnd` guard | RUN | SHIPPED ✓ |
| Direction metadata per metric; git/device/mode/bundle/n/source metadata; storing ALL iterations | RUN | SHIPPED ✓ |
| Median-by-commit baseline, dev-bundle exclusion, warm-up discard, min-n gating, threshold+floor, classification | COMPARE | SHIPPED ✓ — see `openspec/specs/compare.md` |

## Requirements

### Requirement: Composable Optional Sources with Minimum-Measurement Guarantee

**Status**: SHIPPED ✓

`FlowDriver`, `SystemSampler`, and `MarkerSource` SHALL each be independently optional, selected by config/flags via the adapter registry. At least one MEASUREMENT source (`SystemSampler` OR `MarkerSource`) MUST be active; otherwise the tool SHALL exit `2` before any device interaction. Phase 1 ships (a) `MaestroDriver` + `FlashlightSampler` + `AdbLogcatMarkerSource`, and (b) a `ManualDriver` — no automation, instructs the user to perform the flow, waits for confirmation — so the no-Maestro path is built and tested. Flashlight-only-manual and iOS are documented structural seams in the registry, NOT built in Phase 1.

#### Scenario: No measurement source configured
- GIVEN: config selects a FlowDriver but neither SystemSampler nor MarkerSource
- WHEN: `perf run <flow>` executes
- THEN: the tool SHALL exit `2`, and no device interaction SHALL occur

#### Scenario: ManualDriver flow
- GIVEN: config selects ManualDriver + FlashlightSampler
- WHEN: `perf run <flow>` executes
- THEN: the tool SHALL print instructions, wait for user confirmation per iteration, then sample capture proceeds identically to the Maestro path

### Requirement: Flow Execution Loop

**Status**: SHIPPED ✓

The tool SHALL drive the named flow via the configured FlowDriver `n` times (default 10), honoring `--mode warm|cold` (warm default; `--restart` forces cold) and device pinning.

#### Scenario: Successful N-iteration run
- GIVEN: a configured driver/device and a valid flow name
- WHEN: `perf run <flow> 10` executes
- THEN: the flow runs exactly 10 times and per-index outcomes are recorded

#### Scenario: Device offline aborts the run
- GIVEN: the configured device is unreachable
- WHEN: `perf run <flow>` executes
- THEN: the tool SHALL abort and exit `3` without invoking the store

### Requirement: Flashlight (System Sample) Ingestion, Aggregated Per Iteration

**Status**: SHIPPED ✓

Per iteration, the tool SHALL aggregate `measures[]` plus iteration fields into: `total_time_ms` (iteration.time), `start_time_ms` (iteration.startTime), `fps_avg`, `fps_min`, `ram_avg_mb`, `ram_peak_mb`, `cpu_avg_pct`, `cpu_peak_pct` (CPU total per sample = sum of `cpu.perName`, then averaged/peaked). The tool SHALL store `raw_report_path` (gitignored, prunable `results/` dir) and SHALL NOT ingest the per-sample series or copy the JSON blob into the DB. The tool SHALL NEVER ingest network metrics.

#### Scenario: Per-iteration aggregates captured
- GIVEN: a Flashlight results JSON with 10 iterations, each with a `measures[]` series
- WHEN: samples are parsed
- THEN: each iteration yields one `system_sample` row with all eight aggregate columns and `raw_report_path` set, with no per-sample series persisted

#### Scenario: Network fields excluded
- GIVEN: the Flashlight JSON contains network transfer fields
- WHEN: samples are parsed
- THEN: no network metric SHALL be persisted anywhere in the store

### Requirement: Marker Capture (Text + JSON), Coverage Guard, Run-Level Attachment

**Status**: SHIPPED ✓

The tool SHALL parse react-native-performance markers from `adb logcat -s ReactNativeJS:V` in BOTH forms: `[PERF] <name>: <n>ms` and `[PERF] {json}`. Metric names SHALL be arbitrary — no metric name or app-domain route is hardcoded in the parser. Markers SHALL attach to the RUN, not an iteration. `[PERF-META]` remains context only. A `markStart` without matching `markEnd` SHALL be skipped and SHALL trigger a partial-coverage warning when captured occurrences `n < run.iterations`.

#### Scenario: Both marker forms parsed
- GIVEN: logcat emits `[PERF] checkout: 900ms` and `[PERF] {"name":"checkout","value":900}`
- WHEN: markers are parsed
- THEN: both normalize into the same run-level metric record shape

#### Scenario: markStart without markEnd
- GIVEN: one iteration emits `markStart` but never `markEnd`
- WHEN: markers are parsed
- THEN: the occurrence is skipped and partial coverage is flagged when `n < run.iterations`

### Requirement: Direction-Aware Metric Metadata (RUN stores / COMPARE consumes)

**Status**: SHIPPED ✓

The metric dimension SHALL record a `direction` attribute (`higher_is_better` | `lower_is_better`): FPS metrics are `higher_is_better`; duration metrics (marker durations, `total_time_ms`, `start_time_ms`), RAM, and CPU are `lower_is_better`. `run` SHALL persist this correctly and SHALL NOT compute or emit any verdict.

#### Scenario: Direction recorded correctly
- GIVEN: a run persists fps_avg, fps_min, total_time_ms, ram_peak_mb, cpu_avg_pct
- WHEN: the metric dimension is upserted
- THEN: fps_avg/fps_min carry `higher_is_better` and the rest carry `lower_is_better`

### Requirement: Run Context and Regression-Enabling Metadata

**Status**: SHIPPED ✓

The tool SHALL persist per run: `git_commit`, `git_branch`, `device_key`, `is_dev_bundle` (from `[PERF-META]` only, never inferred), `mode` (warm|cold), `iterations` (n), and `source` (active driver/sampler/marker combination) — sufficient for a future COMPARE to compute median-by-commit baselines, exclude dev bundles, group by device, and separate warm/cold series.

#### Scenario: Full metadata persisted
- GIVEN: a cold run of 8 iterations on a pinned device with a release bundle
- WHEN: the run is persisted
- THEN: the row stores `mode=cold`, `iterations=8`, `device_key`, `git_commit`, `git_branch`, `is_dev_bundle=0`, `source`

### Requirement: All-Iterations Storage (COMPARE-scope stat policies deferred)

**Status**: SHIPPED ✓

`run` SHALL store every iteration and SHALL NOT discard, average away, or drop any iteration at ingestion, regardless of count. Warm-up discard and minimum-sample-size gating are COMPARE-scope statistical policies applied at query/verdict time, not ingestion.

#### Scenario: All iterations retained regardless of count
- GIVEN: a run with `n=3`
- WHEN: the run is persisted
- THEN: all 3 iterations are stored with no suppression; any min-n rejection is deferred to COMPARE

### Requirement: CLI Options and Configuration Surface

**Status**: SHIPPED ✓

The tool SHALL accept: flow name, iterations `n` (default 10), `--restart` (forces cold; warm is default), device pinning (`--device` or `MAESTRO_DEVICE`), secret forwarding to the driver's env mechanism (e.g. `PASSWORD`), `--db <path>`, `--config <path>`, `--json`, `--no-color`. The bundle identifier SHALL come from config, never hardcoded.

#### Scenario: Bundle id from config, secret not logged
- GIVEN: `--config` supplies a bundle identifier and `PASSWORD` is set in the environment
- WHEN: `perf run` executes
- THEN: the configured bundle id is used with none hardcoded, and `PASSWORD` is forwarded to the driver without appearing in stdout/stderr

### Requirement: Single-Transaction Ingestion with Rollback

**Status**: SHIPPED ✓

The tool SHALL persist one run and all iteration/measure/system_sample rows in one transaction; dimension upserts (device/flow/metric) SHALL be idempotent via `INSERT ... ON CONFLICT`.

#### Scenario: Partial mid-run failure rolls back fully
- GIVEN: the flow driver fails after 3 of 10 iterations
- WHEN: the ingestion transaction is attempted
- THEN: no run/iteration/measure/system_sample row exists afterward, and dimension rows remain unaffected

### Requirement: Exit-Code Discipline

**Status**: SHIPPED ✓

The tool SHALL exit `0` on success, `2` on usage error (bad arguments, no measurement source configured), `3` on runtime/tooling failure (device offline, driver failure, zero markers AND zero samples captured, transaction failure). `run` SHALL NEVER exit `1`.

#### Scenario: No measurement source is a usage error
- GIVEN: config selects no SystemSampler and no MarkerSource
- WHEN: `perf run` is invoked
- THEN: the tool exits `2`, not `3`

#### Scenario: No data captured
- GIVEN: the flow completes but both active sources yield zero data
- WHEN: the run finishes
- THEN: the tool exits `3` and no run row is written

### Requirement: Hexagonal Boundary Enforcement (Registry-Selected, Optional-Capable Adapters)

**Status**: SHIPPED ✓

The domain layer SHALL NOT import any adapter module. Adapters (FlowDriver, MarkerSource, SystemSampler, RunContextProvider, Store, Clock) SHALL be selected by name via a config-driven registry, which SHALL support any adapter being absent except where the minimum-measurement guarantee applies.

#### Scenario: Domain has no adapter imports
- GIVEN: the `domain/` package source
- WHEN: static import analysis runs
- THEN: no `domain/` module imports from `adapters/`

## Known Limitations & Future Work

### Designation (python-style skill)

- **ruff** (lint + format) and **mypy** (type checking) are designated by the python-style skill but not yet wired into `pyproject.toml` (no `[tool.ruff]` or `[tool.mypy]` blocks present). Treat as a gap to address in Phase 2+ housekeeping.

### Test Coverage

- **SECRET-SCRUB blind spot (WARNING-1)**: Maestro + Flashlight path (`TOOL_MANAGED` mode) only. See verify report #47, warning-1. Fix targeted: also scrub against inner argv. Test added for `DRIVER_MANAGED`; `TOOL_MANAGED` secret-leak test needed.
- **Marker unit not persisted (WARNING-2)**: metric.unit always defaults to 'ms' in the DB despite schema support. Low impact; future `COMPARE` won't misread, but DB fidelity gap exists.

## Testing

- **Full suite**: 197 collected tests across unit/integration/contract/golden layers.
- **Core paths verified**: flow loop, marker parsing (text+JSON+coverage guard), Flashlight aggregation+network-exclusion, run-context+dev-bundle recording, single-txn+rollback, exit 0/2/3 never 1, --json schema_version=1, hexagonal boundary (AST guards), composable sources + min-measurement guard.
- **Corner cases covered**: markStart-no-markEnd, arbitrary metric names, zero-data→exit3, device-offline→exit3+zero rows, unpinned-multi-device pinning, Flashlight status!=SUCCESS handling, missing adb/git degradation, dev-bundle recording, UTC timestamps, WAL+busy_timeout, direction metadata, SQL-injection round-trip.

## Files & Configuration

- **CLI entry**: `src/perf/cli/run.py` (typer command)
- **Core use-case**: `src/perf/application/run_flow.py`
- **Domain**: `src/perf/domain/model.py`, `src/perf/domain/ports.py` (Protocol-based ports)
- **Adapters**: `src/perf/adapters/{store_sqlite,driver_maestro,driver_manual,sampler_flashlight,markers_adb_logcat,context_bash_perfmeta,registry}.py`
- **Database**: `src/perf/db/schema.sql` (schema.sql is reference; migrations run from user_version 0)
- **Config**: `pyproject.toml` (bundle identifier, dev-dependency tools)
- **Tests**: `tests/{unit,integration,contract,golden}` across 20+ test files

## Delivery History

- **PR #1** (perf-run/pr1-foundation): Schema + domain + apply framework
- **PR #2** (perf-run/pr2-store-adapters): SqliteStore + 6 adapters + review fixes (device pinning, Flashlight status, missing binary, diagnostics)
- **PR #3** (perf-run/pr3-app-cli): application/ + cli/ + contracts + config
- **All merged to main** on 2026-07-22
- **Verification**: PASS WITH WARNINGS (verify report #47)

---

**Consolidated**: 2026-07-23 · archive phase — moving Phase 1 to canonical spec in openspec/specs/ for future change comparison.
