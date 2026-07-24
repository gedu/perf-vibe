# Archive: `budget-check` capability (Phase 3, CI gate slice)

**Status**: SHIPPED AND ARCHIVED

## Summary

The `budget-check` capability (Phase 3, CI gate slice) was designed, implemented, verified, and merged in PR-A (#18 domain+adapters), PR-B (#21 application+contract), PR-C (#20 renderer+CLI+docs). This marker indicates the change is no longer active in `openspec/changes/budget-check/` and has been consolidated into the canonical spec.

## Delivery

| Item | Status | Ref |
|---|---|---|
| Specification | SHIPPED ✓ | `openspec/specs/budget-check.md` |
| Implementation | MERGED ✓ | PRs #18, #21, #20 to main |
| Verification | PASS ✓ | `docs/specs/budget-check/verify-report.md` — 0 CRITICAL, 0 WARNING, 0 SUGGESTION |
| Test Suite | 415 passing ✓ | `./.venv/bin/pytest -q` (369 baseline + 46 new) |
| Non-Mutation Invariant | VERIFIED ✓ | `compare`, `run`, `compare_v1`, `compare_pretty` byte-unchanged; `perf compare` still exits 0 on regression |

## Historical Record

Complete SDD artifacts for Phase 3 remain in:
- `docs/specs/budget-check/` — full SDD record (proposal, spec, design, tasks, verify-report)

## Known Limitations (Non-Critical)

Two defects found and fixed during the cycle, both worth recording:

1. **Unpinned `ruff>=0.6` let CI resolve 0.16 while the dev venv sat on 0.15; 0.16 formats Python inside Markdown, so identical commands gave different verdicts locally and in CI.** Fixed by pinning `ruff>=0.16,<0.17` and excluding `*.md` from the formatter (documentation states intent — pseudo-code and forward references — and cannot satisfy a code formatter).

2. **The summary renderer's header and data rows were two hand-tuned f-strings that drifted 2–8 columns apart, with the two horizontal rules rendering 78 and 76 characters wide. The golden test froze the misalignment rather than catching it — a golden proves output is stable, never that it is correct.** Fixed by deriving both lines from one `_SUMMARY_COLUMNS` spec, plus two invariant tests that fail against the previous output.

All non-critical gaps documented in the verify report; none block archive.

## Scope Shipped (Addendum Rev 2 Decisions D1–D7)

- **D1 — Flattened `budget_check_v1` Contract**: top-level `gate_status` + flat `verdicts` with per-metric `gated: bool`, own `schema_version=1`, independent contract test
- **D2 — Own Hand-Rolled Renderer**: open-right layout (top+bottom rule + left rail, no right border), spaced rows, gate banner, glyph+status word emphasis, HEAD header, deterministic
- **D3 — Gate-Fail Return Semantics**: gate fail is a return value (not exception), CLI maps fail→exit 1, exit 3 only from runtime exceptions, verdict always printed before exit
- **D4 — `--strict` Fail-Closed** (moved from DEFERRED): fully implemented, not merely reserved; default fail-open, `--strict` flips to fail-closed for insufficient-data
- **D5 — `--metric <name>` Detail View + `--verbose` Auto-Expand**: drill-down chart (y-axis ticks, x-axis commit labels, HEAD marked), summary auto-expands regressions inline
- **D6 — Git Context on Regression** (render-time, fail-graceful): `CommitLog` port + `GitCommitLog` adapter, argv-list `git log`, fails to sha-only when repo unavailable
- **D7 — Additive `Verdict.series_points`** (baseline point commit labels): backward-compatible field with safe default; `compare_v1`/`compare_pretty` unaffected (verified byte-unchanged)

## Ruff/Mypy Designation

ruff (0.16+, excluding markdown) and mypy (`disallow_untyped_defs`) are both ACTIVE and CLEAN across the implementation. No gaps remain.

---

**Archived**: 2026-07-24
