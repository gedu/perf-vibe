# Archive: `markers` command (snippet + doctor)

**Status**: SHIPPED AND ARCHIVED

## Summary

The `markers` command group (Phase 1–4) was designed, implemented, verified, and merged across stacked PRs #48–#51. This marker indicates the change is no longer active in `openspec/changes/markers-command/` and has been consolidated into the canonical spec.

## Delivery

| Item | Status | Ref |
|---|---|---|
| Specification | SHIPPED ✓ | `openspec/specs/markers.md` |
| Implementation | MERGED ✓ | PRs #48–#51 to main |
| Verification | PASS WITH FINDINGS ✓ | 928 tests passed, 95.01% coverage |
| Test Suite | 928 passing ✓ | Full suite green |

## Historical Record

Complete SDD artifacts for the `markers` change remain in:
- `docs/specs/markers-command/` — full SDD record (proposal, spec, design, exploration, tasks, verify-report)
- Engram — all observations and decisions (topic keys: sdd/markers-command/{proposal,spec,design,tasks,apply-progress,verify-report,archive-report})

## Delivery Phases

| Phase | Goal | PR | Status |
|---|---|---|---|
| 1 | PERF_TAG promotion + classify_line extraction | #48 | MERGED (882 passed, 94.96%) |
| 2 | snippet/doctor --json contracts | #49 | MERGED (901 passed, 94.98%) |
| 3 | markers sub-app + wiring + integration tests | #50 | MERGED (928 passed, 95.01%) |
| 4 | README/docs + doc-sync test | #51 | MERGED (928 passed, 95.01%) |

## Key Achievements

- **Shared PERF_TAG constant**: public in `domain/model.py`; both parser and snippet import it
- **Line classifier**: public `classify_line(raw_line) -> LineVerdict` shared by `parse()` and `markers doctor`
- **Two subcommands**: `markers snippet [--lang ts|js] [--json]` and `markers doctor [<line>] [--json]`
- **Exit discipline**: snippet exits 0/2; doctor exits 0/2/3 (never 1)
- **Anti-drift**: emitted snippet parses cleanly through real parser
- **Documentation**: README instrumentation section + docs/commands.md entry with schema_version table
- **Coverage**: 100% on new modules; full suite 95.01% (gate: 93)

## Review Findings

All findings from verification phases reconciled:
- **C-1 (Correctness)**: Snippet refactored to match user's proven-working module (default import + try/catch)
- **W-1 (Test coverage)**: Anti-drift test now derives emitted sample from snippet body
- **W-2 (Usability)**: Group-level help (`markers --help`) now functional via context_settings
- **S-1–S-3 (Suggestions)**: Logged as non-blocking; all spec-conformant or pre-existing

## Known Limitations

1. **S-1**: Non-TTY-but-empty stdin with argument rejected as ambiguous (spec-conformant)
2. **S-2**: Colon in marker name reported malformed (pre-existing parser regex)
3. **S-3**: Reused parse() diagnostic mentions adb in doctor context (spec-required)

None block production use.

## Canonical Specs Updated

- `openspec/specs/markers.md` — new spec consolidated from delta

## Source of Truth

The following canonical specification now reflects the new behavior:
- `openspec/specs/markers.md`

## SDD Cycle Complete

The change has been fully planned (proposal), specified (spec, design, exploration), implemented (4 stacked PRs), verified (928 tests, 95.01% coverage), and archived. Ready for the next change.

---

**Archived**: 2026-07-31
**Observation IDs**: #108 (proposal), #109 (spec), #110 (design), #111 (tasks), #113 (verify-report), #114 (archive-report)
