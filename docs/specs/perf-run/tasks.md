# Tasks: `perf run` capability

> SDD task breakdown & execution record (Revision 2).
> Companion documents: [`proposal.md`](./proposal.md) (PRD) · [`spec.md`](./spec.md) · [`design.md`](./design.md) (RFC).
> Status: **all phases complete and merged** (PR #1 → #2 → #3). Suite: 197 tests passing; verified via an adversarial review pass.

## Delivery

Three sequential PRs to `main` (chosen to keep each review focused; the full change is ~1500 lines):

| PR | Scope | Phases |
|---|---|---|
| **PR1** | Foundation — schema + pure domain | 1–2 |
| **PR2** | Store + BCP adapters (+ resilience review fixes) | 3–4 |
| **PR3** | Application use-case + typer CLI + banner (+ review fixes) | 5–7 |

## PR1 — foundation

### Phase 1: Schema
- [x] `db/schema.sql` — star schema + `run_metric_summary` view; the Rev2 `system_sample` aggregates, `run.raw_report_path`, `metric.higher_is_better`.
- [x] `db/migrations/0001_init.sql` — the single initial migration (corrected directly to the final shape; no deployed DB existed, so no rename-migration).
- [x] `tests/integration/test_schema.py` — schema applies cleanly; full drift guard between `schema.sql` and `0001` across all tables.

### Phase 2: Domain (pure)
- [x] `domain/model.py` — frozen dataclasses (Run, Measure, SystemSample, Metric, Device, Flow, Marker, RunContext, Verdict, ExecutionPlan, DriverCommand, SamplerCommand, CaptureSpec, DriverResult, MarkerParseResult, SystemSampleParseResult).
- [x] `domain/ports.py` — the `typing.Protocol` ports.
- [x] `tests/unit/test_model.py`, `tests/unit/test_domain_boundary.py` — immutability + the hexagonal boundary guard (hardened to catch relative/package adapter imports).

## PR2 — store + adapters

### Phase 3: SqliteStore + migration runner
- [x] `adapters/store_sqlite.py` — pragmas (WAL/foreign_keys/busy_timeout), the `PRAGMA user_version` migration runner (package-only migration files), the single-transaction ingestion (rollback → zero rows), dimension upserts (idempotent).
- [x] `tests/integration/test_store_ingestion.py`, `test_store_migrations.py`, `tests/unit/test_store_migration_version.py`.

### Phase 4: Adapters (one per port, BCP + Manual)
- [x] `sampler_flashlight.py` — per-iteration aggregation; `wrap()` builds the flashlight argv; honors `status`; never reads a network field.
- [x] `markers_adb_logcat.py` — both `[PERF]` text + JSON forms; arbitrary names; device pinning; `markStart`-without-`markEnd` guard.
- [x] `driver_maestro.py` — `command()` validates flow before spawn; `drive()` executes the plan + owns the logcat lifecycle.
- [x] `driver_manual.py` — no automation, prompt-driven, same logcat lifecycle.
- [x] `context_bash_perfmeta.py` — git/adb argv facts + `[PERF-META]` parsing; degrades to `None` on missing binary.
- [x] `process.py` — shared argv-list subprocess helper (never `shell=True`); secret scrubbing.
- [x] `registry.py` — name→factory maps; each source independently optional.
- [x] Fixtures + integration tests for each adapter.

### Phase 4b: Resilience-review fixes (found by an adversarial review, all fixed with tests)
- [x] Device pinning + a `capture_failed` signal distinct from "no markers".
- [x] Honor Flashlight `status` (never aggregate a failed run as success).
- [x] Catch missing `adb`/`git` binary → degrade to `None` (never exit 1).
- [x] `DriverResult.diagnostics` carries scrubbed stderr so failures explain themselves.

## PR3 — application + CLI

### Phase 5: Application
- [x] `application/run_flow.py` — `RunFlowUseCase` (pure): compose the ExecutionPlan across the 4 shapes, min-measurement guard (exit 2), persist one run, map failures to exit 0/2/3 (never 1).
- [x] `config/loader.py` — layered config.
- [x] `contracts/json_v1.py` — the versioned `--json` confirmation payload.

### Phase 6: CLI
- [x] `cli/main.py` + `commands/run.py` — typer app, global flags, `run` options; secret read from `PASSWORD` env only.
- [x] `cli/output/` — pretty + JSON reporters, stderr nudge on non-TTY.
- [x] `cli/banner.py` — TTY/color-gated ASCII banner, never in `--json`.

### Phase 7: Verification
- [x] `tests/fakes.py` — port fakes (FakeDriver/FakeStore/FrozenClock/…).
- [x] Use-case tests (4 shapes, exit-code matrix, never-1), `--json` contract test, golden test.
- [x] Post-review fixes: uniform driver build kwargs (fixed a broken manual-driver path), guarded `store.close()` and rendering, config `no_color` honored, env-only secret, unknown-adapter → exit 2, secret redaction on the nested-testCommand path, marker unit persisted.
- [x] Coverage raised to ~96%; config-loader layering covered.
