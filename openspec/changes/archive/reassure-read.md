# Archive: `reassure-read` capability (Read/Compare Reassure Data)

**Status**: SHIPPED AND ARCHIVED

## Summary

The `reassure-read` capability (a `reassure` sub-app exposing `import|list|entries|show|
history|compare|run`, five versioned `--json` contracts, and full user/agent
documentation) was designed, implemented, verified, and merged across ten stacked PR
slices on `reassure-read/slice5-run`. This marker indicates the change is no longer active
in `openspec/changes/reassure-read.md` and has been consolidated into the canonical specs.

## Delivery

| Item | Status | Ref |
|---|---|---|
| Specification | SHIPPED ✓ | `openspec/specs/reassure-read.md` (new), `openspec/specs/reassure-ingest.md` (amended in `fcc5bf0`) |
| Implementation | MERGED ✓ (branch tip) | `reassure-read/slice5-run` @ `b307a67`, 21 commits over `main`@`d0149c8` |
| Test Suite | 1421 passing ✓ | 95.58% coverage (floor 93%) — re-measured at archive time |
| Verification | FAIL → RESOLVED ✓ | Report `39bd3c8`: 1 CRITICAL (R-1), 1 WARNING (R-2). Both closed in `6e82a7e`, independently re-verified live at archive time. Three more triage findings (W-2, W-5, W-6) closed in `5838850`/`b307a67`/`77905cc`. |
| Status | COMPLETE ✓ | 114/114 tasks complete |

## Historical Record

Complete SDD artifacts for the `reassure-read` change remain in:
- `docs/specs/reassure-read/` — full SDD record (exploration, proposal, spec.md, design,
  tasks, verify-report, archive-report)
- Engram — all observations and decisions (topic keys:
  `sdd/reassure-read/{proposal,spec,design,tasks,verify-report,archive-report}`,
  `product/reassure-cleanup` for accepted future work)

## Delivery Phases

| Slice | Goal | Commit(s) | Status |
|---|---|---|---|
| 0 | D4 duplicate-name detection in the parser | `fcc5bf0` | MERGED |
| 1a | `Store` read methods (imports, entries) + read models | `4ae8983` | MERGED |
| 1b | `reassure_app` + `list`/`entries` + contracts + goldens + alias + docs | `4c98c90`, `b8b8105` | MERGED |
| 1c | typer floor/ceiling pin for the deprecation notice | `cf00b39` | MERGED |
| 2a | `reassure_series`, D5 derivation, `show` domain logic | `1de204c`, `eebc378` | MERGED |
| 2b | `reassure show <name>` CLI (D5 rendering, D8 selection) | `2f5d128` | MERGED |
| 3 | `reassure history <name>` + contract + chart renderer + docs | `402dc14` | MERGED |
| 4a | D7 floor + baseline window + pure verdict function + `median_by_commit` guard test | `ae75864` | MERGED |
| 4b | `reassure compare <name>` + contract + table renderer + docs | `7ad7a9c` | MERGED |
| 5 | `reassure run` + init-wizard block (D6: last) | `c4d9ff1` | MERGED |
| — | Verification report landed | `b0e71c1` | — |
| — | C-1/C-2 fix (never exit 1 on unlaunchable command; docs) | `39bd3c8` | MERGED |
| — | Re-verification section appended | `d98465f` | — |
| — | R-1/R-2 fix (NUL byte rejection; stream-vs-launch error) | `6e82a7e` | MERGED |
| — | W-2 fix (drop `baseline_commit_n` from the reassure wire) | `5838850` | MERGED |
| — | W-6 fix (sanitize control characters in pretty views) | `77905cc` | MERGED |
| — | W-5 fix (warn on `compare`'s silent walk-back) | `b307a67` | MERGED |

All merged to this branch (not yet to `main` as of archive time — see the worktree's own
branch state). Verification returned `fail` at first pass on two CRITICAL findings (C-1,
C-2), both closed. Re-verification at `39bd3c8` found the verdict still `fail` on one new,
smaller CRITICAL (R-1) plus one WARNING (R-2); both closed in `6e82a7e`. Three further
WARNING findings from the same triage pass (W-2, W-5, W-6) were closed in three follow-up
commits, independently confirmed at archive time by reading each diff, re-running the full
gate suite, and live-reproducing the two exit-code claims (C-1, R-1) against the real
`perfvibe` binary.

## Key Achievements

- **Read-only observability for a write-only capability**: `reassure-import` shipped
  without any way to read its own data back; `rg -i reassure` returned zero matches across
  every user- and agent-facing doc before this change.
- **No Analyzer port** — corrects `openspec/specs/reassure-ingest.md`'s prior inaccurate
  claim that this follow-up enters "through `Analyzer`"; the CLI calls `Store` read methods
  and pure `domain/` functions directly, since `Analyzer.compare_latest` is flow/device/
  mode-keyed and reassure has none of those dimensions.
- **Two structural invariants enforced by type shape, not convention**: I1 (never zip
  `durations[]`/`counts[]`) — no read model carries a raw sample array, only separately-
  named already-reduced `HistoryMetric`s. I2 (`median_by_commit` never touches reassure) —
  guarded by two RED tests including a real verified same-commit/same-branch pair from
  `0006_add_reassure_import_kind.sql` that must stay two distinct history points.
- **D4 duplicate-name detection**: any `name` occurring more than once within one import
  has ALL copies dropped (never "keep the first"), bumping `reassure_import_v1` to
  `SCHEMA_VERSION = 3` with its own `entries_dropped_duplicate_name` key.
- **D5 state-transition rendering**: `issues.initialUpdateCount` renders as "extra mount
  render introduced" on a `0 → 1` transition, never a `delta_pct`; `NULL` (absent
  diagnostic) is never conflated with `0` (present, zero).
- **Never exits `1`**: all seven subcommands stay within `0`/`2`/`3`, including `reassure
  compare` on a confirmed regression (D3 — show-only, no gate) and `reassure run` on an
  unlaunchable configured command (fixed post-verification, C-1/R-1).
- **Deprecated flat alias kept working**: `reassure-import` re-registers the identical
  function object as `reassure import`, hidden from `--help`, native `deprecated=` stderr
  notice — no shim, verified against the installed vendored `click` inside `typer`.

## Artifacts (Unchanged Location — Project Convention)

Per this project's convention (confirmed against `docs/specs/reassure-ingest/`, which
stayed in place after its own archive), the per-change SDD record is **not** moved:
- `docs/specs/reassure-read/exploration.md`
- `docs/specs/reassure-read/proposal.md`
- `docs/specs/reassure-read/spec.md` — both spec deltas (new `reassure-read` capability,
  amendment to `reassure-ingest`)
- `docs/specs/reassure-read/design.md`
- `docs/specs/reassure-read/tasks.md` — 114/114 complete
- `docs/specs/reassure-read/verify-report.md` — full verification pass, `fail` at time of
  writing, superseded by the Final Disposition table in `archive-report.md`
- `docs/specs/reassure-read/archive-report.md` — this change's terminal record

## Canonical Specs Updated

- `openspec/specs/reassure-read.md` — new capability spec, mechanically extracted from the
  change's delta spec
- `openspec/specs/reassure-ingest.md` — D4 amendment and the Non-Goals "through Analyzer"
  correction, both already merged in `fcc5bf0` prior to this archive

## Load-Bearing Facts for Future Work

- **`commit_hash` is a label, never a grouping key (D2).** Ordering is
  `created_date` primary, `imported_at` fallback. One import always produces exactly one
  series point; `statistics.median_by_commit()` must never touch reassure data.
- **`name` is the sole identity, and it is not stable between imports.** A stale-entry
  cleanup command (confirm/delete/**rename**) is accepted future work, not part of this
  change — see Engram `product/reassure-cleanup` (obs #379). Renaming a test in source
  silently splits one series into two under the current model.
- **`reassure compare` always exits `0`, even on a confirmed regression (D3).** An agent
  gating CI on this command's exit code would never observe a failure; this asymmetry is
  documented at length in `AGENTS.md`/`CLAUDE.md`.
- **Never verified on CI's pinned Python 3.11 (W-7, open).** Every gate number recorded in
  `archive-report.md` was measured against this worktree's own `.venv` interpreter, not
  CI's pin. This is the most consequential item this change ships without closing.

## Known Limitations (Intentional Or Deferred)

- No gate: `reassure budget-check` does not exist and is out of scope (D3).
- `reassure_import.kind` is never read by any query, filter, or comparison in this
  capability (locked by `0006_add_reassure_import_kind.sql`).
- No component/test-file dimension derivation from `name`.
- W-1 (`MIN_BASELINE_IMPORTS` doubles as a min-samples gate), W-3 (narrower-than-spec AST
  guard), W-4 (`--limit -1` leaks SQLite semantics), W-8 (`entries` out-of-range id exits
  `3` not `2`), W-9 (PR3 over budget with no recorded exception), and six SUGGESTION-level
  findings are all open, deliberately not fixed in this change — full list in
  `archive-report.md`.
- One TDD lapse: `reassure_show_pretty.py` was written before its golden RED test.

## Verification Findings (Authoritative — See `archive-report.md` For The Full Table)

Per `docs/specs/reassure-read/verify-report.md` (Engram obs #375), re-verified at `39bd3c8`:

- **Verdict at that commit**: `fail` — 1 blocker, 1 CRITICAL (R-1), 1 WARNING (R-2)
- **15 requirements, 24 scenarios**, admitted by `gentle-ai sdd-verify-validate`
- **114 tasks** — all marked complete (`[x]`)
- **Gates at `39bd3c8`**: ruff/format/mypy clean; 1392 passed, 95.57% coverage

**Both R-1 and R-2 closed in `6e82a7e`**, independently re-verified at archive time (code
diff read, full gate suite re-run, live reproduction of the exit-code fix against the real
binary). **Three further WARNING findings** (W-2, W-5, W-6) surfaced from the same triage
pass and closed in `5838850`/`b307a67`/`77905cc`. **Final gates, re-measured by the archive
phase**: ruff/format/mypy clean, 73 source files; **1421 passed**, coverage **95.58%**.

## Source of Truth

The following canonical specifications now reflect the behavior:
- `openspec/specs/reassure-read.md`
- `openspec/specs/reassure-ingest.md`

## SDD Cycle Complete

The change has been fully planned (exploration, proposal), specified (spec, design),
implemented (10 stacked slices + 5 post-verification fix commits), verified (fail →
resolved, with open WARNING/SUGGESTION debt recorded rather than hidden), and archived.
Ready for the next change.

---

**Archived**: 2026-09-04
**Observation IDs**: #343 (proposal), #346 (spec), #348 (design), #351 (tasks), #375
(verify-report), #379 (future-work decision), archive-report (assigned on save)
