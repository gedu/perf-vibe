# Tasks: Ingest reassure `.perf` Files

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | ~965 total (145 + 300 + 240 + 280) across 4 slices |
| 400-line budget risk | Low per-slice (largest slice ~300 lines); High if delivered as one PR |
| Chained PRs recommended | Yes |
| Suggested split | PR1 -> PR2 -> PR3 -> PR4, each independently mergeable |
| Delivery strategy | chained (resolved) |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: Low

Rationale: `sdd-design` revised the forecast to ~960-1060 authored lines (~2.3x the
400-line budget as a single PR). Eduardo decided at the `ask-on-risk` guard (Engram
`sdd/reassure-ingest/state`, decision #280): 4 stacked PRs, `stacked-to-main` (matches
the repo's existing `perf-run` sequential #34/#35/#36 pattern). No `size:exception`.
The slice boundaries are fixed by that decision and are not reopened here.

### Suggested Work Units

| Unit | Goal | PR | Focused test command | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| 1 | Migration + schema mirror + pinned migration-count test edits | PR1 | `./.venv/bin/pytest -q tests/integration/test_schema.py tests/integration/test_store_migrations.py` | N/A — pure DDL, no CLI/runtime surface exists yet | Delete `0005_add_reassure_tables.sql` + revert `schema.sql`/`test_schema.py`/`test_store_migrations.py`; no other module reads these tables |
| 2 | Domain value objects + `ReassureParser` port + adapter + registry factory + fixture | PR2 | `./.venv/bin/pytest -q tests/unit/test_reassure_jsonl.py` | N/A — no CLI wiring yet; parser exercised only via unit tests against the real fixture | Delete `adapters/reassure_jsonl.py` + revert `domain/model.py`/`domain/ports.py`/`adapters/registry.py`; nothing else imports them |
| 3 | `Store.save_reassure_import` + `SqliteStore` implementation (one transaction) | PR3 | `./.venv/bin/pytest -q tests/integration/test_store_reassure.py` | N/A — no CLI wiring yet; store method exercised via integration tests against a real SQLite file | Drop the method + 4 helpers; the tables (already on `main` since PR1) stay unused |
| 4 | `config.reassure_path` + CLI command + `main.py` wiring + `reassure_import_v1` contract | PR4 | `./.venv/bin/pytest -q tests/contract/test_reassure_import_v1_contract.py tests/integration/test_cli_reassure_import.py` | `perfvibe reassure-import tests/fixtures/reassure_sample.perf --json` | Unregister the command in `main.py`; the ports remain callable but unreached |

---

## PR1 — Migration + Pinned Test Edits

**Branch**: `reassure-ingest/pr1-migration` · **Base**: `main` · **Est. lines**: ~145

**Why this is one atomic slice**: `SqliteStore._migrate` (`adapters/store_sqlite.py:234`,
called from `__init__` at `:196`) applies every pending migration in one cascade on
open. The moment `0005_*.sql` exists on disk, six `== 4` assertions in
`tests/integration/test_store_migrations.py` break regardless of whether any other code
exists. Splitting the DDL from these test edits leaves `main` red between merges.

**Independently reviewable**: additive DDL only — no existing table/view/row touched, so
review is "does the DDL match the design + do the pinned tests reflect version 5".
**Independently revertable**: delete the migration file and revert the three test files;
no other module references these tables yet.

- [x] 1.1 RED — `tests/integration/test_store_migrations.py`: change `== 4` -> `== 5` at
  lines 30, 86, 138, 174, 228, 245. Reword the inline chain-enumerating comments at
  lines 86, 138, 245 to list `0005` (not just the digits). Confirm this fails now
  (store still migrates to version 4 — no `0005` file exists).
- [x] 1.2 RED — `tests/integration/test_schema.py`: add the `MIGRATION_0005` constant at
  `:20-22` (pointing at `0005_add_reassure_tables.sql`) and the matching
  `executescript` call at `:184-186`. Confirm this fails (file not found /
  `test_schema_sql_and_migrations_are_fully_equivalent` mismatch). Do NOT add a
  `MIGRATION_0004` entry — `0004` is data-only and correctly absent from this test.
- [x] 1.3 GREEN — Create `src/perf/db/migrations/0005_add_reassure_tables.sql`: 4 tables
  (`reassure_import`, `reassure_entry`, `reassure_duration_sample`,
  `reassure_count_sample`, each sample table `UNIQUE (entry_id, idx)`) + 3 indexes
  (`idx_reassure_entry_name`, `idx_reassure_entry_import`, `idx_reassure_import_time`)
  exactly per `design.md`'s DDL section. No pragmas, no `user_version` bump (the runner
  owns both).
- [x] 1.4 GREEN — Mirror the DDL verbatim into `src/perf/db/schema.sql` under a new
  `REASSURE` banner section, matching the migration byte-for-byte for the equivalence
  test.
- [x] 1.5 Verify slice: `./.venv/bin/pytest -q tests/integration/test_schema.py
  tests/integration/test_store_migrations.py` green; a fresh store opens at
  `user_version == 5`.
- [x] 1.6 Verify gates: `./.venv/bin/ruff check .`, `./.venv/bin/ruff format --check .`,
  `./.venv/bin/mypy src/perf`, `./.venv/bin/pytest -q --cov=perf` (>= 93%).

---

## PR2 — Domain Types, Parser, Registry

**Branch**: `reassure-ingest/pr2-parser` · **Base**: `reassure-ingest/pr1-migration`
(stacked-to-main: retarget to `main` once PR1 merges) · **Est. lines**: ~300

**Independently reviewable**: pure domain + one adapter with no store/CLI consumer yet;
the whole existing suite stays green throughout. **Independently revertable**: delete
`adapters/reassure_jsonl.py` and revert the `domain/model.py`/`domain/ports.py`/
`adapters/registry.py` additions — nothing else imports them.

- [x] 2.1 RED — `tests/unit/test_reassure_jsonl.py`
  [**load-bearing non-alignment test**]: a fixture entry with exactly 8 `counts` and 6
  `durations` parses into two sequences of their own true lengths — 6 durations, 8
  counts — each with contiguous `idx` from 0 within its own series, no padding, no
  truncation, no `None` filler, and the two are never zipped.
- [x] 2.2 RED — `tests/unit/test_reassure_jsonl.py`: `durations: []` with non-empty
  `counts` parses to a valid entry (not skipped) with zero duration values.
- [x] 2.3 RED — `tests/unit/test_reassure_jsonl.py`: header absent, header present with a
  field subset (e.g. only `branch`), `type` absent defaults to `'render'`.
- [x] 2.4 RED — `tests/unit/test_reassure_jsonl.py`: malformed-line tolerance — invalid
  JSON, missing/non-string `name`, missing/non-array `durations` or `counts`, unknown
  `type`, oversized line, `NaN`/`Infinity` values — each skipped with a `REASON_*` code,
  never fatal.
- [x] 2.5 RED — `tests/unit/test_reassure_jsonl.py`: sha256 computed over the exact raw
  bytes (before decode); a missing or unreadable path raises `ReassureParseError`.
- [x] 2.6 RED — `tests/unit/test_reassure_jsonl.py`: `outlierDurations` absent -> `None`
  on the entry; present-and-empty -> `"[]"` (absent vs. empty distinction preserved).
- [x] 2.7 Create `tests/fixtures/reassure_sample.perf`: real sample containing an entry
  with `len(durations) < len(counts)`, one with `durations: []`, one with
  `outlierDurations` absent, plus the malformed-line cases from 2.4.
- [x] 2.8 GREEN — `src/perf/domain/model.py`: add `ReassureHeader`, `ReassureEntry`,
  `ReassureParseResult` frozen dataclasses per `design.md`, with the non-alignment
  invariant stated in `ReassureEntry`'s docstring.
- [x] 2.9 GREEN — `src/perf/domain/ports.py`: add the `ReassureParser` Protocol
  (`parse(self, path: str) -> ReassureParseResult`).
- [x] 2.10 GREEN — `src/perf/adapters/reassure_jsonl.py`: `ReassureParseError` +
  `ReassureJsonlParser` — read bytes, sha256, decode, per-line header/entry detection,
  `json.loads`-only parsing with the `REASON_*` vocabulary, `_is_finite_number` guard,
  `json.dumps` only when `warmupDurations`/`outlierDurations` keys are present.
- [x] 2.11 GREEN — `src/perf/adapters/registry.py`: add `build_reassure_parser` factory.
- [x] 2.12 Verify slice: `./.venv/bin/pytest -q tests/unit/test_reassure_jsonl.py`
  green; `tests/unit/test_domain_boundary.py` and
  `tests/unit/test_application_boundary.py` still pass (no adapter import in
  `domain/`).
- [x] 2.13 Verify gates: `./.venv/bin/ruff check .`, `./.venv/bin/ruff format --check .`,
  `./.venv/bin/mypy src/perf`, `./.venv/bin/pytest -q --cov=perf` (>= 93%).

---

## PR3 — Store Persistence (One Transaction)

**Branch**: `reassure-ingest/pr3-store` · **Base**: `reassure-ingest/pr2-parser`
(stacked-to-main: retarget to `main` once PR2 merges) · **Est. lines**: ~240

**Independently reviewable**: one transaction method against tables that already exist
on `main` (landed in PR1), tested end-to-end against a real SQLite file.
**Independently revertable**: drop the method and its 4 helpers — the tables stay
unused, exactly the proposal's rollback plan.

- [x] 3.1 RED — `tests/integration/test_store_reassure.py`
  [**load-bearing non-alignment-at-rest test**]: an 8-count/6-duration entry persists
  exactly 6 `reassure_duration_sample` rows and 8 `reassure_count_sample` rows, each
  with contiguous `idx` from 0 within its own table, and no row in either table asserts
  a cross-series pair.
- [x] 3.2 RED — `tests/integration/test_store_reassure.py`: `runs` is persisted verbatim
  even when it disagrees with the actual `counts` row count (e.g. `runs: 10` with only
  3 count rows) — the mismatch is recorded, never repaired, never causes a skip.
- [x] 3.3 RED — `tests/integration/test_store_reassure.py`: a `durations: []` entry
  persists with zero duration-sample rows (not skipped); `warmup_durations`/
  `outlier_durations` persist as SQL `NULL` when the JSON key was absent and `'[]'`
  when present-but-empty (both states asserted).
- [x] 3.4 RED — `tests/integration/test_store_reassure.py`: importing a byte-identical
  file a second time returns `None` and inserts zero rows across all four tables.
- [x] 3.5 RED — `tests/integration/test_store_reassure.py`: a forced mid-transaction
  failure leaves 0 rows in `reassure_import`, `reassure_entry`,
  `reassure_duration_sample`, and `reassure_count_sample` — full rollback. Corrected
  wording (was "a forced mid-transaction failure (patched helper)"): what actually
  shipped forces a REAL `sqlite3.IntegrityError` by giving the second entry
  `name=None` (violates `name TEXT NOT NULL`), not a monkeypatched
  `_insert_reassure_entry`. Monkeypatching the store's own insert helper would test a
  fake instead of the real wiring (`python-testing` rule 3: "never monkeypatch the
  thing under test") and would only prove that a hand-thrown exception rolls back —
  never that a genuine database error does.
- [x] 3.6 GREEN — `src/perf/domain/ports.py`: add
  `Store.save_reassure_import(result: ReassureParseResult, source_path: str) -> int |
  None` to the `Store` Protocol.
- [x] 3.7 GREEN — `src/perf/adapters/store_sqlite.py`: implement
  `save_reassure_import` — literal `BEGIN`, insert import row with
  `ON CONFLICT(content_hash) DO NOTHING`, `rowcount == 0` -> `COMMIT; return None`;
  otherwise insert entries then two INDEPENDENT loops over `durations`/`counts` (never
  zipped) into their own sample tables, `COMMIT`, return `import_id`; `except Exception:
  ROLLBACK; raise`. Add the 4 private helpers (`_insert_reassure_import`,
  `_insert_reassure_entry`, `_insert_reassure_duration_samples`,
  `_insert_reassure_count_samples`); every value bound with `?`.
- [x] 3.8 Verify slice: `./.venv/bin/pytest -q tests/integration/test_store_reassure.py`
  green.
- [x] 3.9 Verify gates: `./.venv/bin/ruff check .`, `./.venv/bin/ruff format --check .`,
  `./.venv/bin/mypy src/perf`, `./.venv/bin/pytest -q --cov=perf` (>= 93%).

---

## PR4a — Reassure `kind` Migration + Test Debt (sub-slice of PR4)

**Branch**: `reassure-ingest/pr4a-kind-and-test-debt` · **Base**:
`reassure-ingest/pr3-store` (stacked-to-main: retarget to `main` once PR3 merges) ·
**Est. lines**: ~230 (measured; see apply-progress)

Added by the coordinator after real reassure `.perf` output was analysed
(`.reassure/baseline.perf`/`current.perf` from a real client project): a
`baseline.perf` and a `current.perf` can carry the SAME `commitHash`, so
commit-keyed history cannot distinguish them, and the committed fixture's
entry-name shape (`" > "`-delimited) and per-entry key set (missing `issues`)
were verified FABRICATED against the real files. This sub-slice is scoped
narrowly: it adds the `kind` column (unused by any write path yet — that is
PR4b), fixes the pinned migration-count test debt the new migration causes,
reshapes the fixture to match verified reality, and closes a non-blocking
PR3 review SUGGESTION (an untested zero-entry `save_reassure_import` path).
`config.reassure_path`, the CLI command, and the `reassure_import_v1`
contract remain PR4b, unstarted here.

**Independently reviewable**: one additive column (unused), test-debt fixes forced
by that column, and a fixture/test cleanup — nothing here touches the CLI surface.
**Independently revertable**: delete `0006_add_reassure_import_kind.sql`, revert its
`schema.sql` mirror and the pinned test edits; the fixture/test renames are cosmetic
and revert independently of the column.

- [x] 4a.1 GREEN — `src/perf/db/migrations/0006_add_reassure_import_kind.sql`:
  `ALTER TABLE reassure_import ADD COLUMN kind TEXT NOT NULL DEFAULT 'unknown'` — no
  `CHECK` constraint (matches `run.mode`/`reassure_entry.entry_type` house style);
  DDL comment records the observed same-commit-hash baseline/current evidence and
  states that "first measurement for this name" is DERIVED at read time, never
  stored (it would go stale on out-of-order imports — the observed pair is proof).
  Mirrored into `src/perf/db/schema.sql` under the `REASSURE` banner.
- [x] 4a.2 RED — confirmed `tests/integration/test_store_migrations.py`'s six
  `== 5` assertions fail for the right reason once 0006 exists on disk (the
  cascading `_migrate` picks it up automatically): `assert 6 == 5`, not an
  import/typo error.
- [x] 4a.3 GREEN — `tests/integration/test_store_migrations.py`: the six `== 5` ->
  `== 6` sites (lines 30, 87, 140, 176, 230, 248) plus their inline chain-enumerating
  comments (lines 28-29, 86, 139, 247) reworded to name `0006`.
  `tests/integration/test_schema.py`: added `MIGRATION_0006` constant and its
  `executescript` line in `test_schema_sql_and_migrations_are_fully_equivalent`
  (`0004` correctly stays absent — data-only).
- [x] 4a.4 GREEN — `tests/fixtures/reassure_sample.perf` reshaped to the VERIFIED
  real shape: no `" > "` (or any) delimiter in entry names (invented components,
  e.g. `"WidgetPanel Performance Tests WidgetPanel renders correctly"`), every good
  entry carries the full real key set including `issues.initialUpdateCount` /
  `issues.redundantUpdates` (one entry non-zero), the non-alignment guard entry and
  the `durations: []` entry are both kept, `outlierDurations: []` present-empty is
  kept on one good entry and populated on the non-aligned one, the malformed-line
  skip coverage is unchanged, and the file carries NO trailing newline. Every
  by-name reference in `tests/integration/test_reassure_jsonl.py` and
  `tests/integration/test_store_reassure.py` updated to match.
- [x] 4a.5 GREEN — `tests/integration/test_store_reassure.py`: added
  `test_zero_entries_still_commits_one_import_row_with_no_entry_or_sample_rows`,
  closing the PR3 review SUGGESTION (lineage `review-1fc710595e9babbb`): a
  zero-`entries` `ReassureParseResult` still commits exactly one `reassure_import`
  row with a real (non-`None`) `import_id` and zero rows in the three
  entry/sample tables. Confirmed GREEN against the already-shipped PR3
  implementation (the per-entry loop already handles zero iterations correctly —
  this is a pinning regression guard, not a bugfix).
- [x] 4a.6 Corrected task 3.5's wording above: the shipped mid-transaction-failure
  test uses a REAL `sqlite3.IntegrityError` (`name=None` entry), never a
  monkeypatched insert helper.
- [x] 4a.7 Verify slice: `./.venv/bin/pytest -q tests/integration/test_schema.py
  tests/integration/test_store_migrations.py tests/integration/test_reassure_jsonl.py
  tests/integration/test_store_reassure.py` green; fresh DB reaches
  `user_version == 6`.
- [x] 4a.8 Verify gates: `./.venv/bin/ruff check .`, `./.venv/bin/ruff format --check
  .`, `./.venv/bin/mypy src/perf`, `./.venv/bin/pytest -q --cov=perf` (>= 93%).

---

## PR4b — CLI Command, Config, `--json` Contract

**Branch**: `reassure-ingest/pr4-cli` · **Base**: `reassure-ingest/pr4a-kind-and-test-debt`
(stacked-to-main: retarget to `main` once PR4a merges) · **Est. lines**: ~280

**Note on the contract shape**: `design.md`'s "Contract" section describes a NESTED
payload (`source_path`, `import_id`, `imported`, `header{...}`, `summary{...}`,
`skipped[]`, `diagnostic`) that predates the final decision. The authoritative shape is
`spec.md`'s flat 8-key contract, confirmed as final in Engram decision #280
(`sdd/reassure-ingest/state`): `schema_version`, `path`, `content_hash`,
`already_imported`, `entries_imported`, `entries_skipped`,
`duration_samples_imported`, `count_samples_imported` — no `samples_imported`, no
`zero_entries`. Task 4.2 builds THIS shape, not `design.md`'s stale nested one.

**Independently reviewable**: the user-facing surface over two ports that already
exist and are already tested (PR2 + PR3). **Independently revertable**: unregister the
command in `main.py`; the ports remain callable but unreached.

- [ ] 4.1 RED — `tests/contract/test_reassure_import_v1_contract.py`: pins the exact 8
  top-level keys (see note above), no more, no fewer; asserts no `samples_imported` and
  no `zero_entries` key anywhere in the payload.
- [ ] 4.2 GREEN — `src/perf/contracts/reassure_import_v1.py`: `SCHEMA_VERSION = 1`; pure
  `build_reassure_import_payload(**kwargs)` builder emitting exactly the 8 keys above.
- [ ] 4.3 GREEN — `src/perf/config/loader.py`: add `reassure_path: str =
  ".reassure/current.perf"` to `PerfConfig` — an input path, NOT run through
  `_under_base` (matches `flows[].maestro_path`).
- [ ] 4.4 RED — `tests/integration/test_cli_reassure_import.py`: missing/unreadable path
  exits `2`, no `--json` payload emitted.
- [ ] 4.5 RED — `tests/integration/test_cli_reassure_import.py`: successful import
  against `tests/fixtures/reassure_sample.perf` exits `0` with correct
  `entries_imported`/`duration_samples_imported`/`count_samples_imported`; re-importing
  the identical file exits `0` with `already_imported: true` and all `*_imported`
  fields `0`.
- [ ] 4.6 RED — `tests/integration/test_cli_reassure_import.py`: a readable file where
  every line is malformed exits `0` with `entries_imported: 0`,
  `already_imported: false`, and a stderr warning.
- [ ] 4.7 RED — `tests/integration/test_cli_reassure_import.py`: mixed-quality fixture —
  skipped lines each warn on stderr, good lines imported, exit `0`; stdout under
  `--json` is byte-pure (exactly the payload, no warnings).
- [ ] 4.8 RED — `tests/integration/test_cli_reassure_import.py`: a forced
  store/transaction failure exits `3`. Confirm exit `1` is never produced anywhere in
  this suite.
- [ ] 4.9 GREEN — `src/perf/cli/commands/reassure_import.py`: flat command;
  `resolved = path or config.reassure_path`; `build_reassure_parser().parse(resolved)`;
  `ReassureParseError` -> `emit_error(..., hint=...)` exit `2`; else
  `build_store(...).save_reassure_import(result, resolved)`; `emit_warning` per skipped
  line (line number + fixed reason token only); `build_reassure_import_payload(...)` ->
  `render_json`/pretty; any other exception -> `emit_error` exit `3`.
- [ ] 4.10 GREEN — `src/perf/cli/main.py`: register
  `app.command(name="reassure-import", context_settings={"help_option_names":
  ["--help", "-h"]})(reassure_import_command)` (flat, not `add_typer`).
- [ ] 4.11 Verify slice: `./.venv/bin/pytest -q
  tests/contract/test_reassure_import_v1_contract.py
  tests/integration/test_cli_reassure_import.py` green.
- [ ] 4.12 Verify gates + full chain: `./.venv/bin/ruff check .`,
  `./.venv/bin/ruff format --check .`, `./.venv/bin/mypy src/perf`,
  `./.venv/bin/pytest -q --cov=perf` (>= 93%) with all four slices merged.
- [ ] 4.13 Runtime harness: `perfvibe reassure-import
  tests/fixtures/reassure_sample.perf --json` — confirm the 8-key payload and exit `0`.
