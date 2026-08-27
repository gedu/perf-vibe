# Change: `reassure-read`

**Status**: ACTIVE — proposal complete, spec and design pending

## Summary

Make persisted `@callstack/reassure` data readable and comparable. `reassure-import`
ships today and writes; nothing reads it back, and `rg -i reassure` returns zero
matches across every user- and agent-facing doc. This change adds a `reassure`
sub-app (`import|list|entries|show|history|compare|run`), the `Store` read methods and
pure `domain/` reductions behind it, five versioned `--json` contracts, and the
documentation that makes the surface discoverable.

## SDD Artifacts

| Phase | Location | Status |
|---|---|---|
| Exploration | `docs/specs/reassure-read/exploration.md` · Engram `sdd/reassure-read/explore` | COMPLETE |
| Decisions D1-D6 | Engram `sdd/reassure-read/decisions` | LOCKED |
| Proposal | `docs/specs/reassure-read/proposal.md` · Engram `sdd/reassure-read/proposal` | COMPLETE |
| Spec | `docs/specs/reassure-read/spec.md` | PENDING |
| Design | `docs/specs/reassure-read/design.md` | PENDING |
| Tasks | `docs/specs/reassure-read/tasks.md` | PENDING |
| Verify | `docs/specs/reassure-read/verify-report.md` | PENDING |

## Capabilities

- **New**: `reassure-read` — the `reassure` sub-app surface, its read models,
  ordering rules, exit-code discipline, five `--json` contracts, and the deprecation
  of the flat `reassure-import` alias.
- **Modified**: `reassure-ingest` — D4 duplicate-name detection is a write-path
  behavior change and needs its own delta spec.

## Locked Decisions

| ID | Decision |
|---|---|
| D1 | CLI surface is a `reassure` sub-app (`markers` precedent, `cli/main.py:147`). The flat `reassure-import` stays as a deprecated, hidden alias — it is shipped public surface. |
| D2 | `created_date` is the primary chronological key, `imported_at` the fallback. `commit_hash` is a LABEL only: never a grouping key, filter, or join. One import = one series point. No per-commit medians. |
| D3 | No gate in v1. `reassure compare` is show-only, always exits `0`. No `reassure budget-check`. |
| D4 | Duplicate `name` within an import: detect, warn, drop **all** copies. Never "keep the first". |
| D5 | `issues.initialUpdateCount` is a state transition, not a metric. `0 -> 1` renders as "extra mount render introduced", never a `delta_pct`. `NULL` and `0` stay distinct. |
| D6 | `reassure run` is the LAST slice. Rejected as a `run` driver: `run` persists keyed by flow+device+mode and reassure has none of those dimensions. |
| D7 | `DEFAULT_FLOORS` (`config/loader.py:66`) gets an explicit `"count": 0.0` with a comment explaining WHY it is zero — the floor suppresses timing jitter, render counts are deterministic, so `threshold_pct` alone is the correct guard. Explicit rather than silently inherited from `_effective_floor`'s `self._floors.get(unit, 0.0)` (`adapters/analyzer_sql.py:156`). |
| D8 | `reassure show <name>` defaults to the most recent import by `created_date` (fallback `imported_at`), with `--import <id>` to select another — mirroring how `compare` defaults to the latest run. |

## Architecture Decision: No Analyzer Port

`openspec/specs/reassure-ingest.md:16-17` says this follow-up enters "through
`Analyzer` (compare/history)". **That sentence is inaccurate and must be corrected
when that spec is next touched.** `Analyzer.compare_latest(flow_name, device_key,
mode)` (`domain/ports.py:154`) is flow-keyed and reassure has no flow, device, or
mode dimension.

Chosen shape: **the CLI calls `Store` read methods and pure `domain/` functions
directly** — no new Protocol. Direct precedent in this same feature
(`cli/commands/reassure_import.py:1-5`, "no `application/` use-case") and in the
closest analogue (`cli/commands/history.py:81`). A sibling `ReassureAnalyzer` port
was rejected under `python-architecture` rule 3 (no Protocol for one implementation;
the comparison is pure math, not a new I/O seam). Generalizing `Analyzer` was
rejected as dangerous: it drags reassure into the shared `CompareResult` consumed by
`budget_check_v1`, and `AnalyzerSql` calls `median_by_commit` at `analyzer_sql.py:196`
and `:253`.

## Hard Guard

**`statistics.median_by_commit()` (`domain/statistics.py:83`) MUST NEVER touch
reassure data.** It collapses repeated same-commit runs into one point, directly
violating D2. It works mechanically and raises nothing.
`db/migrations/0006_add_reassure_import_kind.sql` records verified evidence that a real
`baseline.perf`/`current.perf` pair declared the same commit AND the same branch. Slice
4a must ship a test that fails if a same-commit pair collapses.

## Delivery

Nine stacked PRs against a 400-line review budget (`openspec/config.yaml:90`).
Layer-splitting is house precedent — this capability's predecessor shipped as
"PR-A domain -> PR-B store -> PR-C CLI" (`openspec/config.yaml:91`).

| # | Content | Est. |
|---|---|---|
| 0 | D4 duplicate-name detection in the parser | 60-100 |
| 1a | `Store` read methods (imports, entries) + read models | 180-230 |
| 1b | `reassure_app` + `list`/`entries` + contracts + goldens + alias + docs | 220-280 |
| 2 | `reassure show <name>` (D5 rendering, D8 selection) | 200-250 |
| 3a | Per-name series read method + pure per-series p50/p90/n | 180-220 |
| 3b | `reassure history <name>` + contract + chart renderer + docs | 200-250 |
| 4a | D7 floor + baseline window + pure verdict function + `median_by_commit` guard test | 200-250 |
| 4b | `reassure compare <name>` + contract + table renderer + docs | 200-250 |
| 5 | `reassure run` + init-wizard block (D6: last) | 200-300 |

Slice 0 must precede every read model. Slice 4 is the riskiest.

## Documentation Is a Deliverable

The user's requirement: usable by **users first, agents second, and fully
documented**. Docs ride along with the slice that introduces each fact.

Surface: `README.md` (fix the already-stale "six commands" heading; collapse reassure
to exactly ONE row, mirroring how `markers` is presented), `docs/commands.md`,
`docs/configuring-flows.md` (`reassure_path`, documented nowhere today),
`docs/baselines-and-history.md` (per-import vs per-commit baselines contrasted
explicitly), `AGENTS.md`, `CLAUDE.md`.

## Open Questions Before `sdd-spec`

1. D4 accounting — `entries_skipped` (meaning change) vs a new
   `entries_dropped_duplicate_name` key. Both bump `reassure_import_v1` to
   `SCHEMA_VERSION = 3`.
2. `reassure list` default window — assumed `--limit 50`, matching `history`.
3. Deprecation horizon for the flat `reassure-import` alias — assumed indefinite.

## Next

`sdd-spec` and `sdd-design` (can run in parallel).
