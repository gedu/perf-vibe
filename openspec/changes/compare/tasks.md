# Tasks: `compare` capability (Phase 2, compare-only slice)

Grounded in spec `openspec/changes/compare/spec.md` (Rev 2), design `openspec/changes/compare/design.md` (Rev 2), decisions #53/#58/#39. Scope: `perf compare <flow>` only, exit 0/2/3 never 1. `budget-check` + full `--calibrate` sweep deferred.

**Resolved at this phase:** `Analyzer.compare_latest` returns a single additive carrier `CompareResult(verdicts: Sequence[Verdict], calibration: CalibrationReport)` — one method, no second port method (no other `Analyzer` implementer exists yet, so this is a safe, effectively-additive signature change). Calibration reuses the SAME windowed `baseline_points` rows (per design's "one query, two consumers") — no separate calibration window.

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | ~1650–1900 (prod ~880, tests ~800) across 3 PRs |
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
| 1 | Pure domain math + config defaults | PR-A (~600–650 ln) | `pytest tests/unit/test_statistics.py tests/unit/test_regression.py tests/unit/test_calibration.py tests/unit/test_config_loader.py -q` | N/A — pure math, no runtime scenario | Revert `domain/{statistics,regression,calibration}.py`, `model.py` Verdict fields, `config/loader.py` fields; no consumers yet |
| 2 | Baseline read-model + `SqlAnalyzer` + registry | PR-B (~500–550 ln) | `pytest tests/integration/test_store_baseline.py tests/integration/test_analyzer_sql.py tests/integration/test_registry.py -q` | `pytest` against a real temp SQLite file, no device | Revert `store_sqlite.py` `baseline_points`, `analyzer_sql.py`, `registry.build_analyzer`, `ports.py` `CompareResult`; compare CLI not yet wired |
| 3 | CLI command + renderer + `--json` contract + e2e wiring | PR-C (~550–650 ln) | `pytest tests/contract/test_compare_v1_contract.py tests/golden/test_compare_pretty_golden.py tests/integration/test_cli_compare.py tests/integration/test_cli_compare_replay.py -q` | `perfvibe --config examples/demo-compare/perf.toml compare demo` — ReplayDriver-seeded multi-commit history, device-free | Revert `cli/commands/compare.py`, `cli/main.py` registration, `compare_pretty.py`, `compare_v1.py`; PR-A/PR-B remain usable standalone |

## Phase 1: Domain pure math + config (PR-A)

- [ ] 1.1 RED: `tests/unit/test_statistics.py` — hypothesis: `median`, `median_by_commit` (collapses repeat-commit rows), nearest-rank percentile edges (n=1, all-equal, even/odd n; `min≤p50≤p90≤max`)
- [ ] 1.2 GREEN: `domain/statistics.py` — pure `median`, `median_by_commit`, percentile helper
- [ ] 1.3 RED: `tests/unit/test_model.py` — `Verdict` 4-state fields (`latest_value`, `baseline_value`, `unit`, `sample_n`, `baseline_commit_n`, `series`) + `RunPoint` read-model dataclass
- [ ] 1.4 GREEN: `domain/model.py` — extend `Verdict` to `improvement|stable|regression|insufficient-data` + data fields; add `RunPoint`
- [ ] 1.5 RED (highest blast radius): `tests/unit/test_regression.py` — hypothesis direction-aware invariants: FPS drop ⇒ regression, duration/RAM/CPU rise ⇒ regression, floor+threshold-both-required gating, `insufficient-data` on `baseline is None`/low `baseline_commit_n`/low `sample_n` (never silent `stable`)
- [ ] 1.6 GREEN: `domain/regression.py` — `classify(latest, baseline, *, higher_is_better, threshold_pct, floor, baseline_commit_n, sample_n, min_n) -> Verdict`
- [ ] 1.7 RED: `tests/unit/test_config_loader.py` additions — new `PerfConfig` fields `threshold_pct=5.0`, `floors={ms:5,mb:5,pct:3,fps:2}`, `min_baseline_commits=3`, `warmup_k=1`, `baseline_n=10`; `perf.toml` override layering
- [ ] 1.8 GREEN: `config/loader.py` — add the 5 fields through the existing layered `_merge`
- [ ] 1.9 RED (highest blast radius): `tests/unit/test_calibration.py` — table cases: too-loose (`floor>=max_abs`), too-strict (`threshold_pct<noise_pct`), reasonable + exact flag count, **stable-history-flags-0-is-reasonable-NOT-too-loose** (anti-lying-label), `<2` commits ⇒ `insufficient-data`; invariant test: calibration never alters `Verdict.status` or exit code
- [ ] 1.10 GREEN: `domain/calibration.py` — `grade(per_run_points, *, unit, higher_is_better, floor, threshold_pct) -> MetricCalibration`; `grade_all(...) -> CalibrationReport`; honest-degenerate labels only

## Phase 2: Baseline read-model + `SqlAnalyzer` + registry (PR-B)

- [ ] 2.1 RED (highest blast radius): `tests/integration/test_store_baseline.py` — seeded multi-commit temp SQLite proving: same-commit runs collapse to one median, dev-bundle + current-commit excluded, warm/cold + `device_key` never mix, and the median-by-commit result DIFFERS from a naive last-10-runs window
- [ ] 2.2 GREEN: `adapters/store_sqlite.py` — `baseline_points(flow_name, metric_name, device_key, mode, current_commit, limit)` returns per-RUN rows (pre-collapse); `measure`-family CTE per design sketch + `system_sample` variant selecting `iteration.idx` (drops `idx<K` upstream in the analyzer, not here)
- [ ] 2.3 RED: extend `tests/unit/test_domain_boundary.py` — `domain/regression.py`/`statistics.py`/`calibration.py` import no `adapters/` module; `Analyzer.compare_latest` returns `CompareResult`
- [ ] 2.4 GREEN: `domain/ports.py` — `Analyzer.compare_latest(flow_name, device_key) -> CompareResult` (`CompareResult(verdicts, calibration)`, additive dataclass in `model.py`)
- [ ] 2.5 RED (highest blast radius): `tests/integration/test_analyzer_sql.py` — `SqlAnalyzer` composes store reads → `statistics` → `regression.classify` → `Verdict[]`; `calibration.grade_all` fed the SAME per-run rows `baseline_points` returns (assert single query, no divergent second query); warm-up `idx<K` drop applies to `system_sample` metrics ONLY — marker/`measure` metrics untouched (test both families)
- [ ] 2.6 GREEN: `adapters/analyzer_sql.py` — `SqlAnalyzer` implementing `Analyzer`
- [ ] 2.7 RED: extend `tests/integration/test_registry.py` — `build_analyzer(store, **tuning_params)` factory
- [ ] 2.8 GREEN: `adapters/registry.py` — `build_analyzer(store, **params)` plain factory (rule of three: one implementation)

## Phase 3: CLI command + renderer + `--json` contract + e2e wiring (PR-C)

- [ ] 3.1 RED: `tests/contract/test_compare_v1_contract.py` — `schema_version=1` payload shape (verdicts + `calibration` object, per-metric + overall); fails on unversioned shape change
- [ ] 3.2 GREEN: `contracts/compare_v1.py` — `build_compare_payload(result: CompareResult) -> dict`
- [ ] 3.3 RED: `tests/golden/test_compare_pretty_golden.py` — sparklines (`▁▂▃▅▇`) per metric + one calibration label line (`✓ reasonable — X of N` / `⚠ too loose` / `⚠ too strict`), color forced off
- [ ] 3.4 GREEN: `cli/output/compare_pretty.py` — `render_compare(compare_result, color) -> str`
- [ ] 3.5 RED (highest blast radius): `tests/integration/test_cli_compare.py` — exit-code discipline: `0` when a verdict is shown (incl. `regression`), `2` on unknown flow / no history, `3` on runtime error, NEVER `1`; non-TTY stderr nudge without `--json`
- [ ] 3.6 GREEN: `cli/commands/compare.py` — typer command wiring `build_store`/`build_analyzer`/config tuning params into `SqlAnalyzer`, dispatch pretty/`--json`, exit `0/2/3`
- [ ] 3.7 GREEN: `cli/main.py` — register `compare` like `run`
- [ ] 3.8 RED (highest blast radius, real wiring): `tests/integration/test_cli_compare_replay.py` — seed multi-run/multi-commit history via `ReplayDriver` + real `RunFlowUseCase`/`SqliteStore` (vary `git_commit` per seeded run through a fake `RunContextProvider`, NOT the analyzer), then invoke REAL `perf compare demo`; assert verdict + sanity label present in BOTH pretty and `--json`; do NOT monkeypatch `SqlAnalyzer`/`build_analyzer`
- [ ] 3.9 GREEN: `examples/demo-compare/` — new example reusing `examples/demo-run/` fixtures + `ReplayDriver`; add a seed script that persists N runs across M synthetic commits into a fresh `perf.db`, then `perf compare demo` is runnable device-free; extend README with the seed + compare commands

## Phase 4: Verification

- [ ] 4.1 Run full `pytest` (197+ existing + new compare tests); confirm `domain/` boundary test still passes for `regression.py`/`statistics.py`/`calibration.py`
- [ ] 4.2 Update `openspec/specs/perf-run.md` "COMPARE PLANNED" row → implemented (compare-only; `budget-check`/`--calibrate` still deferred)
