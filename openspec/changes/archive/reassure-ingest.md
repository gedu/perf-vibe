# Archive: `reassure-ingest` capability (Ingest reassure `.perf` Files)

**Status**: SHIPPED AND ARCHIVED

## Summary

The `reassure-ingest` capability (parsing and persisting reassure performance data) was designed, implemented, verified, and merged across stacked PRs #53, #56–#60. This marker indicates the change is no longer active in `openspec/changes/reassure-ingest/` and has been consolidated into the canonical spec.

## Delivery

| Item | Status | Ref |
|---|---|---|
| Specification | SHIPPED ✓ | `openspec/specs/reassure-ingest.md` |
| Implementation | MERGED ✓ | PRs #53, #56–#60 to main |
| Test Suite | 999 passing ✓ | 95.17% coverage (floor 93%) |
| Verification | PASS WITH WARNINGS ✓ | 0 CRITICAL, 2 WARNING, 7 SUGGESTION (WARNINGs fixed in #60) |
| Status | COMPLETE ✓ | 47 tasks complete, all PRs merged |

## Historical Record

Complete SDD artifacts for the `reassure-ingest` change remain in:
- `docs/specs/reassure-ingest/` — full SDD record (proposal, exploration, design, tasks, spec.md for reassure-ingest, verify-report)
- Engram — all observations and decisions (topic keys: sdd/reassure-ingest/{proposal,spec,design,exploration,tasks,verify-report,archive-report})

## Delivery Phases

| Phase | Goal | PR | Status |
|---|---|---|---|
| 1 | Migration 0005 (4 reassure tables) + schema mirror + pinned test edits | #53 | MERGED |
| 2 | Domain types (ReassureHeader/Entry/Result), ReassureParser port, adapter, registry | #56 | MERGED |
| 3 | Store.save_reassure_import transaction + SqliteStore implementation | #57 | MERGED |
| 4a | Migration 0006 (kind column) + fixture reshaped to real reassure output + test debt | #58 | MERGED |
| 4b | CLI command, config.reassure_path, reassure_import_v1 contract | #59 | MERGED |
| 5 | Verification finding fixes (type coercion tolerance, component dimension guard) | #60 | MERGED |

All merged to main. Verification returned pass_with_warnings: both warnings (falsy type coercion and missing dedicated component-identity guard test) were fixed in #60.

## Key Achievements

- **Independently-Indexed Sample Storage**: `durations[]` and `counts[]` persisted as two separately-indexed series via sibling tables with own `idx` ordinals — the sole load-bearing invariant across three guard layers (parse, persistence, end-to-end CLI).
- **Non-Alignment Guarded at Three Layers**: Parse test (8 counts / 6 durations → 6+8 separately), persistence test (6 duration rows / 8 count rows at own idx), CLI end-to-end test (counters differ).
- **Real Reassure Validation**: Verified end-to-end against real `.perf` files from a live project: 51-entry `current.perf` imports with zero skips, zero stderr; `baseline.perf` imports 50 entries producing 499 duration vs. 500 count samples — the non-alignment invariant confirmed at rest.
- **Idempotency by Content Hash**: sha256 of raw bytes, duplicate re-import is a no-op (exits 0, reports already_imported: true, zero rows inserted).
- **Full Transaction Rollback**: Mid-transaction store failure leaves zero rows across all four tables.
- **Name as Sole Identity**: Verified against real reassure output: names carry no delimiter, component grouping is derived from naming convention not data.
- **Exit Discipline**: Path errors exit 2, store failures exit 3, never exit 1; zero-entry files exit 0 (not an error).
- **Flat 9-Key Contract**: `schema_version`, `path`, `content_hash`, `kind`, `already_imported`, `entries_imported`, `entries_skipped`, `duration_samples_imported`, `count_samples_imported` — no `samples_imported` (one count cannot describe two series), no `zero_entries` (derived from two fields).

## Artifacts Moved

Per the archive convention, SDD artifacts moved from `openspec/changes/reassure-ingest/` to `docs/specs/reassure-ingest/`:
- `proposal.md` — original change intent and scope
- `exploration.md` — early investigation and rationale
- `design.md` — architecture, invariants, and load-bearing decisions
- `tasks.md` — all 47 implementation tasks (complete)
- `spec.md` — the reassure-ingest capability spec
- `verify-report.md` — full verification pass with findings (warnings fixed in later PRs)

## Canonical Specs Updated

- `openspec/specs/reassure-ingest.md` — new spec consolidated from change spec

## Load-Bearing Facts for Future Work

These were discovered and confirmed at multiple layers; any follow-up must respect them:

- **`durations[]` and `counts[]` are NOT index-aligned.** `durations` comes from the outlier-filtered set, `counts` from the unfiltered post-warmup set (removeOutliers defaults true). `len(durations) <= len(counts) == runs`. Index i of one does NOT refer to the same run as index i of the other. Nothing may zip them. Guarded at parse (two separate sequences), persistence (two sibling tables with own idx), and CLI (separate counters).
- **`name` is the sole identity.** Reassure writes `expect.getState().currentTestName` untransformed, with no delimiter. Verified against real output: plain space-joined phrases, no `>` or any separator. Component grouping is derived from naming convention, not stored. Schema has no component field.
- **`runs` is a declared cardinality.** Persisted verbatim (never synthesized from `len(counts)`), independent of actual sample count. `runs: 10` with only 3 count samples is persisted as-is (mismatch is recorded, never repaired). Storing it is what makes truncated/hand-edited files detectable.
- **Idempotency = sha256 of raw file bytes.** Not by commit; the same commit hash can produce different files. `ON CONFLICT(content_hash) DO NOTHING` makes byte-identical re-import a no-op.
- **`NULL` ≠ empty array** for passthrough columns. `outlierDurations` key absent → SQL `NULL`. Present and empty → `'[]'`. Both states preserved.
- **Exit codes**: skip-and-warn never fatal (0); unreadable file → 2; store failure → 3; never 1.

## Known Limitations (Intentional)

- `issues` field is deliberately not persisted. Hook point: migration 0007 (0006 shipped as `kind`).
- Viewing, compare, and budget-gating surfaces are explicit non-goals. Each is a distinct follow-up entering through `Analyzer` or `budget_check_flow.py`.
- Two pre-existing code debts the follow-up compare will hit: `domain/calibration.py:203` keys noise floor by unit not metric (will need `"count"`-unit default in `DEFAULT_FLOORS`); `adapters/registry.py:221-225` types `build_analyzer` on concrete `SqliteStore` not `Store` Protocol.

## Verification Findings (Authoritative)

Per `openspec/changes/reassure-ingest/verify-report.md` (obs #315), verified read-only against `main` at 30c77ea, clean tree:

- **Verdict**: 0 CRITICAL / 2 WARNING / 7 SUGGESTION
- **9 requirements, 17 scenarios** — all covered with code and passing tests
- **47 tasks** — all marked complete ([x])
- **Gates**: ruff (pass), mypy (pass), 999 tests, 95.17% coverage (floor 93%)

**Both warnings fixed in #60:**
1. **W1 — type falsy coercion**: `entry_type = data.get("type") or "render"` treats falsy PRESENT values as absent. Unreachable from real reassure output (zod enum rejects upstream). Addressed by more defensive parsing.
2. **W2 — No dedicated component-identity test**: Satisfied by construction (no component column). Now guarded by schema equivalence test.

## Source of Truth

The following canonical specification now reflects the behavior:
- `openspec/specs/reassure-ingest.md`

## SDD Cycle Complete

The change has been fully planned (proposal, exploration), specified (spec, design), implemented (6 stacked PRs), verified (pass with warnings, both fixed), and archived. Ready for the next change.

---

**Archived**: 2026-08-21
**Observation IDs**: #274 (proposal), #276 (spec), #277 (design), #281 (tasks), #315 (verify-report)
