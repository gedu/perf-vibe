# Specification: `perf run` capability

> SDD spec artifact (Revision 2). Requirements & scenarios for `perf run`.
> Companion documents: [`proposal.md`](./proposal.md) (PRD) · [`design.md`](./design.md) (RFC) · [`tasks.md`](./tasks.md).

## Purpose

`perf run <flow> [n]` drives a performance flow N times using composable, config-selected sources, assembles run context, and persists one run in a single transaction. **Persist-only** — no verdict/auto-compare. Revision 2 makes `FlowDriver`/`SystemSampler`/`MarkerSource` independently optional with a minimum-measurement guarantee, corrects Flashlight ingestion to the real per-sample-time-series shape (aggregate per iteration, keep the raw file, no blob/time-series in the DB), generalizes marker parsing to text + JSON with no hardcoded metric names, and adds direction-aware + regression-enabling metadata so a future `compare` capability can implement classification without touching `run`.

## Scope note (RUN vs COMPARE)

| Concern | Owner |
|---|---|
| Composable sources, min-measurement guarantee, ManualDriver | RUN |
| Flashlight per-iteration aggregation, `raw_report_path` | RUN |
| Marker parsing (text + JSON), `markStart`/`markEnd` guard | RUN |
| Direction metadata per metric; git/device/mode/bundle/n/source metadata; storing ALL iterations | RUN |
| Median-by-commit baseline, dev-bundle exclusion, warm-up discard, min-n gating, threshold+floor, classification (improvement/stable/regression/insufficient-data) | COMPARE (future — forward requirements, not implemented in `run`) |

## Requirements

### Requirement: Composable optional sources with a minimum-measurement guarantee

`FlowDriver`, `SystemSampler`, and `MarkerSource` SHALL each be independently optional, selected by config/flags via the adapter registry. At least one MEASUREMENT source (`SystemSampler` OR `MarkerSource`) MUST be active; otherwise the tool SHALL exit `2` before any device interaction. Phase 1 SHALL ship (a) `MaestroDriver` + `FlashlightSampler` + `AdbLogcatMarkerSource`, and (b) a `ManualDriver` — no automation, instructs the user to perform the flow, waits for confirmation — so the no-Maestro path is built and tested. Flashlight-only-manual and iOS are DOCUMENTED SEAMS (the registry supports them structurally), NOT built in Phase 1.

- **Scenario: No measurement source configured** — GIVEN config selects a FlowDriver but neither SystemSampler nor MarkerSource, WHEN `perf run <flow>` executes, THEN the tool SHALL exit `2` and no device interaction SHALL occur.
- **Scenario: ManualDriver flow** — GIVEN config selects ManualDriver + FlashlightSampler, WHEN `perf run <flow>` executes, THEN the tool SHALL print instructions, wait for user confirmation per iteration, then sample capture proceeds identically to the Maestro path.

### Requirement: Flow execution loop

The tool SHALL drive the named flow via the configured FlowDriver `n` times (default 10), honoring `--mode warm|cold` (warm default; `--restart` forces cold) and device pinning.

- **Scenario: Successful N-iteration run** — GIVEN a configured driver/device and a valid flow name, WHEN `perf run <flow> 10` executes, THEN the flow runs exactly 10 times and per-index outcomes are recorded.
- **Scenario: Device offline aborts the run** — GIVEN the configured device is unreachable, WHEN `perf run <flow>` executes, THEN the tool SHALL abort and exit `3` without invoking the store.

### Requirement: Flashlight (system sample) ingestion, aggregated per iteration

Per iteration, the tool SHALL aggregate `measures[]` plus iteration fields into: `total_time_ms` (iteration.time), `start_time_ms` (iteration.startTime), `fps_avg`, `fps_min`, `ram_avg_mb`, `ram_peak_mb`, `cpu_avg_pct`, `cpu_peak_pct` (CPU total per sample = sum of `cpu.perName`, then averaged/peaked). The tool SHALL store `raw_report_path` (gitignored, prunable `results/` dir) and SHALL NOT ingest the per-sample series or copy the JSON blob into the DB. The tool SHALL NEVER ingest network metrics.

- **Scenario: Per-iteration aggregates captured** — GIVEN a Flashlight results JSON with 10 iterations, each with a `measures[]` series, WHEN samples are parsed, THEN each iteration yields one `system_sample` row with all eight aggregate columns and `raw_report_path` set, with no per-sample series persisted.
- **Scenario: Network fields excluded** — GIVEN the Flashlight JSON contains network transfer fields, WHEN samples are parsed, THEN no network metric SHALL be persisted anywhere in the store.

### Requirement: Marker capture (text + JSON), coverage guard, run-level attachment

The tool SHALL parse react-native-performance markers from `adb logcat -s ReactNativeJS:V` in BOTH forms: `[PERF] <name>: <n>ms` and `[PERF] {json}`. Metric names SHALL be arbitrary — no metric name or app-domain route is hardcoded in the parser. Markers SHALL attach to the RUN, not an iteration. `[PERF-META]` remains context only. A `markStart` without matching `markEnd` SHALL be skipped and SHALL trigger a partial-coverage warning when captured occurrences `n < run.iterations`.

- **Scenario: Both marker forms parsed** — GIVEN logcat emits `[PERF] checkout: 900ms` and `[PERF] {"name":"checkout","value":900}`, WHEN markers are parsed, THEN both normalize into the same run-level metric record shape.
- **Scenario: markStart without markEnd** — GIVEN one iteration emits `markStart` but never `markEnd`, WHEN markers are parsed, THEN the occurrence is skipped and partial coverage is flagged when `n < run.iterations`.

### Requirement: Direction-aware metric metadata (RUN stores / COMPARE consumes)

The metric dimension SHALL record a direction attribute (`higher_is_better` | `lower_is_better`): FPS metrics are `higher_is_better`; duration metrics (marker durations, `total_time_ms`, `start_time_ms`), RAM, and CPU are `lower_is_better`. `run` SHALL persist this correctly and SHALL NOT compute or emit any verdict.

- **Scenario: Direction recorded correctly** — GIVEN a run persists fps_avg, fps_min, total_time_ms, ram_peak_mb, cpu_avg_pct, WHEN the metric dimension is upserted, THEN fps_avg/fps_min carry `higher_is_better` and the rest carry `lower_is_better`.

### Requirement: Run context and regression-enabling metadata

The tool SHALL persist per run: `git_commit`, `git_branch`, `device_key`, `is_dev_bundle` (from `[PERF-META]` only, never inferred), `mode` (warm|cold), `iterations` (n), and `source` (active driver/sampler/marker combination) — sufficient for a future COMPARE to compute median-by-commit baselines, exclude dev bundles, group by device, and separate warm/cold series.

- **Scenario: Full metadata persisted** — GIVEN a cold run of 8 iterations on a pinned device with a release bundle, WHEN the run is persisted, THEN the row stores `mode=cold`, `iterations=8`, `device_key`, `git_commit`, `git_branch`, `is_dev_bundle=0`, `source`.

### Requirement: All-iterations storage (COMPARE-scope stat policies deferred)

`run` SHALL store every iteration and SHALL NOT discard, average away, or drop any iteration at ingestion, regardless of count. Warm-up discard and minimum-sample-size gating are COMPARE-scope statistical policies applied at query/verdict time, not ingestion.

- **Scenario: All iterations retained regardless of count** — GIVEN a run with `n=3`, WHEN the run is persisted, THEN all 3 iterations are stored with no suppression; any min-n rejection is deferred to COMPARE.

### Requirement: CLI options and configuration surface

The tool SHALL accept: flow name, iterations `n` (default 10), `--restart` (forces cold; warm is default), device pinning (`--device` or `MAESTRO_DEVICE`), secret forwarding to the driver's env mechanism (e.g. `PASSWORD`), `--db <path>`, `--config <path>`, `--json`, `--no-color`. The bundle identifier SHALL come from config, never hardcoded.

- **Scenario: Bundle id from config, secret not logged** — GIVEN `--config` supplies a bundle identifier and `PASSWORD` is set in the environment, WHEN `perf run` executes, THEN the configured bundle id is used with none hardcoded, and `PASSWORD` is forwarded to the driver without appearing in stdout/stderr.

### Requirement: Single-transaction ingestion with rollback

The tool SHALL persist one run and all iteration/measure/system_sample rows in one transaction; dimension upserts (device/flow/metric) SHALL be idempotent via `INSERT ... ON CONFLICT`.

- **Scenario: Partial mid-run failure rolls back fully** — GIVEN the flow driver fails after 3 of 10 iterations, WHEN the ingestion transaction is attempted, THEN no run/iteration/measure/system_sample row exists afterward, and dimension rows remain unaffected.

### Requirement: Exit-code discipline

The tool SHALL exit `0` on success, `2` on usage error (bad arguments, no measurement source configured), `3` on runtime/tooling failure (device offline, driver failure, zero markers AND zero samples captured, transaction failure). `run` SHALL NEVER exit `1`.

- **Scenario: No measurement source is a usage error** — GIVEN config selects no SystemSampler and no MarkerSource, WHEN `perf run` is invoked, THEN the tool exits `2`, not `3`.
- **Scenario: No data captured** — GIVEN the flow completes but both active sources yield zero data, WHEN the run finishes, THEN the tool exits `3` and no run row is written.

### Requirement: Hexagonal boundary enforcement (registry-selected, optional-capable adapters)

The domain layer SHALL NOT import any adapter module. Adapters (FlowDriver, MarkerSource, SystemSampler, RunContextProvider, Store, Clock) SHALL be selected by name via a config-driven registry, which SHALL support any adapter being absent except where the minimum-measurement guarantee applies.

- **Scenario: Domain has no adapter imports** — GIVEN the `domain/` package source, WHEN static import analysis runs, THEN no `domain/` module imports from `adapters/`.
