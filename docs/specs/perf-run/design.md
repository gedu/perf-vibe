# Design (RFC): `perf run` capability

> SDD design artifact (Revision 2). The technical "how" for `perf run`.
> Companion documents: [`proposal.md`](./proposal.md) (PRD) · [`spec.md`](./spec.md) · [`tasks.md`](./tasks.md).
> `perf run` remains **persist-only** (no Analyzer/verdict).

## What changed vs Revision 1

Revision 1 assumed one Maestro driver + one Flashlight sampler always paired, a flat per-iteration sampler, and a hardcoded marker template. Revision 2 makes `FlowDriver`/`SystemSampler`/`MarkerSource` independently optional with a min-measurement guarantee, adds a `ManualDriver`, resolves the Flashlight-wraps-Maestro coupling via a compose-time `ExecutionPlan` (no composite adapter), corrects Flashlight ingestion to per-iteration aggregation of a per-sample time-series (+ `raw_report_path`, no blob/series in DB), generalizes marker parsing to text + JSON with arbitrary names, and adds direction-aware metric metadata + regression-enabling run metadata.

## 1. The key architectural problem: Flashlight WRAPS Maestro, yet ports must stay orthogonal

The real invocation couples two concerns in ONE OS process:

```
flashlight test --testCommand "maestro test <flow>" --iterationCount N --resultsFilePath X
# plus, in parallel:  adb logcat -s ReactNativeJS:V
```

Flashlight (a `SystemSampler` concern) both DRIVES iterations (a `FlowDriver` concern) and produces the metrics artifact. But the four supported shapes need the ports independently optional:

| Shape | Composed OS command | Iteration owner |
|---|---|---|
| Maestro + Flashlight + markers (BCP full) | `flashlight test --testCommand "maestro test <flow>" --iterationCount N --resultsFilePath X` + parallel logcat | Flashlight (`--iterationCount`) |
| Maestro, no Flashlight | `maestro test <flow>` run N times by the driver | Driver loop |
| ManualDriver (+ markers) | no automated command; prompt user per iteration + parallel logcat | Driver prompt loop |
| markers-only | ManualDriver + logcat, no flashlight | Driver prompt loop |

### Solution: compose-time `ExecutionPlan` (pure) + I/O-time `drive()` — NO composite adapter

The coupling is resolved as PURE DATA in the use-case, not by any adapter knowing another. Each active adapter contributes a fragment; the use-case (pure) assembles an `ExecutionPlan` value object; the driver (I/O) executes whatever command it is handed, agnostic to Flashlight/Maestro.

**Split of responsibility (the crux):**
- **FlowDriver** = *produce the raw run*. Contributes the inner test command (pure `command()`), and owns the OS process + parallel-logcat lifecycle (`drive(plan)`), because only it knows flow timing (when to start/stop logcat). Agnostic to WHAT `plan.command` is.
- **SystemSampler** = *wrap + interpret the system artifact*. Contributes an optional command-wrapper + the `results_path` it will read (pure `wrap()`), and later parses that file (`parse()`). Flashlight's `--iterationCount` means when it wraps it OWNS the loop → it declares `manages_iterations=True`.
- **MarkerSource** = *what to capture + how to parse*. Contributes the logcat capture spec (pure `capture_spec()`) and parses the buffer the driver returns (`parse()`).

Because Flashlight declares `manages_iterations`, `ExecutionPlan.loop_mode` selects `TOOL_MANAGED` (single spawn of the flashlight command) vs `DRIVER_MANAGED` (driver loops N times over the inner maestro command, or prompts N times for manual). The logcat lifecycle uniformly wraps the drive step. **Domain imports no adapter**; the coupling lives only in value objects + use-case composition.

**Composite adapter? No.** Each adapter implements exactly ONE port. `FlashlightSampler` having both `wrap()` (compose-time, pure) and `parse()` (read-time, I/O) is a single port with two methods, not a composite; same for `FlowDriver` (`command()` pure + `drive()` I/O). The only shared adapter-internal code is a non-port `adapters/process.py` helper (argv spawn + parallel capture) reused by drivers — not a port, no domain impact.

### Component / sequence (BCP full path)

```
cli/commands/run.py → config.loader → registry.build_{driver,sampler,marker,context,store,clock}
  └─ RunFlowUseCase.execute(flow, n, mode, restart)          [PURE orchestration]
       1. guard: sampler or marker active? else UsageError → exit 2 (no device touch)
       2. inner   = driver.command(flow, mode=…, restart=…, env=…)   # ["maestro","test",flow,"--env",…] | None (manual)
       3. results = results_dir/f"{flow}-{mode}-{ts}.json"  (if sampler)
       4. wrap    = sampler.wrap(inner, iterations=n, restart, results)  # flashlight argv + manages_iterations=True | None
       5. command = wrap.argv if wrap else inner.argv
          loop    = TOOL_MANAGED if (wrap and wrap.manages_iterations) else DRIVER_MANAGED
       6. capture = marker.capture_spec() if marker else None          # adb logcat argv
       7. plan    = ExecutionPlan(command, inner, loop, n, capture, results)
       8. result  = driver.drive(plan)   # I/O: logcat start → spawn/loop/prompt → logcat stop
                     device offline / driver fail → RuntimeError → exit 3 (store untouched)
       9. samples = sampler.parse(results) if sampler else []
          mres    = marker.parse(result.logcat_lines, iterations=n) if marker else EMPTY
      10. if not samples and not mres.markers → RuntimeError → exit 3 (no run row)
      11. ctx = context.context();  now = clock.now_utc_iso()
      12. run_id = store.save_run(ctx, flow, n, mode, source, mres.markers, samples, results)  # single txn
      13. return RunSummary(run_id, n, metrics_captured, mres.partial_coverage, ctx.is_dev_bundle)
  └─ JsonReporter | PrettyReporter → exit 0
```

Manual path: `inner.argv=None`, `command=None`, `loop=DRIVER_MANAGED`; `drive()` prompts N times while logcat captures. Manual + Flashlight uses Flashlight `measure` (documented seam) → `wrap()` returns `None` in Phase 1, so manual is wired only with markers.

## 2. Schema (fresh-DB `schema.sql` + numbered migrations)

`system_sample` is the per-iteration aggregate table (keyed by `iteration_id`) — one row per iteration, extended with the real Flashlight aggregates:

```sql
-- system_sample aggregate columns
total_time_ms REAL, start_time_ms REAL,
fps_avg REAL, fps_min REAL, ram_avg_mb REAL, ram_peak_mb REAL,
cpu_avg_pct REAL, cpu_peak_pct REAL
-- direction metadata on the metric dimension (0 = lower_is_better, 1 = higher_is_better)
metric.higher_is_better INTEGER NOT NULL DEFAULT 0
-- run-level reference to the Flashlight JSON on disk (TEXT path, NOT a blob, NOT the series)
run.raw_report_path TEXT
```

`raw_report_path` placement: Flashlight emits exactly ONE results JSON per run (all N iterations in one file), so it is a RUN-scoped artifact → placed on `run` (normalized), reconciled with any per-sample spec wording via join.

> Implementation note: because no database was ever deployed with the thin Revision-1 shape, the corrected schema was folded directly into the initial migration `0001` rather than shipped as a `0002` rename-migration. The migration **runner** (below) remains as infrastructure for the first *real* future migration.

**Migration runner** (`adapters/store_sqlite.py`):

```
connect(): PRAGMA foreign_keys=ON; journal_mode=WAL; busy_timeout=<ms>
_migrate(conn):
  v = PRAGMA user_version
  for n, path in sorted numbered migrations (NNNN_*.sql):
      if n > v:
          with one transaction:
              conn.executescript(read path)
              conn.execute(f"PRAGMA user_version = {n}")   # int from filename (validated), PRAGMA can't bind
```

The filename integer is validated (digits-only) so the f-string is injection-safe. Migration files are loaded ONLY from the package's own `db/migrations/`, never a user path.

## 3. FlashlightSampler (SystemSampler adapter)

`wrap(inner, iterations, restart, results_path)` → `SamplerCommand(argv=["flashlight","test","--testCommand", shlex.join(inner.argv), "--iterationCount", str(n), "--resultsFilePath", str(results_path), *("--skipRestart" if not restart else [])], results_path, manages_iterations=True)`.

`parse(results_path)` reads the JSON. Per `iterations[i]`: `total_time_ms=time`, `start_time_ms=startTime`; over `measures[]`: `fps_avg=mean(fps)`, `fps_min=min(fps)`, `ram_avg_mb=mean(ram)`, `ram_peak_mb=max(ram)`; per-sample CPU total `= sum(cpu.perName.values())` → `cpu_avg_pct=mean`, `cpu_peak_pct=max`. Empty `measures[]` → metric fields `None` but time/startTime still recorded. Honors the top-level and per-iteration `status` (a non-SUCCESS run is never silently aggregated). **Hard boundary: never read/ingest any network field.** Does NOT ingest the per-sample series and does NOT copy the JSON into the DB — only records `raw_report_path`.

## 4. AdbLogcatMarkerSource (MarkerSource adapter)

`capture_spec()` → `CaptureSpec(argv=["adb", ("-s", <device>)?, "logcat", "-s", "ReactNativeJS:V"])` (device-pinned when configured). `parse(lines, iterations)`:
- Text form `[PERF] <name>: <n>ms` via regex; brace form `[PERF] {json}` via `json.loads` (never `eval`). Both normalize to the SAME run-level `Marker(name, value, unit)` shape.
- Metric names ARBITRARY — no name/route hardcoded.
- `markStart` without `markEnd` → occurrence skipped; when captured occurrences `n < run.iterations` → `partial_coverage=True`.
- `[PERF-META]` lines are context only (consumed by `RunContextProvider`, not markers). Markers attach to the RUN.

## 5. RunFlowUseCase (application — pure, no I/O, no Analyzer)

Deps = `FlowDriver`, `SystemSampler|None`, `MarkerSource|None`, `RunContextProvider`, `Store`, `Clock`. Orchestration = §1 sequence. Enforces the min-measurement guard (exit 2, before device). Single-transaction persist; any exception → full rollback → 0 fact rows → exit 3. Exit codes 0/2/3, **never 1**. No Analyzer/verdict; a future auto-compare is a CLI-layer seam (`commands/run.py` may chain a future `CompareUseCase` after success), not a use-case dep.

## 6. Config / flags (layering: CLI > env > config file > defaults)

- Adapter selection by registry name: `driver=maestro|manual`, `sampler=flashlight|null`, `marker=adb-logcat|null`.
- `BUNDLE_ID` from config, NEVER hardcoded.
- Device pinning `--device` / `MAESTRO_DEVICE`; iterations `n` default 10; `--restart` forces cold (Flashlight `--skipRestart` default = warm).
- Secret forwarding: `PASSWORD` → driver env mechanism (maestro `--env`), forwarded via env, NEVER printed to stdout/stderr.
- Globals: `--db`, `--config`, `--json` (carries `schema_version`; pretty view lossy, never parsed), `--no-color` (+ `NO_COLOR` + TTY). Non-TTY without `--json` → one stderr nudge.
- `.db` and raw Flashlight JSONs (`results/`) are local + gitignored.

## 7. Testing (TDD RED→GREEN)

| Layer | Target | Approach |
|---|---|---|
| Unit | RunFlowUseCase: happy path, min-measurement→exit 2, device offline→exit 3, no-data→exit 3, rollback→0 rows | Fake every port |
| Unit | ExecutionPlan composition: 4 shapes select correct command + loop_mode | pure, no I/O |
| Integration | FlashlightSampler.parse aggregation + no-network + empty-measures + status | trimmed fixture JSON |
| Integration | AdbLogcatMarkerSource.parse both forms + arbitrary names + dangling markStart → partial_coverage | captured logcat fixture |
| Integration | SqliteStore single-txn (partial-failure→0 rows, dimension idempotency) + migration runner | real temp SQLite |
| Integration | ManualDriver drive() prompt loop without a device | fake stdin |
| Contract | `run --json` confirmation subset stable | snapshot; fails on shape change w/o `schema_version` bump |
| Golden | pretty confirmation, color forced off | `tests/golden/*.txt` |

## Key ports (`domain/ports.py` — Protocols)

- `FlowDriver.command(flow,*,mode,restart,env) -> DriverCommand` (pure) · `drive(plan) -> DriverResult` (I/O)
- `SystemSampler.wrap(inner,*,iterations,restart,results_path) -> SamplerCommand | None` (pure) · `parse(results_path) -> SystemSampleParseResult` (I/O)
- `MarkerSource.capture_spec() -> CaptureSpec | None` (pure) · `parse(lines,*,iterations) -> MarkerParseResult` (pure)
- `RunContextProvider.context() -> RunContext` · `Store.save_run(...) -> int` · `Clock.now_utc_iso() -> str`

Domain value objects (frozen dataclasses): `DriverCommand`, `SamplerCommand`, `CaptureSpec`, `ExecutionPlan`, `DriverResult`, `MarkerParseResult`, `SystemSampleParseResult`, `Marker`, `SystemSample`, `RunContext`, `Run`.

## Direction metadata mapping (RUN stores, COMPARE consumes)

`higher_is_better=1`: `fps_avg`, `fps_min`. `higher_is_better=0`: `total_time_ms`, `start_time_ms`, `ram_avg_mb`, `ram_peak_mb`, `cpu_avg_pct`, `cpu_peak_pct`, all marker durations.

## Threat model (run shells maestro/adb/git/flashlight)

argv lists only, never `shell=True`; `flow_name` validated against config-known flows before invocation; git shelled in an explicit cwd (missing binary → fields `None`, not failure); `PASSWORD` forwarded via env, never logged; all SQL values bound via `?`, no dynamic SQL identifiers.

## Delivery (3 PRs)

PR1 (foundation: schema + domain) → PR2 (adapters + store + migration runner) → PR3 (application + CLI + ManualDriver wiring). Greenfield; `.db`/`results/` gitignored & discardable; rollback = revert branch.
