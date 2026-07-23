# Proposal: `perf compare` capability (Phase 2 — verdict engine)

## Intent

`perf run` (Phase 1) persists everything a regression verdict needs but computes nothing. Developers currently cannot answer "did my latest run get worse?" without hand-writing SQL. This change adds the verdict engine: `perf compare <flow>` computes and SHOWS a per-metric regression verdict against recent history. First slice is human-facing/informational; the CI gate is deferred.

## Scope

### In Scope
- `perf compare <flow>` command: computes + displays the verdict (pretty sparklines + versioned `--json`).
- Per-metric, direction-aware verdict: latest run p50/p90 vs a median-by-commit baseline over the prior N commits (default 10, configurable).
- Baseline hygiene: same flow+metric+`device_key`, matching warm/cold `mode`, EXCLUDE dev bundles and the current commit.
- 4-state classification: `improvement | stable | regression | insufficient-data` (min-n gating, never a silent "stable").
- Threshold model: `threshold_pct` + absolute floor (both configurable).
- Warm-up discard default K=1 for Flashlight/`system_sample` metrics only (they carry iteration `idx`).
- Exit codes `0`/`2`/`3`, NEVER `1` (verdict is informational output here).

### Out of Scope (deferred)
- `budget-check` CI gate (exit `1` on regression) — planned follow-up SDD change (master-design §18 Phase 3).
- Per-metric threshold overrides; variance/reliability flag.
- Warm-up discard for marker/`measure` metrics — N/A by run's design (no iteration ordinal); documented policy, not a gap.
- `perf run` auto-invoking compare — future CLI-layer seam (decision #29).
- Schema change to run's tables — none expected (run already stores all inputs).

## Capabilities

### New Capabilities
- `compare`: the regression verdict engine — baseline computation, direction-aware classification, pretty + `--json` v1 output.

### Modified Capabilities
- None. `perf run`'s requirements are unchanged; compare consumes its stored metadata (the perf-run spec's "COMPARE PLANNED" row is fulfilled, not altered).

## Approach

Pure domain first: NEW `domain/regression.py` (direction-aware threshold+floor verdict, min-n, insufficient-data) + likely `domain/statistics.py` (percentile/median helpers). A `SqlAnalyzer` adapter implements the EXISTING `Analyzer` Protocol — reuses the shipped `run_metric_summary` view plus a NEW median-by-commit baseline read-model on `SqliteStore` (history + baseline queries). Wire via registry `build_analyzer`, expose `cli/commands/compare.py`, a net-new sparkline pretty renderer, and a versioned `--json` verdict contract (schema_version=1, master-design §13). Add `PerfConfig` fields: `threshold_pct`, `floor`, baseline `N`, warm-up `K`, `min_n`. Extend `Verdict.status` docstring to 4 states.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/perf/domain/regression.py` | New | Pure verdict: direction-aware threshold+floor, min-n, insufficient-data |
| `src/perf/domain/statistics.py` | New (likely) | Percentile/median-by-commit helpers |
| `src/perf/domain/model.py` | Modified | Extend `Verdict.status` docstring to 4 states |
| `src/perf/adapters/store_sqlite.py` | Modified | New history + median-by-commit baseline read-models |
| `src/perf/adapters/analyzer_sql.py` | New | `SqlAnalyzer` implementing existing `Analyzer` Protocol |
| `src/perf/adapters/registry.py` | Modified | `build_analyzer` factory |
| `src/perf/cli/commands/compare.py` | New | `perf compare <flow>` typer command, exit 0/2/3 |
| `src/perf/cli/output/` | New | Sparkline pretty renderer + `--json` v1 verdict builder |
| config (`PerfConfig`/`pyproject.toml`) | Modified | `threshold_pct`, `floor`, baseline `N`, warm-up `K`, `min_n` |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Baseline query mis-groups runs (median-by-commit vs run window) | Med | Dedicated read-model + integration tests on recorded fixtures; §9.4 tightened to by-commit |
| FPS direction inversion (higher-is-better) misclassified | Med | Direction-aware from `metric.higher_is_better`; unit tests both directions (decision #39) |
| `--json` contract drift | Low | Contract test pins schema_version=1 shape |
| Warm-up asymmetry (Flashlight vs marker) confuses users | Low | Documented policy; verdict/output states when K applies |
| Sparse history → false "stable" | Med | `insufficient-data` classification via min-n gating |

## Rollback Plan

Compare is purely additive: a new command, new adapter, new domain module, additive config fields, and a new read-model — no schema migration, no change to `run`'s write path. Rollback = revert the change branch; `perf run` and all Phase 1 behavior are untouched. If only the renderer misbehaves, `--json` remains a stable fallback.

## Dependencies

- Phase 1 `perf run` (SHIPPED) — the persisted data model and `run_metric_summary` view.
- Existing `Analyzer`/`Reporter` Protocols in `domain/ports.py` (declared, unused).
- No new runtime libraries (stdlib `sqlite3`, typer, existing test stack).

## Success Criteria

- [ ] `perf compare <flow>` prints a per-metric verdict (pretty sparklines) and a `schema_version=1` `--json` payload.
- [ ] Verdict is direction-aware, median-by-commit, excludes dev bundles + current commit, groups by device+mode.
- [ ] Classification returns `insufficient-data` (never silent "stable") when baseline commits or post-warm-up iterations < min-n.
- [ ] Warm-up K applies to Flashlight metrics only; marker-metric policy documented in output/spec.
- [ ] Exit codes are `0`/`2`/`3` only — never `1`.
- [ ] No schema migration required; `perf run` behavior unchanged (all Phase 1 tests still pass).
- [ ] Contract test pins the `--json` shape.
