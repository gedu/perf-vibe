# Archive: `perf run` capability (Phase 1)

**Status**: SHIPPED AND ARCHIVED

## Summary

The `perf run` capability (Phase 1) was designed, implemented, verified, and merged in PRs #1–#3. This marker indicates the change is no longer active in `openspec/changes/perf-run/` and has been consolidated into the canonical spec.

## Delivery

| Item | Status | Ref |
|---|---|---|
| Specification | SHIPPED ✓ | `openspec/specs/perf-run.md` |
| Implementation | MERGED ✓ | PRs #1–#3 to main |
| Verification | PASS WITH WARNINGS ✓ | engram #47 (verify-report) |
| Test Suite | 197 passing ✓ | `./.venv/bin/pytest -q` |

## Historical Record

Complete SDD artifacts for Phase 1 remain in:
- `docs/specs/perf-run/` — full SDD record (proposal, spec, design, tasks, README)
- engram — all decisions and observations (topic keys: perf-cli/propose/perf-run, sdd/perf-run/spec, perf-cli/design/perf-run, perf-cli/tasks/perf-run, sdd/perf-run/verify-report, and related decisions/apply-reports)

## Known Limitations

Two non-critical gaps documented in verify report #47:
1. **WARNING-1** (HIGH): Secret-scrub blind spot in Maestro+Flashlight (`TOOL_MANAGED`) path only. Fix targeted: scrub diagnostics against inner argv + add test. Does not block Phase 1 completion.
2. **WARNING-2** (LOW): Marker unit not persisted (metric.unit always 'ms' in DB). DB-fidelity gap; future COMPARE won't misread. Acceptable for Phase 1.

Both eligible for Phase 2+ housekeeping; neither blocks archive.

## Ruff/Mypy Designation

The python-style skill designates **ruff** and **mypy** integration but implementation is deferred to Phase 2+ housekeeping (no `[tool.ruff]` or `[tool.mypy]` blocks in `pyproject.toml` yet). Treat as a known gap in the canonical spec's "future work" section.

---

**Archived**: 2026-07-23
