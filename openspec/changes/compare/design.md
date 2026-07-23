# Design: `compare` capability (Phase 2, compare-only slice)

> **REVISION 2** — SUPERSEDES Rev 1 where they conflict. Adds (a) pinned tuning
> defaults and (b) an always-on **config sanity label**, per authoritative
> decision `perf-cli/decisions/compare-calibration` (#58) and the revised spec
> `perf-cli/spec/compare` (#56). Everything in Rev 1 stands unchanged unless a
> row below marks it revised. The full `--calibrate` sweep stays DEFERRED.

## Technical Approach

`perf compare <flow>` reads `run`'s store (writes NOTHING new), computes a per-metric, direction-aware verdict, and renders it. Pure verdict math lives in `domain/regression.py` + `domain/statistics.py`; all SQL lives in the extended `SqliteStore`; a thin `SqlAnalyzer` (the one `Analyzer` adapter) composes store reads → pure math → `Verdict[]`; `cli/commands/compare.py` composes analyzer + reporter, exit `0/2/3` never `1`. Mirrors `run`'s wiring (registry factories, `OutputContext`, versioned `--json`). Fulfils spec `openspec/changes/compare/spec.md`, grounded in #53/#39.

**Rev 2 additions.** (1) Tuning knobs become real, layered `PerfConfig` fields with conservative defaults (decision #58): `threshold_pct=5.0`, per-unit `floors={ms:5, mb:5, pct:3, fps:2}`, `min_baseline_commits=3`, `warmup_k=1`. (2) A pure `domain/calibration.py` grades the ACTIVE config against the flow's stored delta distribution and returns a `CalibrationReport` (`reasonable | too-loose | too-strict` + flag count). The `SqlAnalyzer` feeds it the SAME per-run rows the baseline query already returns (no second query). The label surfaces in BOTH renderers and the `--json` payload; it is purely informational — it NEVER changes a per-metric verdict or the exit code (`0/2/3`, never `1`).

## Architecture Decisions

| Decision | Choice | Rejected | Rationale |
|---|---|---|---|
| Median location | Pull per-commit rows via SQL, compute **two-level median in `statistics.py`** | Pure-SQL median | SQLite has no `MEDIAN`; median-of-per-commit-medians nests window-rank ugly SQL; Python is pure + hypothesis-testable (skill: pure core). |
| Two metric families | `measure`/marker metrics use `run_metric_summary` (no warm-up); `system_sample` metrics compute percentiles over the run's iterations, warm-up `idx < K` dropped | One code path | Markers hang off the run (no ordinal); only `system_sample` carries `idx`. Warm-up asymmetry is an explicit branch (#53.3), never silent. |
| Verdict carrier | Extend `Verdict` to 4-state + pure data fields (`latest_value`, `baseline_value`, `unit`, `sample_n`, `baseline_commit_n`, `series`) | New `MetricComparison` type | Keeps `Analyzer.compare_latest → Sequence[Verdict]` intact; one lossless carrier feeds BOTH renderers. `run` never builds it, so additive fields are safe. |
| Direction | `delta_pct` raw-signed; classify via `metric.higher_is_better` | "bigger = worse" | FPS-drop inversion bug (#39). |
| Absolute floor | `floors` mapping keyed by `metric.unit` (ms/MB/%/fps) | one scalar floor | A single floor across ms/MB/fps is semantically wrong; per-metric override still deferred. |
| Analyzer factory | `build_analyzer(store, params)` — single impl, plain factory like `build_store` | name-keyed map | Rule of three: one implementation. |
| Tuning defaults (Rev 2) | Pin `threshold_pct=5.0`, `floors={ms:5,mb:5,pct:3,fps:2}`, `min_baseline_commits=3`, `warmup_k=1` as `PerfConfig` fields (layered like existing config) | Hardcode in domain; a single scalar floor | Conservative/low-noise defaults are the user's own success criterion (#58 — a noisy tool gets abandoned); all overridable in `perf.toml`. Resolves Rev 1's open question. |
| Sanity-label location (Rev 2) | Pure `domain/calibration.py` (label logic + `CalibrationReport` carrier); analyzer supplies the delta distribution | Compute inside the analyzer/store; compute in the renderer | Keeps the honest-degenerate math pure + hypothesis-testable behind the hexagonal boundary (spec req 9); renderers stay dumb. |
| Degenerate detection (Rev 2) | `too-loose` IFF `floor >= max observed |Δ|` (config CAN NEVER flag); `too-strict` IFF `threshold_pct < typical run-to-run noise` (median within-commit `|Δ%|`) | Naive "0 of N flagged ⇒ too loose" | A stable history legitimately flags 0 — labelling that "too loose" would LIE (#58). Only genuinely un-actionable configs warn; otherwise just report the count. |
| One query, two consumers (Rev 2) | Baseline read-model returns per-RUN rows (pre-collapse); analyzer derives BOTH the median-by-commit baseline AND the calibration delta distribution + within-commit noise from those same rows | A separate calibration query | Avoids a divergent second query (task directive 4); within-commit spread needs the per-run rows the baseline CTE already selects. |
| Sweep mode (Rev 2) | `--calibrate` (multi-value threshold/floor sweep) DEFERRED — future seam only | Design it now | Out of this slice (#58.3); `CalibrationReport` is shaped so a sweep can later call `calibration.grade()` N times. |

## Data Flow

    compare.py ─→ build_store, build_analyzer(store, cfg params)
        └─→ SqlAnalyzer.compare_latest(flow, device)
              store.latest_run / measure_summary / system_sample_points   (SQL, ?-bound)
              store.baseline_points(...)  → per-RUN rows (pre-collapse)    (SQL, ?-bound)
                → statistics.median_by_commit()   (pure) ─→ baseline
                → regression.classify()           (pure) ─→ Verdict[]
                → calibration.grade(per_run_rows, cfg) (pure, SAME rows) ─→ CalibrationReport
        └─→ --json: contracts.compare_v1.build_compare_payload (verdicts + calibration)
            pretty: compare_pretty.render_compare (sparklines + label line)
        └─→ exit 0 (2 usage / 3 runtime; NEVER 1) — calibration NEVER affects exit

## Interfaces / Contracts

`regression.classify(latest, baseline, *, higher_is_better, threshold_pct, floor, baseline_commit_n, sample_n, min_n) -> Verdict`: `insufficient-data` when `baseline is None or baseline_commit_n < min_n or sample_n < min_n`; else `abs_delta = latest-baseline`, `rel_pct = abs_delta/baseline*100` (guard `baseline==0`); `worse = abs_delta>0` for lower-better, `abs_delta<0` for higher-better; `regression` when worse AND `abs(abs_delta) >= floor` AND `abs(rel_pct) >= threshold_pct`; `improvement` symmetric on the good side; else `stable`. Below floor OR below threshold ⇒ `stable`. Pure, hypothesis-testable.

Baseline SQL sketch (measure family; static identifiers, every value `?`-bound):

```sql
WITH eligible AS (
  SELECT r.run_id, r.git_commit, r.started_at FROM run r
  JOIN flow f ON f.flow_id=r.flow_id  JOIN device d ON d.device_id=r.device_id
  WHERE f.name=? AND d.device_key=? AND r.mode=?
    AND COALESCE(r.is_dev_bundle,0)=0 AND r.git_commit IS NOT NULL AND r.git_commit<>?),
per_run AS (
  SELECT e.git_commit, e.started_at, s.p90_ms FROM eligible e
  JOIN run_metric_summary s ON s.run_id=e.run_id JOIN metric m ON m.metric_id=s.metric_id
  WHERE m.name=?),
recent AS (SELECT git_commit FROM per_run GROUP BY git_commit ORDER BY MAX(started_at) DESC LIMIT ?)
SELECT p.git_commit, p.p90_ms FROM per_run p JOIN recent r ON r.git_commit=p.git_commit;
```

Python then: group rows by commit → `median` per commit (collapses repeat same-commit runs) → `median` across commits = baseline. The `system_sample` variant selects `iteration.idx, system_sample.<col>` and drops `idx < K` before per-run percentile. `--json` = `contracts/compare_v1.py` `schema_version=1`, pure `build_compare_payload` (like `build_run_payload`).

**Calibration contract (Rev 2, `domain/calibration.py` — pure).**
`grade(per_run_points, *, unit, higher_is_better, floor, threshold_pct) -> MetricCalibration` where `per_run_points: Sequence[(git_commit, value, started_at)]` are the exact rows `baseline_points` returns. Steps: (1) `commit_medians` = median-by-commit ordered by time (reuses `statistics`); (2) **walk-forward delta distribution** — for each commit `i≥1`: `base=median(commit_medians[:i])`, `Δabs=median_i-base`, `Δpct=Δabs/base*100` (guard `base==0`), yielding N-1 signed deltas ("each run vs its own median-by-commit baseline"); (3) **within-commit noise** — for commits with repeat runs: `|value-commit_median|` per run → `noise_pct = median(|Δ%|)` (fallback: adjacent-commit `|Δ%|` when no repeats); (4) `max_abs = max(|Δabs|)`; (5) `flagged = count(direction-aware regression under (floor, threshold_pct))` reusing `regression.classify`'s worse-AND-floor-AND-threshold rule. Label: `too-loose` IFF `floor >= max_abs` (config can never fire); else `too-strict` IFF `threshold_pct < noise_pct` (noise alone flags); else `reasonable`. Returns `MetricCalibration(metric_name, status, flagged_count, total_count, max_abs, noise_pct)`.
`grade_all(...) -> CalibrationReport` aggregates: `runs_flagged` = historical runs where ANY metric would flag / `runs_total`, and `status` = worst per-metric status. Both dataclasses frozen, no I/O. Only `insufficient-data` guard: `<2` commits ⇒ `status="insufficient-data"`, no warn.

## File Changes

| File | Action | Description |
|---|---|---|
| `domain/regression.py` | Create | Pure direction-aware `classify` → `Verdict`. |
| `domain/statistics.py` | Create | Pure `median`, `median_by_commit`, nearest-rank percentile helper. |
| `domain/calibration.py` | Create (Rev 2) | Pure `grade`/`grade_all` → `MetricCalibration`/`CalibrationReport`; honest-degenerate label logic. |
| `domain/model.py` | Modify | `Verdict` → 4-state + pure data fields; add `RunPoint` read-model. |
| `adapters/analyzer_sql.py` | Create | `SqlAnalyzer` implements `Analyzer`; composes store reads + pure math (verdicts + `calibration.grade_all` from the SAME per-run rows). |
| `adapters/store_sqlite.py` | Modify | `history()` + baseline/latest/system-sample read-models (`?`-bound). `baseline_points` returns per-RUN rows (pre-collapse) so it feeds both baseline and calibration. |
| `adapters/registry.py` | Modify | `build_analyzer(store, params)` threads the tuning params. |
| `adapters/ports.py`→`domain/ports.py` | Modify (Rev 2) | `Analyzer.compare_latest` returns verdicts + report; add `CompareResult` carrier or a second method — keep the port change additive. |
| `cli/commands/compare.py` | Create | typer command; exit 0/2/3. |
| `cli/main.py` | Modify | Register `compare` like `run`. |
| `cli/output/compare_pretty.py` | Create | Per-metric verdict + sparkline (`▁▂▃▅▇`, ~10 lines) + one calibration label line (`✓ reasonable — X of N runs would flag` / `⚠ too loose` / `⚠ too strict`). |
| `contracts/compare_v1.py` | Create | Versioned `--json` payload: verdicts + `calibration` object (per-metric + overall). |
| `config/loader.py` | Modify (Rev 2) | Add `threshold_pct=5.0`, `floors={ms:5,mb:5,pct:3,fps:2}`, `min_baseline_commits=3`, `warmup_k=1`, `baseline_n=10` as `PerfConfig` fields, read through the existing layered `_merge`. |

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit | `classify` direction invariants (FPS vs duration), floor, threshold boundary, insufficient-data | `hypothesis` properties |
| Unit | `median_by_commit`, percentile edges (n=1, all-equal, even/odd) | `hypothesis` |
| Unit | `calibration.grade`: too-loose (`floor>=max Δ`), too-strict (`threshold<noise`), reasonable + exact flag count; **stable-history-flags-0-is-reasonable-NOT-too-loose** (anti-lying-label); `<2` commits ⇒ insufficient-data | `hypothesis` properties + table cases |
| Unit | calibration NEVER alters `Verdict` status or exit code (invariant test) | property |
| Unit | `PerfConfig` Rev 2 defaults + `perf.toml` override layering (`floors`, `threshold_pct`) | injected `project_dir`/`env` |
| Integration | `SqlAnalyzer` + baseline query on temp SQLite seeded multi-commit | prove median-by-commit collapse, dev-bundle/current-commit exclusion, warm/cold+device split, warm-up K on `system_sample` only, and that calibration reuses the SAME per-run rows (single query) |
| Golden | pretty + sparkline output | color off, `--update-golden` |
| Contract | `--json` verdict shape | fail on unversioned change |
| E2E-ish | REAL wiring `compare` (no monkeypatch of analyzer) | fake only device/clock; reuse `ReplayDriver`/seed to build history |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process integration. Compare only reads a local SQLite file with `?`-bound values and static SQL identifiers (perf-cli-standards rule 4).

## Migration / Rollout

No migration. Purely additive (new command/adapter/domain modules/config fields/read-models); `run` write path and schema untouched. Rollback = revert branch.

## Open Questions

- [x] ~~Default `threshold_pct` and per-unit floor defaults~~ — RESOLVED (Rev 2, decision #58): `threshold_pct=5.0`, `floors={ms:5, mb:5, pct:3, fps:2}`, `min_baseline_commits=3`, `warmup_k=1`.
- [ ] Calibration window == baseline window (`baseline_n` commits). A full-history distribution is richer but is exactly the DEFERRED `--calibrate` sweep; confirm the windowed distribution is acceptable for the always-on label at tasks/apply.
- [ ] Port shape for returning verdicts + `CalibrationReport` together (`CompareResult` carrier vs. a second analyzer method) — decide at tasks; keep additive so `run` never touches it.
