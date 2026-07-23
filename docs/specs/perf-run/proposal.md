# Proposal (PRD): `perf run` capability (Phase 1 first slice)

> Spec-Driven Development artifact for the `perf run` capability.
> Exported from the SDD record so it is versioned and publicly accessible.
> Companion documents: [`spec.md`](./spec.md) · [`design.md`](./design.md) (RFC) · [`tasks.md`](./tasks.md).

## Intent

Developers need to catch mobile performance regressions **before merge**, locally, with retention beyond Embrace's 3/14/30-day window. `perf run` is the foundational capture command: without a trustworthy stored run there is nothing to compare, show, or gate. It is the first command built, so it also introduces the CLI skeleton and SQLite store every later command inherits.

## Scope

### In scope
- `perf run <flow> [n] [--restart] [--mode warm|cold]`: drive a Maestro flow N times on the configured Android device.
- Capture in-app `[PERF]` markers (logcat) + Flashlight per-iteration system samples (FPS/CPU/RAM; **no network**).
- Assemble `RunContext`: bash env facts (git commit/branch, device, build variant, tool version) + app `[PERF-META]` line (app version, `is_dev_bundle`, bundle source).
- Persist exactly ONE run (+ iteration/measure/system_sample) in a single transaction; roll back + exit `3` on any partial failure.
- Record the `is_dev_bundle` trust flag faithfully. Minimal own-confirmation output (pretty + `--json`).
- typer CLI skeleton (`perf.cli:main`, `run` subcommand) as the shared pattern.

### Out of scope
- Auto-compare / verdict rendering (the use-case stays pure; composed at the CLI layer later once `compare` exists).
- Analyzer, regression math, percentiles, baseline/threshold logic, verdict-rendering reporter.
- Any second-platform adapter or speculative config for non-BCP platforms.

## Approach

Hexagonal (ports & adapters): a pure domain (`model.py`, `ports.py` Protocols), a `RunFlowUseCase` orchestrating ports, and one BCP adapter per port. Ports touched: `FlowDriver`→`MaestroDriver`, `MarkerSource`→`AdbLogcatMarkerSource`, `SystemSampler`→`FlashlightSampler`, `RunContextProvider` (bash + `[PERF-META]` composite), `Store`→`SqliteStore` (single-transaction ingestion + `PRAGMA user_version` migration runner, WAL + busy_timeout), `Clock`→`SystemClock`. Invariant: markers hang off the **run**, not the iteration.

## Affected areas

| Area | Impact | Description |
|---|---|---|
| `pyproject.toml`, `src/perf/cli/` | New | typer app, `run` command, output plumbing |
| `src/perf/application/run_flow.py` | New | pure use-case |
| `src/perf/domain/{model,ports}.py` | New | value objects + Protocols |
| `src/perf/adapters/*` | New | BCP adapters |
| `src/perf/db/` | New | schema.sql + migrations |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Partial-failure leaves a half-written run | Med | `BEGIN`/`COMMIT` wrap all fact rows; rollback + exit 3 test; dimension upsert idempotent |
| Marker parsing / template / `markStart` without `markEnd` | Med | Recorded-fixture tests; partial-coverage flag; `[PERF]` vs `[PERF-META]` split |
| Network fields ingested from Flashlight | Low | Hard adapter boundary, explicit no-network test |
| Exit `2` (usage) vs `3` (runtime) conflated | Med | Distinct codes, documented + tested |

## Rollback plan

Greenfield: revert the feature branch. No migrations shipped elsewhere; the local `.db` is gitignored and discardable.

## Dependencies

- `typer` (justified exception to the stdlib-first policy — see [`design.md`](./design.md)). External tools: `adb`, `maestro`, `flashlight`.

## Success criteria

- [ ] `perf run <flow> n` stores exactly one run with N iterations + captured measures/samples.
- [ ] Any partial failure writes zero rows and exits `3`.
- [ ] `is_dev_bundle` recorded only from `[PERF-META]`.
- [ ] `--json` emits machine-parseable confirmation; a non-TTY stderr nudge is present.

## Decisions resolved at proposal time

- **Persist-only** — the use-case does not auto-compare or render a verdict.
- **CLI framework: `typer`** — a conscious exception to stdlib-first, justified by `--json` discoverability in the help.
- **Output default** — explicit `--json` flag + a one-line stderr nudge on non-TTY (not auto-JSON-on-pipe).
