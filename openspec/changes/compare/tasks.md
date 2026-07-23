# Tasks: `compare` capability (Phase 2, compare-only slice)

Grounded in spec `openspec/changes/compare/spec.md` (Rev 3), design `openspec/changes/compare/design.md` (Rev 3), decisions #53/#58/#39. Scope: `perf compare <flow>` only, exit 0/2/3 never 1. `budget-check` + full `--calibrate` sweep deferred.

**Rev 3 additions** (three cross-cutting quality bars): PERFORMANCE (bounded/indexed baseline, one additive index migration, O(1) query-count budget, scale test), UX (pretty-output golden coverage), CORNER CASES (C1–C9 matrix, one RED test each). New tasks are tagged `[Rev 3]`; they slot into the existing PR split. NOTE: Rev 3 introduces ONE migration — `db/migrations/0002_compare_baseline_index.sql` (additive index only, no table/data change) — so this slice is no longer migration-free.

**Resolved at this phase:** `Analyzer.compare_latest` returns a single additive carrier `CompareResult(verdicts: Sequence[Verdict], calibration: CalibrationReport)` — one method, no second port method (no other `Analyzer` implementer exists yet, so this is a safe, effectively-additive signature change). Calibration reuses the SAME windowed `baseline_points` rows (per design's "one query, two consumers") — no separate calibration window. Rev 3 refines `baseline_points` to be BATCHED PER METRIC-FAMILY (one query for all `measure` metrics, one for all `system_sample` metrics; returns a `metric_name` column; `LIMIT`ed to `baseline_n` commits) so the SQL-statement count is O(1) in history size AND metric count.

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | ~1950–2250 (prod ~930, tests ~1050) across 3 PRs — Rev 3 adds ~300–350 (perf scale test, corner-case matrix tests, extra goldens, index migration) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR-A → PR-B → PR-C |
| Delivery strategy | ask-on-risk (default; not specified upstream) |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| 1 | Pure domain math + config defaults + domain corner cases `[Rev 3]` | PR-A (~640–700 ln) | `pytest tests/unit/test_statistics.py tests/unit/test_regression.py tests/unit/test_calibration.py tests/unit/test_config_loader.py -q` | N/A — pure math, no runtime scenario | Revert `domain/{statistics,regression,calibration}.py`, `model.py` Verdict fields, `config/loader.py` fields; no consumers yet |
| 2 | Additive index migration + batched baseline read-model + `SqlAnalyzer` + registry `[Rev 3]` | PR-B (~560–620 ln) | `pytest tests/integration/test_store_baseline.py tests/integration/test_analyzer_sql.py tests/integration/test_registry.py tests/integration/test_migrations.py -q` | `pytest` against a real temp SQLite file, no device | Revert `db/migrations/0002_*.sql` + `schema.sql` index line, `store_sqlite.py` `baseline_points`, `analyzer_sql.py`, `registry.build_analyzer`, `ports.py` `CompareResult`; compare CLI not yet wired. Index revert is non-destructive (drop only) |
| 3 | CLI command + renderer + `--json` contract + e2e wiring + perf scale test + UX goldens + CLI corner cases `[Rev 3]` | PR-C (~750–850 ln) | `pytest tests/contract/test_compare_v1_contract.py tests/golden/test_compare_pretty_golden.py tests/integration/test_cli_compare.py tests/integration/test_cli_compare_replay.py tests/integration/test_compare_performance.py -q` | `perfvibe --config examples/demo-compare/perf.toml compare demo` — ReplayDriver-seeded multi-commit history, device-free | Revert `cli/commands/compare.py`, `cli/main.py` registration, `compare_pretty.py`, `compare_v1.py`, `test_compare_performance.py`; PR-A/PR-B remain usable standalone |

> `[Rev 3]` note on PR-C size: the perf scale test + extra golden cases push PR-C toward ~850 ln (mostly test/golden fixtures, which are lower review-risk). If review budget is tight, the perf scale test (3.10–3.11) is a clean sub-slice that can land as its own follow-up PR-C2 without blocking the CLI.

## Phase 1: Domain pure math + config (PR-A)

- [x] 1.1 RED: `tests/unit/test_statistics.py` — hypothesis: `median`, `median_by_commit` (collapses repeat-commit rows), nearest-rank percentile edges (n=1, all-equal, even/odd n; `min≤p50≤p90≤max`)
- [x] 1.2 GREEN: `domain/statistics.py` — pure `median`, `median_by_commit`, percentile helper
- [x] 1.3 RED: `tests/unit/test_model.py` — `Verdict` 4-state fields (`latest_value`, `baseline_value`, `unit`, `sample_n`, `baseline_commit_n`, `series`) + `RunPoint` read-model dataclass
- [x] 1.4 GREEN: `domain/model.py` — extend `Verdict` to `improvement|stable|regression|insufficient-data` + data fields; add `RunPoint`
- [x] 1.5 RED (highest blast radius): `tests/unit/test_regression.py` — hypothesis direction-aware invariants: FPS drop ⇒ regression, duration/RAM/CPU rise ⇒ regression, floor+threshold-both-required gating, `insufficient-data` on `baseline is None`/low `baseline_commit_n`/low `sample_n` (never silent `stable`)
- [x] 1.6 GREEN: `domain/regression.py` — `classify(latest, baseline, *, higher_is_better, threshold_pct, floor, baseline_commit_n, sample_n, min_n) -> Verdict`
- [x] 1.7 RED: `tests/unit/test_config_loader.py` additions — new `PerfConfig` fields `threshold_pct=5.0`, `floors={ms:5,mb:5,pct:3,fps:2}`, `min_baseline_commits=3`, `warmup_k=1`, `baseline_n=10`; `perf.toml` override layering
- [x] 1.8 GREEN: `config/loader.py` — add the 5 fields through the existing layered `_merge`
- [x] 1.9 RED (highest blast radius): `tests/unit/test_calibration.py` — table cases: too-loose (`floor>=max_abs`), too-strict (`threshold_pct<noise_pct`), reasonable + exact flag count, **stable-history-flags-0-is-reasonable-NOT-too-loose** (anti-lying-label), `<2` commits ⇒ `insufficient-data`; invariant test: calibration never alters `Verdict.status` or exit code
- [x] 1.10 GREEN: `domain/calibration.py` — `grade(per_run_points, *, unit, higher_is_better, floor, threshold_pct) -> MetricCalibration`; `grade_all(...) -> CalibrationReport`; honest-degenerate labels only
- [x] 1.11 RED `[Rev 3 corner cases]`: `tests/unit/test_regression.py` (extend) — pure-domain corner cases: **C1/C5** `baseline is None` (first-ever run / new metric) ⇒ `insufficient-data` never `1`; **C3** single baseline commit (`baseline_commit_n=1 < min_n`) ⇒ `insufficient-data`; **C4** all-equal / zero-variance baseline INCLUDING `baseline==0` ⇒ `stable` with NO `ZeroDivisionError`; assert no path raises for these inputs
- [x] 1.12 GREEN: fold C1/C3/C4/C5 handling into `domain/regression.py` `classify` (baseline-None + min-n guards already planned in 1.6; add explicit `baseline==0` divide guard so zero-variance is `stable`, not a crash)

## Phase 2: Baseline read-model + `SqlAnalyzer` + registry (PR-B)

- [ ] 2.0 GREEN `[Rev 3 migration]`: `db/migrations/0002_compare_baseline_index.sql` — `CREATE INDEX IF NOT EXISTS idx_run_baseline ON run(flow_id, device_id, mode, started_at)` (additive, no table/data change); mirror the same index into `db/schema.sql` INDEX section so fresh + migrated DBs converge. Covered by the existing `user_version` migration-runner test path; add a focused test asserting `PRAGMA user_version` advances and `idx_run_baseline` exists after `SqliteStore` init on both a fresh DB and a pre-Rev3 DB
- [ ] 2.1 RED (highest blast radius): `tests/integration/test_store_baseline.py` — seeded multi-commit temp SQLite proving: same-commit runs collapse to one median, dev-bundle + current-commit excluded, warm/cold + `device_key` never mix, and the median-by-commit result DIFFERS from a naive last-10-runs window; **[Rev 3]** one query returns ALL family metrics (a `metric_name` column present) and the result set is `LIMIT`ed to `baseline_n` commits
- [ ] 2.2 GREEN: `adapters/store_sqlite.py` — `baseline_points(flow_name, device_key, mode, current_commit, limit)` returns per-RUN rows (pre-collapse), **batched across the whole metric-family** (returns `metric_name`; `LIMIT`ed to `baseline_n` commits per the Rev 3 query shape); `measure`-family CTE + `system_sample` variant selecting `iteration.idx` (drops `idx<K` upstream in the analyzer, not here)
- [ ] 2.2a RED `[Rev 3 corner cases]`: `tests/integration/test_store_baseline.py` (extend) — **C7** unseen device/mode ⇒ `[]`; **C8** warm-only history queried for cold (and vice-versa) ⇒ `[]`; **C9** dev-bundle-only history ⇒ `[]`; assert empty result set (no crash), which the analyzer maps to `insufficient-data`
- [ ] 2.5a RED `[Rev 3 corner cases]`: `tests/integration/test_analyzer_sql.py` (extend) — **C5** metric in latest but absent from baseline ⇒ that metric `insufficient-data`; **C6** metric in baseline but absent from latest ⇒ skipped/noted, no crash; **C7/C8/C9** empty baseline rows ⇒ `insufficient-data` (never `stable`, never raise)
- [ ] 2.3 RED: extend `tests/unit/test_domain_boundary.py` — `domain/regression.py`/`statistics.py`/`calibration.py` import no `adapters/` module; `Analyzer.compare_latest` returns `CompareResult`
- [ ] 2.4 GREEN: `domain/ports.py` — `Analyzer.compare_latest(flow_name, device_key) -> CompareResult` (`CompareResult(verdicts, calibration)`, additive dataclass in `model.py`)
- [ ] 2.5 RED (highest blast radius): `tests/integration/test_analyzer_sql.py` — `SqlAnalyzer` composes store reads → `statistics` → `regression.classify` → `Verdict[]`; `calibration.grade_all` fed the SAME per-run rows `baseline_points` returns (assert single query, no divergent second query); warm-up `idx<K` drop applies to `system_sample` metrics ONLY — marker/`measure` metrics untouched (test both families)
- [ ] 2.6 GREEN: `adapters/analyzer_sql.py` — `SqlAnalyzer` implementing `Analyzer`
- [ ] 2.7 RED: extend `tests/integration/test_registry.py` — `build_analyzer(store, **tuning_params)` factory
- [ ] 2.8 GREEN: `adapters/registry.py` — `build_analyzer(store, **params)` plain factory (rule of three: one implementation)

## Phase 3: CLI command + renderer + `--json` contract + e2e wiring (PR-C)

- [ ] 3.1 RED: `tests/contract/test_compare_v1_contract.py` — `schema_version=1` payload shape (verdicts + `calibration` object, per-metric + overall); fails on unversioned shape change
- [ ] 3.2 GREEN: `contracts/compare_v1.py` — `build_compare_payload(result: CompareResult) -> dict`
- [ ] 3.3 RED `[Rev 3 UX]`: `tests/golden/test_compare_pretty_golden.py` — color FORCED OFF golden fixtures for FIVE cases: (a) normal multi-metric verdict, (b) a `regression` (assert plain-text emphasis marker present, e.g. `!`/`REGRESSION`, since color is off), (c) `insufficient-data`, (d) single-data-point sparkline, (e) `max==min` sparkline edge; each line shows metric / latest-vs-baseline / arrow + signed % / classification; assert the sanity-label footer (`✓ reasonable — X of N` / `⚠ too loose` / `⚠ too strict`) appears; also assert NO ANSI escape bytes under `--no-color`/`NO_COLOR`/non-TTY
- [ ] 3.3a RED `[Rev 3 UX]`: `tests/golden/test_compare_pretty_golden.py` (extend) + `tests/contract/test_compare_v1_contract.py` (extend) — the sanity label appears in BOTH pretty AND `--json`, and asserting it is present NEVER changes the exit code (still `0`); `--json` payload byte-identical regardless of color/TTY state
- [ ] 3.4 GREEN: `cli/output/compare_pretty.py` — `render_compare(compare_result, *, color) -> str`; per-metric line (name, latest vs baseline, arrow `↑/↓/→` + signed %, classification, sparkline), `regression` emphasized (bold/red with color; plain-text marker without), sanity label as a single footer line; honor `--no-color`/`NO_COLOR`/non-TTY via the existing `OutputContext`
- [ ] 3.5 RED (highest blast radius): `tests/integration/test_cli_compare.py` — exit-code discipline: `0` when a verdict is shown (incl. `regression`), `2` on unknown flow / no history, `3` on runtime error, NEVER `1`; non-TTY stderr nudge without `--json`
- [ ] 3.5a RED `[Rev 3 corner cases]`: `tests/integration/test_cli_compare.py` (extend) — **C1** first-ever run of a KNOWN flow ⇒ all metrics `insufficient-data`, exit `0` (never `1`); **C2** unknown flow ⇒ exit `2`; assert NO corner case (C1–C9, exercised end-to-end) crashes or exits `1`
- [ ] 3.6 GREEN: `cli/commands/compare.py` — typer command wiring `build_store`/`build_analyzer`/config tuning params into `SqlAnalyzer`, dispatch pretty/`--json`, exit `0/2/3`
- [ ] 3.7 GREEN: `cli/main.py` — register `compare` like `run`
- [ ] 3.8 RED (highest blast radius, real wiring): `tests/integration/test_cli_compare_replay.py` — seed multi-run/multi-commit history via `ReplayDriver` + real `RunFlowUseCase`/`SqliteStore` (vary `git_commit` per seeded run through a fake `RunContextProvider`, NOT the analyzer), then invoke REAL `perf compare demo`; assert verdict + sanity label present in BOTH pretty and `--json`; do NOT monkeypatch `SqlAnalyzer`/`build_analyzer`
- [ ] 3.9 GREEN: `examples/demo-compare/` — new example reusing `examples/demo-run/` fixtures + `ReplayDriver`; add a seed script that persists N runs across M synthetic commits into a fresh `perf.db`, then `perf compare demo` is runnable device-free; extend README with the seed + compare commands
- [ ] 3.10 RED `[Rev 3 performance / highest blast radius]`: `tests/integration/test_compare_performance.py` — seed a LARGE history (≈800–1000 runs across 50+ distinct commits, multiple metrics, both warm and cold) into a temp SQLite; wrap the connection in a counting proxy that tallies `execute`/`executemany`; invoke the REAL `SqlAnalyzer.compare_latest` (or `perf compare`) and assert: (a) the verdict is CORRECT (matches a small hand-computed baseline), (b) wall-clock `< COMPARE_PERF_BUDGET_MS` (named module constant, e.g. `150`, tunable), (c) executed SQL-statement count `<= COMPARE_MAX_SQL_STATEMENTS` (named constant, small — guards against O(history) scan and per-commit/per-metric N+1). Make both budgets named constants at module top so they are tunable for the dev machine
- [ ] 3.11 `[Rev 3 performance]` VERIFY: capture `EXPLAIN QUERY PLAN` for the baseline query in the perf test (or a sibling assertion) and assert it uses `idx_run_baseline` / does not full-scan `run`

## Phase 4: Verification

- [ ] 4.1 Run full `pytest` (197+ existing + new compare tests incl. Rev 3 perf + corner-case + extra golden cases); confirm `domain/` boundary test still passes for `regression.py`/`statistics.py`/`calibration.py`
- [ ] 4.2 `[Rev 3]` Confirm the additive index migration applies cleanly on a pre-Rev3 DB (`user_version` advances, `idx_run_baseline` present) and that `db/schema.sql` fresh-init produces the same index — fresh and migrated DBs converge
- [ ] 4.3 Update `openspec/specs/perf-run.md` "COMPARE PLANNED" row → implemented (compare-only; `budget-check`/`--calibrate` still deferred)
