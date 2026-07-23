# Archive: `compare` capability (Phase 2, compare-only slice)

**Status**: SHIPPED AND ARCHIVED

## Summary

The `compare` capability (Phase 2, compare-only slice) was designed, implemented, verified, and merged in PR-A (`ba95f42`), PR-B (`75fc1c3`), PR-C (`f6de697`). This marker indicates the change is no longer active in `openspec/changes/compare/` and has been consolidated into the canonical spec.

## Delivery

| Item | Status | Ref |
|---|---|---|
| Specification | SHIPPED ✓ | `openspec/specs/compare.md` |
| Implementation | MERGED ✓ | PRs #11–#13 to main |
| Verification | PASS ✓ | `docs/specs/compare/verify-report.md` — 0 CRITICAL, 2 WARNING, 2 SUGGESTION |
| Test Suite | 328 passing ✓ | `./.venv/bin/pytest -q` |

## Historical Record

This change used the file-based artifact record only (no engram topic keys exist for `compare`). Complete SDD artifacts for Phase 2 remain in:
- `docs/specs/compare/` — full SDD record (proposal, spec, design, tasks, verify-report)

## Known Limitations

Non-critical gaps documented in the verify report:
1. **WARNING (empirical, accepted)**: `store_sqlite.py`'s `eligible` CTE (baseline queries) scans the full indexed `(flow_id, device_id, mode)` partition before the `recent` CTE's `LIMIT baseline_n` narrows the window — index-bounded, not a full `run`-table scan, but technically O(partition size) rather than strictly O(baseline_n). Empirically proven fast (46.6ms at ~5101 runs / 301 commits, 150ms budget); scale test acts as a regression tripwire. Not rewritten per explicit review instruction.
2. **WARNING**: task 4.3 (updating `openspec/specs/perf-run.md`'s COMPARE row) was left unchecked at verify time — resolved as part of this archive pass.
3. **SUGGESTION**: `Metric.unit` still always defaults to `'ms'` at `run`-ingestion time (pre-existing Phase 1 gap, `perf-run.md` WARNING-2). `compare`'s `_compare_measure_family` correctly reads whatever unit `run` persisted — not a `compare`-introduced bug, an inherited upstream fidelity gap.

All eligible for future housekeeping; none block archive.

## Ruff/Mypy Designation

The python-style skill designates **ruff** and **mypy** integration but implementation remains deferred (no `[tool.ruff]` or `[tool.mypy]` blocks in `pyproject.toml` yet) — a pre-existing, project-wide gap not introduced by `compare`. Treat as a known gap in the canonical spec's "future work" section.

---

**Archived**: 2026-07-23
