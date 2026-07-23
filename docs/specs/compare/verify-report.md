# Verify Report: `compare` capability (Phase 2)

**Date**: 2026-07-23
**Verified against**: `openspec/changes/compare/{proposal,spec,design,tasks}.md` (all Rev 3)
**Implementation state**: merged to `main` via PR-A (`ba95f42`), PR-B (`75fc1c3`), PR-C (`f6de697`); working tree clean, `main` up to date with `origin/main`.

## Test Suite

`./.venv/bin/pytest -q` → **328 passed**, 0 failed (matches confirmed baseline; no regressions from a fresh full run).

Targeted re-runs, all green:
- `tests/integration/test_store_migrations.py tests/integration/test_schema.py` — 26 passed (migration/index convergence)
- `tests/integration/test_compare_perf.py tests/contract/test_compare_v1_contract.py tests/unit/test_domain_boundary.py` — 14 passed
- `tests/golden/test_compare_pretty_golden.py` — 11 passed

`ruff`/`mypy` are not wired into `pyproject.toml` (`[tool.ruff]`/`[tool.mypy]` absent) — confirmed via direct grep. Per the scope guardrail this is a known, pre-existing gap; reported as SUGGESTION only, not CRITICAL/WARNING.

## Spec Conformance (spec.md Rev 3)

All requirements checked against code, not just task checkboxes:

- **Verdict Computation / Baseline Correctness** — `domain/statistics.median_by_commit` + `adapters/store_sqlite.baseline_measure_points`/`baseline_system_sample_points` collapse repeated same-commit runs, exclude dev-bundle + current commit (`COALESCE(r.is_dev_bundle,0)=0`, `r.git_commit<>?` — `store_sqlite.py:602-605`), never mix warm/cold/device (`mode`/`device_key` bound filters). Matches design's SQL sketch.
- **Direction-Aware Classification** — `domain/regression.py:73-74` (`worse = delta < 0 if higher_is_better else delta > 0`), FPS-drop-is-regression and duration-rise-is-regression both covered by hypothesis tests (`tests/unit/test_regression.py`).
- **Threshold + Absolute Floor** — `regression.classify` requires BOTH `exceeds_floor` AND `exceeds_threshold` (`regression.py:70-71,76,78`); defaults `threshold_pct=5.0`, `floors={ms:5,mb:5,pct:3,fps:2}`, `min_baseline_commits=3`, `warmup_k=1`, `baseline_n=10` all present in `config/loader.py:88-92` and overridable via `perf.toml` (`_merge` layering, `loader.py:185-215`).
- **Config Sanity Label** — `domain/calibration.py` `grade`/`grade_all`; label logic was CRITICALLY buggy after PR-C (dishonest `too-loose`) but was caught and fixed in the same PR-C review cycle (commit `184ed86`) — the shipped `too-loose` rule (`calibration.py:153-159`, evidence-based: floor suppressed a concrete `>=threshold_pct` step) is honest and matches spec's "Zero flags with a normal floor is not a false too-loose" scenario (`test_stable_baseline_is_reasonable_not_too_loose`). Label never touches exit code or verdict status (verified structurally — `calibration.grade_all` returns a separate `CalibrationReport`, never mutates `Verdict`).
- **Insufficient-Data Classification** — `regression.classify:49` guards `latest is None or baseline is None or baseline_commit_n < min_n or sample_n < min_n` → `insufficient-data`, never falls through to `stable`.
- **Warm-Up Discard Asymmetry** — `adapters/analyzer_sql.py` `_collapse_latest_system_sample`/`_collapse_baseline_system_sample` apply `idx < warmup_k` drop ONLY in the `system_sample` family path; `_compare_measure_family` never references `warmup_k`. Explicit branch, not silent, matches design.
- **Output Contract** — pretty (`compare_pretty.render_compare`) + `--json` (`contracts/compare_v1.py`, `schema_version=1`) both present; non-TTY stderr nudge reuses `run`'s shared `OutputContext.should_nudge_stderr` (`cli/output/context.py:26-31`), wired in `compare.py:119-120`.
- **Exit-Code Discipline** — `cli/commands/compare.py` has exactly three `raise typer.Exit(code=...)` sites: `2` (unknown flow, no history), `3` (two `except Exception` guards, both `# noqa: BLE001`-annotated per skill rule 7/style rule 7), `0` (final line). Grepped for any `code=1`/`exit(1)` — none exist. `regression` still exits `0` (no special-casing by verdict status anywhere in the CLI).
- **Hexagonal Boundary Enforcement** — grepped `domain/*.py` imports for `adapters` — zero matches; `tests/unit/test_domain_boundary.py` (5 tests) passes and explicitly covers `regression.py`/`statistics.py`/`calibration.py`.
- **Bounded Compare Performance (NFR)** — `db/migrations/0002_compare_baseline_index.sql` ships `idx_run_baseline ON run(flow_id, device_id, mode, started_at)`, mirrored in `db/schema.sql:88`; both fresh-DB and pre-Rev3-migrated-DB convergence proven by `tests/integration/test_store_migrations.py` (10 tests, all green). `tests/integration/test_compare_perf.py` seeds ~5101 runs / 301 commits, asserts 5 SQL statements (budget 8) and wall-clock under budget (150ms) via a counting connection proxy + `EXPLAIN QUERY PLAN` — passes. One documented residual: `store_sqlite.py:592-597` comments that the `eligible` CTE technically scans the full `(flow,device,mode)` partition before the `recent` LIMIT narrows it — this is an ACKNOWLEDGED, index-bounded (not full-table) scan, empirically proven fast at 5k-run scale (task 2.13); not rewritten per explicit review instruction. Downgraded to WARNING below (not CRITICAL) since it is disclosed, tested, and does not violate the "no full `run` scan" language (the scan is bounded to the indexed partition, not the whole table).
- **Pretty-Output UX** — `compare_pretty.py`: per-metric line has name/latest-vs-baseline/arrow+%/classification/sparkline; regression gets `!` marker even color-off (`_metric_line:79-89`); sanity label is footer-only (`render_compare:109-111`); no ANSI when `color=False` (verified structurally — `_style` only emits ANSI when `color=True`); `--json` payload construction (`compare_v1.py`) takes no color/TTY parameter at all, so it is provably color-agnostic. 11/11 golden tests pass (5 cases + extensions from task 3.3/3.3a).
- **Corner-Case Handling (C1–C9)** — grepped all compare test files for `C1`..`C9` markers — all nine present with dedicated tests across `test_regression.py`, `test_store_baseline.py`, `test_analyzer_sql.py`, `test_cli_compare.py`. All pass in the full suite.

## Design Conformance (design.md Rev 3)

- Hexagonal layering intact: `domain/regression.py`, `domain/statistics.py`, `domain/calibration.py` are pure, no adapter imports; `adapters/analyzer_sql.py` is the sole `Analyzer` implementation and contains zero raw SQL (all SQL lives in `adapters/store_sqlite.py`) — matches "no SQL lives here" docstring claim, confirmed by reading the file.
- Ports are `typing.Protocol` (`domain/ports.py`); `Analyzer.compare_latest` returns the single additive `CompareResult` carrier as resolved at tasks #59 — no second port method.
- SQL identifier safety: the only f-string-interpolated SQL identifiers are `field` values drawn from `_SYSTEM_SAMPLE_METRIC_FIELDS`, a fixed tuple derived at import time from `SystemSample`'s own dataclass fields (never user/config input) — compliant with skill rule 4's static-identifier exception; every value is `?`-bound.
- "One query, two consumers" — `SqlAnalyzer.compare_latest` builds `per_metric_points` once per family and feeds it to both `regression.classify` (baseline) and `calibration.grade_all` (sanity label) — no second baseline query, matches design and the Rev 2/3 contract.
- Query-count budget — confirmed empirically by `test_compare_perf.py` (5 statements, O(1) in both commits and metrics).
- Additive index migration — confirmed present, tested, and mirrored into `db/schema.sql`.

## Tasks Conformance (tasks.md)

Phases 1–3 (PR-A/B/C): all checkboxes `[x]`, and every checked item was traced to real code/tests above — not a rubber-stamp check. Post-merge review fixes (2.10–2.13, PR-C review fixes 1–2) are also present in the shipped code, not just described in tasks.md prose.

**Phase 4 (Verification) — as tracked in tasks.md, all three items were unchecked prior to this verify pass:**
- **4.1** (run full pytest, confirm domain boundary) — now confirmed GREEN by this verify pass (328 passed, domain boundary test passes). Recommend checking off.
- **4.2** (confirm migration applies cleanly pre-Rev3 → Rev3, fresh/migrated DB converge) — now confirmed GREEN by this verify pass (`test_store_migrations.py`, `test_schema.py`, 26/26 passed). Recommend checking off.
- **4.3** (update `openspec/specs/perf-run.md` "COMPARE PLANNED" row → implemented) — **NOT DONE**. `openspec/specs/perf-run.md:19` still reads `| Median-by-commit baseline, ... | COMPARE | PLANNED (Phase 2+) |` verbatim. This is a genuine, unaddressed task, not a rubber-stamped checkbox — flagged below as WARNING.

## Findings

### CRITICAL
None. No spec requirement, design decision, or non-deferred task is unimplemented in a way that breaks a contract. No exit-1 path exists in `compare`. Domain stays pure. `--json` schema_version is stable and versioned.

### WARNING
1. **Task 4.3 not completed** — `openspec/specs/perf-run.md:19`'s "COMPARE PLANNED" row was never updated to reflect that compare shipped. This is a doc-sync gap (not a code gap): a reader of the canonical `perf-run` spec today would incorrectly believe `compare` is still unimplemented. Low functional risk (compare itself works and is fully tested), but it is an explicit, unaddressed tasks.md item and should be fixed before/at archive — either now or as part of `sdd-archive`'s canonical-spec write.
2. **Documented, not-rewritten scan cost in `baseline_measure_points`/`baseline_system_sample_points`** — the `eligible` CTE (`store_sqlite.py:591-606`) scans the full `(flow_id, device_id, mode)`-indexed partition (via `idx_run_baseline`) before the `recent` CTE's `LIMIT baseline_n` narrows to the actual window. This is disclosed in-code (FIX 4 comment) and empirically proven fast (46.6ms at ~5101 runs / 301 commits, well under the 150ms budget), but it is technically O(partition size) rather than strictly O(baseline_n) — a flow/device/mode partition with, say, 100k historical runs would scan more rows than the bounded-performance NFR's stricter reading implies. Given the explicit review instruction ("not rewritten... scale test as the tripwire") and the passing scale test, this is acceptable as shipped but should be watched — not CRITICAL because it is indexed (not a full `run`-table scan) and has an empirical regression tripwire in place.

### SUGGESTION
1. `ruff`/`mypy` are not wired into `pyproject.toml` (no `[tool.ruff]`/`[tool.mypy]` sections) — pre-existing project-wide gap per `python-style` SKILL.md rule 1, not introduced by this change. Consider adding as a follow-up, non-blocking for this change.
2. Marker `Metric.unit` still always defaults to `'ms'` at `run`-ingestion time (documented as a pre-existing WARNING-2 in `perf-run.md:178`) — `compare`'s `_compare_measure_family` correctly reads whatever unit `run` persisted, so this is not a `compare`-introduced bug, just an inherited upstream fidelity gap worth another look someday.

## Deferred / Out-of-Scope (correctly NOT implemented — not gaps)

- `budget-check` CI gate (exit 1) — absent, as required.
- `perf compare --calibrate` sweep mode — absent, as required.
- Warm-up discard for marker/`measure` metrics — correctly N/A by design (no ordinal), documented, not silently skipped.
- Per-metric threshold override, variance/reliability flag — absent, as required.

## Result

- **status**: `done`
- **executive_summary**: 0 CRITICAL, 2 WARNING, 2 SUGGESTION — `compare` is spec-conformant, design-conformant, fully tested (328/328 passing), and safe to archive once task 4.3's doc-sync gap is resolved.
- **artifacts**: `openspec/changes/compare/verify-report.md`
- **next_recommended**: `sdd-archive` (no CRITICAL blockers; recommend folding the 4.3 fix — updating `openspec/specs/perf-run.md`'s COMPARE row — into the archive step, since archive canonically updates specs anyway)
- **risks**: none blocking; the two WARNINGs above are the only unresolved items and neither is CRITICAL
