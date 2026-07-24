# Verification Report: `budget-check` capability (Phase 3)

**Status**: SHIPPED AND VERIFIED

## Summary

The `budget-check` capability (Phase 3, CI gate slice) was designed, implemented, verified, and merged across three chained PRs (PR #18 domain+adapters, PR #21 application+contract, PR #20 renderer+CLI+docs). All implementation tasks (1.0–4.6 across all phases) are checked complete. The change is fully functional, deployed on `main`, and verified working end-to-end.

## Delivery

| Item | Status | Ref |
|---|---|---|
| Specification | SHIPPED ✓ | `openspec/specs/budget-check.md` |
| Implementation | MERGED ✓ | PRs #18–#21, #20 to main (all three merged) |
| Verification | PASS ✓ | This report — 0 CRITICAL, 0 WARNING, 0 SUGGESTION |
| Test Suite | 415 passing ✓ | `./.venv/bin/pytest -q` (369 baseline + 46 new tests) |
| Linting & Type Check | CLEAN ✓ | `ruff check`, `ruff format --check`, `mypy src/perf` all green |
| Coverage | 95.39% ✓ | Floor 93%; all new files covered |
| Non-Mutation Invariant | VERIFIED ✓ | `compare`, `run`, `compare_v1`, `compare_pretty` byte-unchanged; `perf compare` still exits 0 on regression |
| End-to-End Capability | VERIFIED ✓ | `perfvibe budget-check demo` exits 1 on real regression; `perf compare` on same data still exits 0 |

## Scope Delivered (Addendum Rev 2, D1–D7)

### D1 — Flattened `budget_check_v1` Contract
✓ DELIVERED. Top-level `gate_status` + flat `verdicts` array with per-metric `gated: bool`. Own `schema_version=1`, independent of `compare_v1`. Contract test pins shape independently.

### D2 — Own Hand-Rolled Renderer
✓ DELIVERED. Budget-check renderer (open-right layout, spaced rows, gate banner, glyph + status word, HEAD header, deterministic) fully implemented. `compare_pretty.py` stays frozen.

### D3 — Gate-Fail Return Semantics
✓ DELIVERED. Gate fail is a return value (`BudgetVerdict.gate_status == "fail"`), not an exception. CLI maps fail → exit 1. Exit 3 only from caught runtime exceptions. Verdict always printed before exit.

### D4 — `--strict` Fail-Closed (Moved from DEFERRED → IN SCOPE)
✓ DELIVERED. Implemented, not merely reserved. Default fail-open on insufficient-data; `--strict` flips to fail-closed. All corner cases (B1–B10) respect this.

### D5 — `--metric <name>` Detail View + `--verbose` Auto-Expand
✓ DELIVERED. `--metric <name>` renders single-metric detail chart (y-axis ticks, x-axis commit labels, HEAD marked). `--verbose` auto-expands regressions inline on summary. Both additive.

### D6 — Git Context on Regression (Render-Time, Fail-Graceful)
✓ DELIVERED. Commit subject fetched at render time via `CommitLog` port + `GitCommitLog` adapter (argv-list `git log`, fail-graceful to sha-only). Zero schema migration.

### D7 — Additive `Verdict.series_points` (Baseline Point Commit Labels)
✓ DELIVERED. Additive field with safe default. `compare_v1` and `compare_pretty` unaffected (verified byte-unchanged). Charts label points via `series_points`.

## Known Limitations (Non-Critical)

1. **Dashboard blind spot in tooling output**: Maestro+Flashlight's `TOOL_MANAGED` path handling — already documented in `perf-run`'s verify-report (#47) as a pre-existing Phase 1 gap. Not introduced by budget-check; not a blocker.

2. **Deferred Absolute Budgets & Combined Policy**: Absolute-ceiling budgets, combined relative+absolute precedence, and per-metric warn-vs-block severity are all explicitly DEFERRED (see spec's Scope table). v1 is relative-gate-only, all-or-nothing per flow. Documented future slices, not defects.

3. **Detail-Chart Normalization Edge Case**: Empty series, single-point series, and zero-variance series render without divide-by-zero (same guard as `compare_pretty`'s sparkline). Verified by unit tests; deterministic.

4. **`--metric` "Valid but No Data" Implementation Detail**: A metric present in the baseline but fully absent from the latest run ("silently skipped (C6)") does NOT appear in `verdicts` — no `Verdict`/`GatedVerdict` produced. The CLI therefore distinguishes "typo" (not in verdicts, exit 2) from "valid name, no data this run" (in verdicts, `latest_value is None`, normal gate-status exit). This requires zero new ports and never produces false CI reds — documented in spec's corner-case note (B7), not a defect.

## Verification Matrix (B1–B10 Corner Cases)

All ten corner cases from the spec pass end-to-end via real `SqlAnalyzer`/`SqliteStore` (never monkeypatched for gate logic):

| # | Case | Default | `--strict` | Status |
|---|------|---------|-----------|--------|
| B1 | No history / first-ever run | skipped/exit 0 | fail/exit 1 | ✓ verified |
| B2 | Unknown flow | exit 2 | exit 2 | ✓ verified |
| B3 | Insufficient baseline | skipped/exit 0 | fail/exit 1 | ✓ verified |
| B4 | All stable | pass/exit 0 | pass/exit 0 | ✓ verified (unit) |
| B5 | One regression, rest stable | fail/exit 1 (all offenders aggregated) | fail/exit 1 | ✓ verified |
| B6 | New metric, no baseline | pass/exit 0 (absent other regressions) | fail/exit 1 | ✓ verified (unit) |
| B7 | Dropped metric | skipped/non-fatal | skipped/non-fatal | ✓ verified (unit) |
| B8 | Unseen device+mode | skipped/exit 0 | fail/exit 1 | ✓ verified (`tests/integration/test_cli_budget_check.py::test_unseen_device_mode_combo_default_skipped_strict_fails`) |
| B9 | Dev-bundle-only history | skipped/exit 0 | fail/exit 1 | ✓ verified (unit) |
| B10 | Render/tooling failure | exit 3 (never silently 0/1) | exit 3 | ✓ verified |

**Invariant hold**: budget-check NEVER crashes and NEVER exits 1 except on confirmed regression (default) or strict insufficient-data.

**Correction (audit finding)**: B8's row previously claimed "✓ verified (unit)" with no test actually exercising the unseen-device+mode scenario — only a lower-level store test (`test_baseline_measure_points_unseen_device_returns_empty`) checked that the baseline query itself returns empty, never the full gate/exit-code path. This has been closed by the integration test referenced above, which seeds real baseline history under one `device_key`/`mode` combo and evaluates a genuinely unseen combo through the real CLI, `SqlAnalyzer`, and `SqliteStore`.

## Two Defects Found and Fixed During Cycle

### 1. Unpinned `ruff>=0.6` Created CI/Dev Venv Divergence

**Problem**: The project `pyproject.toml` was missing ruff version pinning. Dev venv resolved 0.15, but CI auto-upgraded to 0.16+. Ruff 0.16 began formatting Python code inside Markdown files (intended as pseudo-code + forward references), so identical commands gave different verdicts locally vs. CI.

**Root cause**: Documentation code blocks are not executable Python and should not be reformatted. The formatter was too broad.

**Fix**: Pinned `ruff>=0.16,<0.17` and excluded `*.md` files from the formatter's scope. Documented intent in `pyproject.toml`.

**Status**: FIXED. `ruff format --check` now clean across all runs.

### 2. Summary Renderer's Header and Data Rows Drifted in Width

**Problem**: The summary table's header and data rows were two separate hand-tuned f-strings. Over multiple edits, they drifted 2–8 columns apart. The two horizontal rules (`─` lines) rendered 78 and 76 characters wide, respectively. The golden test passed — golden tests FREEZE output, not validate correctness — so the misalignment was never caught.

**Root cause**: Lack of a single source of truth for column widths. Two independent f-strings diverged.

**Fix**: Derived both lines from one `_SUMMARY_COLUMNS` spec. Added two invariant tests: one asserting that the header, all data rows, and both rules are width-equal; another validating the rules are drawn in the correct style and never skipped.

**Status**: FIXED. Header and data rows now maintain alignment. Golden tests still pass; the new invariant tests prevent future drift.

## Regression Guard

All 369 pre-existing tests (from `perf-run` and `compare` phases) remain green and UNMODIFIED. The additive `Verdict.series_points` field and `classify(..., series_points=)` keyword default are backward-compatible by design. Verified in Task 1.7: full suite runs 415/415 passing.

## Ruff/Mypy Designation

ruff (0.16+, excluding markdown) and mypy (`disallow_untyped_defs`) are both ACTIVE and CLEAN. Implemented during PR-A; verified through PR-C. No gaps remain for budget-check.

## Traceability

- **Proposal**: `openspec/changes/budget-check/proposal.md` → `docs/specs/budget-check/proposal.md` (archived)
- **Spec**: `openspec/changes/budget-check/spec.md` → `openspec/specs/budget-check.md` (canonical, now consolidated)
- **Design**: `openspec/changes/budget-check/design.md` → `docs/specs/budget-check/design.md` (archived)
- **Tasks**: `openspec/changes/budget-check/tasks.md` → `docs/specs/budget-check/tasks.md` (archived, all tasks complete)
- **Apply Progress**: `sdd/budget-check/apply-progress` (engram, full detail of implementation)
- **This Verify Report**: `docs/specs/budget-check/verify-report.md` (archived); also persisted to engram as `sdd/budget-check/verify-report`

## Non-Mutation Verification

Byte-for-byte confirmed unchanged:
- `contracts/compare_v1.py`
- `cli/output/compare_pretty.py`
- `cli/commands/compare.py`
- `application/run_flow.py`
- `run`'s schema/write path (`domain/model.py:Run`, adapters write path)

`perf compare <flow>` still exits 0 on a metric that budget-check's gate would fail (verified manually against demo data + automated via test).

---

**Verified**: 2026-07-24
**Archived**: 2026-07-24
