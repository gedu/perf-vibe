# Design: `compare` capability (Phase 2, compare-only slice)

> **REVISION 3** — SUPERSEDES Rev 2 where they conflict. Adds three cross-cutting
> quality bars: (a) a **bounded-performance NFR** (indexed baseline access, one
> additive index migration, O(1) query-count budget), (b) **pretty-output UX**
> criteria, and (c) an explicit **corner-case** handling approach. Everything in
> Rev 2 stands unchanged unless a Rev 3 section marks it revised. The one material
> change vs Rev 2 is that this slice now DOES ship a migration — a single
> **additive index** (no table/data change). The full `--calibrate` sweep stays
> DEFERRED.
>
> **REVISION 2** — SUPERSEDES Rev 1 where they conflict. Adds (a) pinned tuning
> defaults and (b) an always-on **config sanity label**, per authoritative
> decision `perf-cli/decisions/compare-calibration` (#58) and the revised spec
> `perf-cli/spec/compare` (#56). Everything in Rev 1 stands unchanged unless a
> row below marks it revised. The full `--calibrate` sweep stays DEFERRED.

## Technical Approach

`perf compare <flow>` reads `run`'s store (writes NOTHING new), computes a per-metric, direction-aware verdict, and renders it. Pure verdict math lives in `domain/regression.py` + `domain/statistics.py`; all SQL lives in the extended `SqliteStore`; a thin `SqlAnalyzer` (the one `Analyzer` adapter) composes store reads → pure math → `Verdict[]`; `cli/commands/compare.py` composes analyzer + reporter, exit `0/2/3` never `1`. Mirrors `run`'s wiring (registry factories, `OutputContext`, versioned `--json`). Fulfils spec `openspec/changes/compare/spec.md`, grounded in #53/#39.

**Rev 2 additions.** (1) Tuning knobs become real, layered `PerfConfig` fields with conservative defaults (decision #58): `threshold_pct=5.0`, per-unit `floors={ms:5, mb:5, pct:3, fps:2}`, `min_baseline_commits=3`, `warmup_k=1`. (2) A pure `domain/calibration.py` grades the ACTIVE config against the flow's stored delta distribution and returns a `CalibrationReport` (`reasonable | too-loose | too-strict` + flag count). The `SqlAnalyzer` feeds it the SAME per-run rows the baseline query already returns (no second query). The label surfaces in BOTH renderers and the `--json` payload; it is purely informational — it NEVER changes a per-metric verdict or the exit code (`0/2/3`, never `1`).

**Rev 3 additions (performance, UX, corner cases).** (1) PERFORMANCE — the baseline read is refined to be *batched per metric-family* (one query for the whole `measure` family, one for the whole `system_sample` family) rather than one query per metric, so the SQL-statement count is O(1) in both history size and metric count. Access is made indexed against the exact filter+order pattern via one **additive index** (`idx_run_baseline`). Aggregation stays in SQL (`run_metric_summary`) and only the small windowed rows reach Python. The sanity label consumes the same windowed rows (already the Rev 2 "one query, two consumers" rule). (2) UX — `compare_pretty.render_compare` gets explicit acceptance criteria (per-metric line shape, regression emphasis that degrades gracefully without color, sanity label as a footer, `--no-color`/`NO_COLOR`/non-TTY honored). (3) CORNER CASES — the C1–C9 matrix from the spec is handled in the pure domain (`insufficient-data`/`stable`/skip) plus the analyzer/CLI (empty baseline, unknown flow), each with a RED test; none crashes or exits `1`.

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

## Bounded Performance (Rev 3)

**Index audit against the baseline access pattern.** The baseline query filters `run` by `flow_id` + `device_id` + `mode`, with residual predicates `COALESCE(is_dev_bundle,0)=0`, `git_commit IS NOT NULL`, `git_commit<>?`, ordered by recency (`started_at`) to pick the most recent `baseline_n` commits. Existing indexes: `idx_run_flow_device_time(flow_id, device_id, started_at)`, `idx_measure_metric(metric_id)`, `idx_measure_run(run_id)`. The existing run index seeks to `(flow_id, device_id)` and scans by `started_at`, but **`mode` is NOT in any index** — so SQLite scans the ENTIRE flow+device partition (warm AND cold) and filters `mode` as a residual predicate. For a flow+device with a long history that is wider than the window we actually need. The pattern's index is therefore MISSING for `mode`.

**Additive migration (new index only — no table/data change):**

```sql
-- db/migrations/0002_compare_baseline_index.sql (numeric prefix > current user_version)
CREATE INDEX IF NOT EXISTS idx_run_baseline ON run(flow_id, device_id, mode, started_at);
```

This lets SQLite seek directly to the `(flow_id, device_id, mode)` partition and read it in `started_at` order (dev-bundle / current-commit remain cheap residual filters on that narrowed set). It is picked up by the existing `user_version`-driven migration runner (`SqliteStore._migrate`); it touches no table, column, or row, so `run`'s write path and the canonical `schema.sql` fresh-DB shape stay behaviorally identical (add the same `CREATE INDEX` to `schema.sql` so fresh and migrated DBs converge). Verify with `EXPLAIN QUERY PLAN` that the baseline query uses `idx_run_baseline` and touches only the `(flow, device, mode)` partition, not a full `run` scan.

**Bounded baseline query shape (LIMIT to N commits).** Refine the Rev 1/2 sketch to (a) select the recent commit set with `LIMIT ?` (= `baseline_n`) and (b) drop the per-metric `WHERE m.name=?` filter, returning a `metric_name` column instead so ONE query serves ALL `measure`-family metrics:

```sql
WITH eligible AS (
  SELECT r.run_id, r.git_commit, r.started_at FROM run r
  JOIN flow f ON f.flow_id=r.flow_id  JOIN device d ON d.device_id=r.device_id
  WHERE f.name=? AND d.device_key=? AND r.mode=?
    AND COALESCE(r.is_dev_bundle,0)=0 AND r.git_commit IS NOT NULL AND r.git_commit<>?),
recent AS (                                   -- most-recent N commits only
  SELECT git_commit FROM eligible GROUP BY git_commit
  ORDER BY MAX(started_at) DESC LIMIT ?),      -- LIMIT = baseline_n (bounds the window)
per_run AS (
  SELECT e.git_commit, e.started_at, m.name AS metric_name, s.p90_ms
  FROM eligible e JOIN recent rc ON rc.git_commit=e.git_commit
  JOIN run_metric_summary s ON s.run_id=e.run_id JOIN metric m ON m.metric_id=s.metric_id)
SELECT git_commit, metric_name, p90_ms, started_at FROM per_run;
```

The `system_sample` family uses one analogous query selecting `iteration.idx` + the sample columns (analyzer drops `idx<K`). Python then groups by `metric_name`, then by `commit` → median-per-commit → median across commits = baseline; the SAME per-run rows feed `calibration.grade_all`.

**Query-count budget.** Per `perf compare` invocation the SQL-statement count is a small CONSTANT, independent of commits/runs/metrics: `1` latest-run lookup + `1` `measure`-family baseline query + `1` `system_sample`-family baseline query (≈3–5 statements incl. connection pragmas). It is **O(1) in history size and O(1) in metric count** — explicitly NOT O(commits) and NOT O(metrics). No per-commit and no per-metric query fan-out (no N+1). A counting connection wrapper in the scale test asserts the executed-statement count stays under a small named ceiling.

## UX (Rev 3)

`compare_pretty.render_compare(compare_result, *, color)` renders one line per metric plus a footer:

```
<metric>   <latest> vs <baseline> <unit>   <arrow> <±pct>%   <CLASSIFICATION>   <sparkline>
...
<sanity-label footer: ✓ reasonable — X of N runs would flag | ⚠ too loose | ⚠ too strict>
```

- Per-metric line: metric name, latest vs baseline, delta arrow (`↑`/`↓`/`→`) + signed %, the classification word, then the sparkline (`▁▂▃▅▇`).
- `regression` is visually emphasized: color path uses bold/red; **color-off path degrades to a plain-text marker** (e.g. a leading `!` / `REGRESSION` word) so emphasis never depends on color alone.
- Sanity label is a single FOOTER line after all metrics — never interleaved mid-metric.
- Color is disabled (no ANSI emitted) under `--no-color`, `NO_COLOR` env, OR non-TTY stdout, reusing `run`'s existing `OutputContext`/color-decision plumbing.
- `--json` (`contracts/compare_v1.py`) is entirely unaffected by color/pretty/TTY state — the payload is color-agnostic.

## Corner Cases (Rev 3)

Every case is handled without crash and without exit `1`. Ownership: pure domain classifies the data-shape cases; the analyzer/CLI handle the empty-baseline and unknown-flow cases.

| # | Case | Owner | Behavior |
|---|---|---|---|
| C1 | First-ever run, known flow (no prior baseline) | analyzer → `regression.classify` (`baseline None`) | `insufficient-data`, exit `0` |
| C2 | Unknown flow (no rows) | CLI | usage error, exit `2` |
| C3 | Single baseline commit (`< min_baseline_commits`) | `regression.classify` (`baseline_commit_n < min_n`) | `insufficient-data` |
| C4 | All-equal / zero-variance (incl. baseline==0) | `statistics` + `regression.classify` guard | `stable`, no divide-by-zero |
| C5 | New metric (latest, absent from baseline) | analyzer (empty baseline rows for that metric) | `insufficient-data` for that metric |
| C6 | Dropped metric (baseline, absent from latest) | analyzer (iterates latest-run metrics) | skipped/noted |
| C7 | Unseen device/mode | store baseline read returns `[]` | `insufficient-data` |
| C8 | Warm-only vs cold-only (mode split) | store `mode=?` filter ⇒ `[]` | `insufficient-data` |
| C9 | Dev-bundle-only history | store `is_dev_bundle` exclusion ⇒ `[]` | `insufficient-data` |

## File Changes

| File | Action | Description |
|---|---|---|
| `domain/regression.py` | Create | Pure direction-aware `classify` → `Verdict`. |
| `domain/statistics.py` | Create | Pure `median`, `median_by_commit`, nearest-rank percentile helper. |
| `domain/calibration.py` | Create (Rev 2) | Pure `grade`/`grade_all` → `MetricCalibration`/`CalibrationReport`; honest-degenerate label logic. |
| `domain/model.py` | Modify | `Verdict` → 4-state + pure data fields; add `RunPoint` read-model. |
| `adapters/analyzer_sql.py` | Create | `SqlAnalyzer` implements `Analyzer`; composes store reads + pure math (verdicts + `calibration.grade_all` from the SAME per-run rows). |
| `adapters/store_sqlite.py` | Modify | `history()` + baseline/latest/system-sample read-models (`?`-bound). `baseline_points` returns per-RUN rows (pre-collapse), **batched across all metrics of a family** (Rev 3, returns a `metric_name` column, `LIMIT`ed to `baseline_n` commits) so it feeds baseline + calibration in ONE query per family. |
| `db/migrations/0002_compare_baseline_index.sql` | Create (Rev 3) | Additive `CREATE INDEX idx_run_baseline ON run(flow_id, device_id, mode, started_at)`; no table/data change. Picked up by the existing `user_version` migration runner. |
| `db/schema.sql` | Modify (Rev 3) | Add the same `idx_run_baseline` to the fresh-DB canonical schema so fresh and migrated DBs converge (INDEX section). |
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
| Golden | pretty + sparkline output (Rev 3: normal multi-metric, regression, insufficient-data, single-point sparkline, `max==min` sparkline edge; sanity label present in pretty AND `--json`; label never changes exit code) | color forced off, `--update-golden` |
| Contract | `--json` verdict shape | fail on unversioned change |
| Performance (Rev 3) | scale: ≈800–1000 runs / 50+ commits / multi-metric / warm+cold in temp SQLite → correct verdict + wall-clock under named budget (`COMPARE_PERF_BUDGET_MS`, e.g. 150) + SQL-statement count under named ceiling (`COMPARE_MAX_SQL_STATEMENTS`) via a counting connection wrapper | seed temp DB, wrap the `sqlite3.Connection` to count `execute`/`executemany`, assert bounds |
| Corner cases (Rev 3) | C1–C9 matrix — each returns the expected classification, never crashes, never exits `1` | seeded temp SQLite / CLI invocation per case |
| E2E-ish | REAL wiring `compare` (no monkeypatch of analyzer) | fake only device/clock; reuse `ReplayDriver`/seed to build history |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process integration. Compare only reads a local SQLite file with `?`-bound values and static SQL identifiers (perf-cli-standards rule 4).

## Migration / Rollout

**Rev 3 changes this from "no migration" to ONE additive index migration.** `db/migrations/0002_compare_baseline_index.sql` adds `idx_run_baseline` only — no table, column, row, or write-path change — applied by the existing `user_version`-driven runner and mirrored into `db/schema.sql` for fresh DBs. Everything else stays purely additive (new command/adapter/domain modules/config fields/read-models); `run`'s ingestion path and table shapes are untouched. Rollback = revert branch (dropping the index is safe and non-destructive; an older binary simply ignores it).

## Open Questions

- [x] ~~Default `threshold_pct` and per-unit floor defaults~~ — RESOLVED (Rev 2, decision #58): `threshold_pct=5.0`, `floors={ms:5, mb:5, pct:3, fps:2}`, `min_baseline_commits=3`, `warmup_k=1`.
- [x] ~~Calibration window == baseline window~~ — RESOLVED (Rev 3): the always-on label uses the SAME windowed rows the baseline read returns (the bounded-performance NFR requires it); a full-history distribution remains the DEFERRED `--calibrate` sweep only.
- [x] ~~Port shape for verdicts + `CalibrationReport`~~ — RESOLVED at tasks (#59): single additive `CompareResult(verdicts, calibration)` carrier, one method.
- [ ] Confirm via `EXPLAIN QUERY PLAN` at apply that `idx_run_baseline` is actually selected for the baseline query (and that batching per family, not per metric, is what the analyzer issues) — validated by the Rev 3 performance test's statement-count guard.
