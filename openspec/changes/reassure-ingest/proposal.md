# Proposal: Ingest reassure `.perf` Files

## Intent

Reassure already ships its own compare and a Danger plugin — but it diffs exactly **two** files (`current.perf` vs `baseline.perf`), both regenerated per CI run and then discarded. That structure structurally cannot answer *"has this test drifted over the last 20 commits?"*: no series is ever retained. perf-vibe already owns durable local SQLite history and one percentile-based regression methodology (`domain/statistics.py:83`, `domain/regression.py`). Landing reassure data in that store is the prerequisite for trend-over-time, and it must come first: the original "filter by component" idea died on inspection because the format has no such field. Real data in SQLite beats designing a viewing surface against guesses.

## Scope

### In Scope
- Migration `0005_add_reassure_tables.sql` + `db/schema.sql` mirror: `reassure_import` / `reassure_entry` plus **independently-indexed** sample storage for the two raw series (`durations` and `counts` are NOT index-aligned — see the settled constraint below); reassure's own mean/stdev not persisted (derivable, second source of truth). Exact table shape is `sdd-design`'s call.
- New `ReassureParser` port + `adapters/reassure_jsonl.py`; `Store.save_reassure_import` + `SqliteStore` implementation (one transaction, rolled back whole); `build_reassure_parser` factory; `config.reassure_path`.
- Flat `perfvibe reassure-import <path>` — single file, config fallback. CLI calls the two ports directly, `compare.py:28,53-80`-style; no `application/` module.
- `contracts/reassure_import_v1.py`, fixture, and parser/store/CLI/contract tests.

### Out of Scope
Comparing reassure runs · history views · name/test filtering · budget gating on reassure metrics. Each is a **distinct follow-up change**. Hook points: compare/history enter through `Analyzer`; gating through `application/budget_check_flow.py`. **This change stores data and prints a confirmation. It judges, compares and gates nothing.**

## Settled Constraints (do not reopen)

| Constraint | Consequence |
|---|---|
| **No component field, no test-file field.** The only identity is the flat `name` — Jest's `currentTestName`, space-joined with NO delimiter, so "by name" and "by test" are the same string. | Any component dimension is **derived**, never stored. No later phase may treat it as data. |
| **`durations` and `counts` are NOT index-aligned.** Verified verbatim in reassure's `packages/measure/src/measure-helpers.tsx` (`processRunResults`): `durations` is built from the outlier-**filtered** set, `counts` from the **unfiltered** post-warmup set, and `removeOutliers` defaults to `true`. So `counts.length === runs` always while `durations.length === runs - outliers.length`. | **Nothing may zip the two series.** They are two independently-indexed series that share an entry. Storing them as index-aligned pairs writes silently mismatched data — no exception, just wrong numbers feeding percentiles. Same family of trap as the component dimension. Also: `outlierDurations` is **absent**, not empty, when `removeOutliers` is off (`outliers?.map(...)`), and `durations` may legitimately be `[]` if every run is an outlier. |
| Reassure gets **own tables**, not the `flow × device × metric → measure` star. | Rejected knowingly, even though the star would have given compare/history/budget-check for free: reassure has no device dimension and carries two value series against a single `duration_ms` column. |
| Header line **and** all three fields (`branch`, `commitHash`, `creationDate`) are optional. | Nothing may depend on `commitHash` being present. |
| Idempotency = sha256 of raw file bytes, `content_hash UNIQUE`, `ON CONFLICT DO NOTHING` — not by commit, matching the many-runs-per-commit precedent at `adapters/store_sqlite.py:372-416`. | Known limitation, correct by design: catches a byte-identical re-import, **not** a same-commit re-run with fresh measurement noise. |
| New contract module is mandatory, not optional. | Every contract test pins the exact key set at every nesting level (`tests/contract/test_compare_v1_contract.py:184` — removing, renaming *or adding* a key fails), and `contracts/history_v1.py:58` forces `device`/`mode` as top-level keys. Precedent: `markers_snippet_v1`, `markers_doctor_v1`. |
| Flat command, no `reassure` sub-app. | `markers_app` (`cli/main.py:137-141`) got one only because two subcommands shipped simultaneously. |
| Exit codes. | Bad line / missing field / unknown `type` → skip + warn, never fatal · unreadable or missing file → `2` · zero entries from a readable file → `0` plus payload flag plus stderr warning · store/transaction failure or unexpected exception → `3`. Never `1`. |
| First slice is **ingest only**. | Chosen so real data lands in SQLite before the viewing/filtering surface is designed. |

## Capabilities

### New Capabilities
- `reassure-ingest`: parse a reassure `.perf` JSON-Lines file, persist it idempotently into dedicated tables, and report the outcome — including malformed-input tolerance, exit-code policy, and the `reassure_import_v1` `--json` contract.

### Modified Capabilities
- None. The migration is additive; no existing command's specified behavior changes.

## Approach

Exploration positions 4.2–4.6 unchanged; 4.1 revised by exploration §1.1 (the two series are separately indexed, so sample storage keeps them independent — EAV is ruled out by `src/perf/db/schema.sql:65`). Raw-storage shape; `warmupDurations`/`outlierDurations` as nullable JSON-text passthrough columns (diagnostic only, no query surface, absent ≠ empty); parser tolerant per line, strict per file; CLI orchestrates two port calls.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/perf/db/migrations/0005_add_reassure_tables.sql` | New | Import/entry tables + independently-indexed sample storage; exact count is `sdd-design`'s call |
| `src/perf/db/schema.sql` | Modified | Mirror DDL (house convention) |
| `src/perf/domain/model.py`, `domain/ports.py` | Modified | Frozen dataclasses; `ReassureParser`; `Store.save_reassure_import` |
| `src/perf/adapters/reassure_jsonl.py` | New | JSONL parser, skip-and-warn |
| `src/perf/adapters/store_sqlite.py`, `registry.py` | Modified | Store method + helpers; plain factory |
| `src/perf/config/loader.py` | Modified | `reassure_path` (input path, not `_under_base`) |
| `src/perf/cli/commands/reassure_import.py`, `cli/main.py` | New/Modified | Flat command + wiring |
| `src/perf/contracts/reassure_import_v1.py` | New | `--json` payload builder |
| `tests/integration/test_schema.py`, `test_store_migrations.py` | Modified | `MIGRATION_0005`; every `== 4` becomes `== 5` |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| ~920 authored lines vs a 400-line review budget (~2.3x) | High | Exploration §5 recommends three slices: parsing/domain → persistence → CLI. Delivery shape is the orchestrator's call at `sdd-tasks`, not pre-empted here. |
| Reader reintroduces a component dimension as if stored | Med | Recorded above as a first-class constraint; spec must assert `name` is the sole identity |
| A follow-up compare zips `durations` with `counts` | High | Recorded above as a settled constraint. The two series are not index-aligned; zipping mis-attributes render counts to durations with no error raised. The sample schema must make the pairing structurally impossible, not merely discouraged. |
| `name` uniqueness within one file unverified | Med | No `UNIQUE(import_id, name)`; any follow-up assuming one row per name defends itself |
| Noise floor is looked up by **unit**, not metric (`domain/calibration.py:203`, `floors.get(unit, 0.0)`) | Med | Out of scope here; the follow-up compare must budget for a `"count"`-unit floor or hit false negatives |
| `build_analyzer` types on concrete `SqliteStore` (`adapters/registry.py:221-225`) | Low | Pre-existing leak; only bites a follow-up compare adapter |

## Rollback Plan

Revert per slice; each is independently revertable. Schema rollback is the standard house path: `0005` is additive-only, so reverting the code leaves three unused tables — a `0006` drop migration removes them if desired. No existing table, view, command, or `--json` payload is touched, so a revert cannot break `run`/`compare`/`budget-check`/`history`.

## Dependencies

- No new packages (stdlib `json`, `hashlib`, `sqlite3`).
- A real reassure `.perf` sample committed as a fixture; format facts limited to exploration §1.
- Project gates: 93% coverage floor (`AGENTS.md:40`), strict TDD (RED first, RED for the right reason), venv commands (`./.venv/bin/pytest -q --cov=perf`, `./.venv/bin/ruff check .`, `./.venv/bin/mypy src/perf`).

## Success Criteria

- [ ] `perfvibe reassure-import <fixture> --json` exits `0` and reports the imported entry/sample counts; re-running the identical file exits `0` and inserts zero new rows.
- [ ] Migration lifts `user_version` 4 → 5 and `schema.sql` stays fully equivalent to the migration chain (`test_schema_sql_and_migrations_are_fully_equivalent` passes).
- [ ] A fixture with no header, one invalid-JSON line, one unknown `type`, and one missing required field imports every good line, warns per skipped line on stderr, and exits `0`.
- [ ] Missing/unreadable path → exit `2`; a forced store failure → exit `3` with zero rows persisted; zero-entry readable file → exit `0` with the payload flag set.
- [ ] Contract test pins the exact `reassure_import_v1` key set at every level; stdout stays byte-pure under `--json`.
- [ ] `ruff`, `mypy`, and the 93% coverage floor all pass.

## Open Questions

None on the product. Exploration answered all seven open questions and took a position on each; nothing was left for this phase to decide. The one item still outstanding is a **delivery** decision (single oversized PR vs the recommended three-slice chain), which belongs to the orchestrator at `sdd-tasks`, not to the product proposal.
