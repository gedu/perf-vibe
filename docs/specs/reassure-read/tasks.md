# Tasks: Read, Chart and Compare Persisted reassure Data (`reassure-read`)

> Size note: like `design.md`'s own 800-word-budget exception, this artifact exceeds the
> `sdd-tasks` skill's 530-word guidance. Ten slices, each carrying RED-before-GREEN tests,
> a contract, a renderer, a golden and per-slice docs, do not compress into 530 words without
> becoming a summary — the same "use the project's ACTUAL patterns" argument `design.md:3-6`
> makes, mirrored against the house precedent `docs/specs/reassure-ingest/tasks.md`.

## D5 GAP — RESOLVED by reordering, not by duplicating a method

**Verified**: `reassure_series`'s read model already carries the value D5 needs. `design.md:126`
declares `ReassureSeriesPoint.entry: ReassureEntryRow` — a full nested row, not a summary — and
`design.md:113` declares `ReassureEntryRow.initial_update_count: int | None = None` as one of
that row's fields. So every point `reassure_series` returns already exposes
`point.entry.initial_update_count`; nothing new needs to be added to any read model.

**The fix is the reorder, and reordering closes the gap completely.** Moving the series-method
slice (now **2a**) ahead of `show` (now **2b**) means `show` becomes `reassure_series`'s first
consumer instead of needing a duplicate or a new field. Because `reassure_series(name, limit)`
only ever returns points for imports that CONTAIN `name` (proven by slice 2a's own coverage-gap
test — an import missing `name` contributes nothing to the series), asking for the last two
points directly yields "the latest point" and "the immediately preceding import that ALSO
contains `name`" — the exact pair D5 needs, satisfied by the query the design already specified
for a different purpose. **2b.8** below states the exact call shape. No new store method, no new
field, no extra query beyond what 2a already ships.

## Split PR1b — user-confirmed, at its natural seam

At ~950 lines the original combined PR1b was 2-3x the review budget. Split:
- **1b**: sub-app scaffolding + deprecated alias + `typer` pin + `list` + its
  contract/renderer/golden/CLI tests + `README.md` + `docs/commands.md` (sub-app overview,
  `import`, `list`).
- **1c**: `entries` + its contract/renderer/golden/CLI tests + `docs/commands.md` (`entries`
  addendum) + `docs/configuring-flows.md` (`reassure_path`) + `AGENTS.md`/`CLAUDE.md`
  (`entries` addendum).

## Renumbered, monotonic execution order

`0 → 1a → 1b → 1c → 2a → 2b → 3 → 4a → 4b → 5`, stacked to main.

| New | Was | Content |
|---|---|---|
| 0 | 0 | unchanged |
| 1a | 1a | store read methods + read models (no name filter) |
| 1b | 1b (part) | scaffolding + alias + typer pin + `list` + docs |
| 1c | 1b (part) | `entries` + docs |
| 2a | 3a | `reassure_series` + per-series reduction |
| 2b | 2 | `reassure show` (D5/D8) + the `name` filter on `reassure_entries` |
| 3 | 3b | `reassure history` |
| 4a | 4a | D7 floor + `domain/reassure_compare.py` + the two `median_by_commit` guard tests |
| 4b | 4b | `reassure compare` |
| 5 | 5 | `reassure run` + init wizard |

**Dependency check after the move (every dependency still holds):**
- 1b/1c depend on 1a's `reassure_imports`/`reassure_entries` — 1a ships first. ✓
- **2b (`show`) now depends on 2a (`reassure_series`) for its D5 lookup — 2a ships immediately
  before it.** This is the entire point of the reorder. ✓
- 3 (`history`) depends on 2a (`reassure_series`) — 2a ships before it (previously true too,
  unaffected by the reorder). ✓
- 4a (pure `compare_series`) only needs the `ReassureSeriesPoint`/`ReassureEntryRow` model
  SHAPES (its unit tests hand-build these objects, no store call) — both models exist since
  2a/1a. ✓
- 4b (`compare` CLI) depends on 2a (`reassure_series`) + 4a (`compare_series`) — both ship
  before it. ✓
- 5 (`run`) depends only on the pre-existing `reassure import` path (PR0-adjacent, already on
  `main`) — unaffected by the reorder. ✓
- 1c has no functional dependency on 1b (the `entries` and `list` commands are independent);
  the 1b→1c base relationship is a STACKING convenience only, not a code dependency.

---

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | ~3,900-4,100 total across 10 slices (signal-based — file/test/doc count per slice, not an exact diff count) |
| 400-line budget risk | **High** — several individual slices estimated above 400 |
| Chained PRs recommended | Yes (user-confirmed shape, now 10 slices after the split) |
| Suggested split | PR0 → PR1a → PR1b → PR1c → PR2a → PR2b → PR3 → PR4a → PR4b → PR5 |
| Delivery strategy | ask-on-risk |
| Chain strategy | stacked-to-main |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

**Per-slice estimate** (signal-based: file/test/doc count, calibrated against the shipped
`reassure-ingest` precedent — the **247-line calibration anchor**: `reassure_import_v1`'s ONE
flat contract module plus its ONE contract test alone shipped at 105 + 142 = 247 lines, before
any CLI command code existed. That number is the most load-bearing one in this forecast: it is
what explains the overage as STRUCTURAL to this repo's docstring-dense convention — every file
touched during exploration carries multi-paragraph rationale docstrings — rather than scope
creep, and it is the reference a reviewer will need when judging the real diffs at apply time):

| Slice | New/modified files (signal) | Est. lines | Note |
|---|---|---|---|
| **0** | 3 modified + 1 spec delta + 3 test files | ~280 | Medium |
| **1a** | 4 modified + 1 new test file + a novel query-count trace-hook pattern | ~360 | Medium-High |
| **1b** | ~6 new (sub-app, list contract, list renderer, list contract test, list golden, deprecation+list CLI tests) + 5 modified (main.py, pyproject.toml, README.md, commands.md) | ~520 | High |
| **1c** | ~5 new (entries contract, entries renderer, entries contract test, entries golden, entries CLI test) + 3 modified (commands.md, configuring-flows.md, AGENTS.md/CLAUDE.md) | ~430 | High |
| **2a** | 4 modified (model, ports, store, fakes) + 1 test file | ~300 | Medium |
| **2b** | 5 new + 3 modified (store name-filter, docs) | ~430 | **Deferred — see below** |
| **3** | 5 new + 2 modified (docs) | ~450 | **Deferred — see below** |
| **4a** | 2 new (pure domain module + unit test) + 1 modified (config) | ~320 | Medium |
| **4b** | 5 new + 2 modified (docs) | ~450 | **Deferred — see below** |
| **5** | ~3 new + 5 modified | ~420 | Medium-High |

**Budget decision for 2b, 3, 4b: deferred to apply time against the REAL diff, not this
estimate.** Reason: the total estimate for this change moved from **1640-2130 lines** at
`sdd-design` to **3900-4100 lines** here at `sdd-tasks` — roughly a 2x swing between two
agents' forecasts of the SAME design. A third estimate produced by this same forecasting
method is not more authoritative than either of the first two; it cannot be what grants or
denies a `size:exception`. This forecast's job is to flag WHICH slices need that real-diff
check before merging (2b, 3, 4b — each estimated at or above 400 by file-count signal), not to
pre-decide the outcome. 1a/1b/1c/5 remain informational estimates under normal `ask-on-risk`
gating; 4a is comfortably under budget by design (`design.md:441-443`, "4a loses its store
method... it gets smaller, which helps the riskiest slice"'s sibling).

### Suggested Work Units

| Unit | Goal | PR | Focused test command | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| 0 | D4 dedup + widened diagnostic + `reassure_import_v1` v2→v3 | PR0 | `pytest -q tests/integration/test_reassure_jsonl.py tests/contract/test_reassure_import_v1_contract.py` | `perfvibe reassure-import <dup-fixture> --json` | Revert the 3 modified files + spec delta |
| 1a | 2 `Store` read methods + read models | PR1a | `pytest -q tests/integration/test_store_reassure_read.py` | N/A — no CLI surface yet | Revert `model.py`/`ports.py`/`store_sqlite.py`/`fakes.py` additions |
| 1b | Sub-app + alias + typer pin + `list` + README/commands.md | PR1b | `pytest -q tests/contract/test_reassure_list_v1_contract.py tests/integration/test_cli_reassure_list.py tests/integration/test_cli_reassure_deprecation.py` | `perfvibe reassure list --json` | Unregister `reassure_app` in `main.py`; alias reverts to non-deprecated |
| 1c | `entries` + configuring-flows.md/AGENTS.md/CLAUDE.md | PR1c | `pytest -q tests/contract/test_reassure_entries_v1_contract.py tests/integration/test_cli_reassure_entries.py` | `perfvibe reassure entries <id> --json` | Remove the `entries` registration |
| 2a | `reassure_series` store method | PR2a | `pytest -q tests/integration/test_store_reassure_read.py` (series cases) | N/A — no CLI surface yet | Revert the method + model |
| 2b | `reassure show` (D5/D8/A14) + name filter | PR2b | `pytest -q tests/contract/test_reassure_show_v1_contract.py tests/integration/test_cli_reassure_show.py` | `perfvibe reassure show <name> --json` | Remove the `show` registration |
| 3 | `reassure history` + chart renderer | PR3 | `pytest -q tests/contract/test_reassure_history_v1_contract.py tests/integration/test_cli_reassure_history.py` | `perfvibe reassure history <name> --json` | Remove the `history` registration |
| 4a | `domain/reassure_compare.py` | PR4a | `pytest -q tests/unit/test_reassure_compare.py` | N/A — pure domain | Delete the module |
| 4b | `reassure compare` (D3) | PR4b | `pytest -q tests/contract/test_reassure_compare_v1_contract.py tests/integration/test_cli_reassure_compare.py` | `perfvibe reassure compare <name> --json` | Remove the `compare` registration |
| 5 | `reassure run` + init wizard | PR5 | `pytest -q tests/unit/test_reassure_run.py tests/integration/test_cli_reassure_run.py` | `perfvibe reassure run --json` | Remove the `run` registration + wizard block |

---

## PR0 — D4 Duplicate Detection + Contract Bump

**Branch**: `reassure-read/slice0-duplicate-detection` · **Base**: `main` · **Est. lines**: ~280

- [x] 0.1 RED — `tests/integration/test_reassure_jsonl.py`: `name: "X"` on 3 well-formed lines
  (different values) → zero `X` entries persisted-candidate, three
  `(line_number, "duplicate_name")` pairs in `skipped`; other names' order unchanged.
- [x] 0.2 RED — same file: one duplicated name + four unique well-formed entries → all four
  survive, only the duplicate is dropped.
- [x] 0.3 RED — same file **[unmissable]**: assert `_build_diagnostic`'s output distinguishes
  malformed-line count from duplicate-name-drop count via two separate clauses — MUST fail
  against the current single-clause wording (`adapters/reassure_jsonl.py:300-301`).
- [x] 0.4 GREEN — `src/perf/adapters/reassure_jsonl.py`: add `REASON_DUPLICATE_NAME =
  "duplicate_name"` beside `REASON_*` (`:45-50`); change the loop accumulator at `:128` to
  `pending: list[tuple[int, ReassureEntry]]`; post-loop grouping by `entry.name` drops ALL
  copies of any name occurring ≥2 times, appending `(line_number, REASON_DUPLICATE_NAME)` per
  dropped copy; survivors keep first-seen (dict insertion) order.
- [x] 0.5 GREEN — same file: widen `_build_diagnostic` (`:293-301`) to `"{m} line(s) skipped as
  malformed; {d} dropped as duplicate name(s); {k} entries imported."`
- [x] 0.6 RED — `tests/contract/test_reassure_import_v1_contract.py`: widen
  `_REQUIRED_KEYS_AND_TYPES` to ELEVEN keys (`entries_dropped_duplicate_name: int`); rename/fix
  `test_exact_ten_keys_...` → asserts `len(payload) == 11`; assert `SCHEMA_VERSION == 3`.
- [x] 0.7 GREEN — `src/perf/contracts/reassure_import_v1.py`: bump `SCHEMA_VERSION` to `3`, add
  `entries_dropped_duplicate_name: int` to the builder's signature and returned dict.
- [x] 0.8 GREEN — `src/perf/cli/commands/reassure_import.py`: compute
  `entries_dropped_duplicate_name` by counting `skipped` reasons `== REASON_DUPLICATE_NAME`,
  pass it to `build_reassure_import_payload`.
- [x] 0.9 RED — `tests/golden/test_reassure_import_pretty_golden.py`: add a fixture with
  `entries_dropped_duplicate_name > 0`, expect a new counter row.
- [x] 0.10 GREEN — `src/perf/cli/output/reassure_import_pretty.py`: add a fifth row to
  `_counter_rows` (`:55-71`) for `entries_dropped_duplicate_name`, painted non-DIM when
  non-zero; regenerate the golden.
- [x] 0.11 GREEN — `openspec/specs/reassure-ingest.md`: apply the MODIFIED "reassure_import_v1
  --json Contract" (11 keys, `SCHEMA_VERSION 3`) and ADDED "Duplicate Entry-Name Detection And
  Dropping" requirements verbatim from `docs/specs/reassure-read/spec.md`'s delta section.
- [x] 0.12 GREEN — `openspec/specs/reassure-ingest.md:16-17`: correct "enters through
  `Analyzer`" — `Analyzer.compare_latest` is flow/device/mode-keyed; this capability enters
  through `Store` read methods + pure `domain/` functions instead.
- [x] 0.13 Verify slice: `./.venv/bin/pytest -q tests/integration/test_reassure_jsonl.py
  tests/contract/test_reassure_import_v1_contract.py
  tests/golden/test_reassure_import_pretty_golden.py`.
- [x] 0.14 Verify gates: `./.venv/bin/ruff check .`, `./.venv/bin/ruff format --check .`,
  `./.venv/bin/mypy src/perf`, `./.venv/bin/pytest -q --cov=perf` (>= 93%).

---

## PR1a — Read-Path Store Methods (`list`/`entries` backing)

**Branch**: `reassure-read/slice1a-read-store` · **Base**: `reassure-read/slice0-duplicate-detection`
(retarget to `main` once PR0 merges) · **Est. lines**: ~360

- [x] 1a.1 RED — `tests/integration/test_store_reassure_read.py` [new]: `reassure_imports(limit)`
  orders `ORDER BY COALESCE(created_date, imported_at) DESC`; a header-less import sorts by
  `imported_at` and its `ordering_key` reports `'imported_at'`.
- [x] 1a.2 RED — same file: `entry_count` per import matches the real row count via one batched
  `IN (...)` query; a counting `sqlite3` trace hook asserts exactly **2** queries execute.
- [x] 1a.3 RED — same file: `reassure_entries(import_id)` returns every entry for that import
  with independently-reduced `duration`/`count` `HistoryMetric`s — an 8-count/6-duration entry
  yields `duration.n == 6`, `count.n == 8` from exactly **3** total queries; a trace-hook
  assertion confirms no executed SQL text joins the two sample tables.
- [x] 1a.4 RED — same file: an entry with zero rows in one sample table yields `None` on that
  series, never a zero-valued `HistoryMetric`.
- [x] 1a.5 RED — same file: an `import_id` with zero entries returns an empty sequence, not an
  error.
- [x] 1a.6 GREEN — `src/perf/domain/model.py`: add frozen `ReassureImportRow` and
  `ReassureEntryRow` per `design.md`'s "Read Models" section (`commit_hash`/`branch` labelled
  LABEL-ONLY; `kind` deliberately absent, A5). `ReassureEntryRow.initial_update_count: int |
  None` is part of this dataclass (`design.md:113`) — it is what makes slice 2a/2b's D5 fix
  possible later; no later slice needs to add it.
- [x] 1a.7 GREEN — `src/perf/domain/ports.py`: add `reassure_imports(self, limit: int) ->
  Sequence[ReassureImportRow]` and `reassure_entries(self, import_id: int) ->
  Sequence[ReassureEntryRow]` to `Store` (no `name` filter yet — slice 2b owns that, per the
  Slice Map's explicit carve-out, `design.md:445-448`).
- [x] 1a.8 GREEN — `src/perf/adapters/store_sqlite.py`: implement `reassure_imports` (window
  query + one batched `COUNT(*) ... GROUP BY import_id ... WHERE import_id IN (?,?,…)`, the
  `IN (…)` text built via `",".join("?" for …)`, never a bound value) and `reassure_entries`
  (entry window + two independent batched reduction queries, mirroring
  `_history_system_summaries:822-855`, reusing `statistics.median`/`percentile` and
  `_HISTORY_P90`).
- [x] 1a.9 GREEN — `tests/fakes.py`: add `reassure_imports`/`reassure_entries` to `FakeStore`.
- [x] 1a.10 Verify slice: `./.venv/bin/pytest -q tests/integration/test_store_reassure_read.py`.
- [x] 1a.11 Verify gates.

---

## PR1b — Sub-App, Deprecated Alias, `list`, README/commands.md

**Branch**: `reassure-read/slice1b-subapp-list` · **Base**:
`reassure-read/slice1a-read-store` (retarget to `main` once PR1a merges) · **Est. lines**: ~520
(High — see forecast)

- [x] 1b.0 Research — read `typer`'s and Click's changelogs for the lowest `typer` version
  whose vendored click resolves `>= 8.2` (per design's "Typer version dependency"); record the
  floor version and a one-line rationale.
- [x] 1b.1 GREEN — `pyproject.toml:12`: tighten `typer>=0.12` to a floor-and-ceiling pin on the
  researched minor line; comment cites the ruff-pin incident (`pyproject.toml:20-26`) as
  precedent.
- [x] 1b.2 GREEN — `src/perf/cli/commands/reassure.py` [new]: create `reassure_app`
  (`add_completion=False`, shared `context_settings`, help text) mirroring `markers.py:324-328`;
  register the EXISTING `reassure_import` function object as `reassure_app.command(name=
  "import", ...)` — no wrapper, no copy.
- [x] 1b.3 RED — `tests/integration/test_cli_reassure_deprecation.py` [new]: `reassure-import`
  and `reassure import` on the same file produce byte-identical `--json` payloads/exit codes;
  `perfvibe --help` omits `reassure-import`; the flat form's deprecation notice lands on
  stderr only, stdout stays byte-pure under `--json`; the sub-app form prints no notice.
- [x] 1b.4 GREEN — `src/perf/cli/main.py`: mark the flat `app.command(name="reassure-import",
  ...)` registration `hidden=True, deprecated="use \`perfvibe reassure import\` instead"`
  (native Click echo, no shim — A10); add `app.add_typer(reassure_app, name="reassure")`.
- [x] 1b.5 RED — `tests/contract/test_reassure_list_v1_contract.py` [new]: exact key set for
  `reassure_list_v1` (JSON round-trip, version-bump guard, mirrors
  `test_reassure_import_v1_contract.py`'s discipline).
- [x] 1b.6 GREEN — `src/perf/contracts/reassure_list_v1.py` [new]: `SCHEMA_VERSION = 1`; pure
  `build_reassure_list_payload(**kwargs)`.
- [x] 1b.7 RED — `tests/integration/test_cli_reassure_list.py` [new]: default `--limit 50`; D2
  ordering; empty roster still exits `0`.
- [x] 1b.8 GREEN — `src/perf/cli/commands/reassure.py`: add `reassure_list` —
  `store.reassure_imports(limit)` → `build_reassure_list_payload` → render; register as
  `"list"`.
- [x] 1b.9 RED — `tests/golden/test_reassure_list_pretty_golden.py` [new]: box+table golden;
  `ordering_key` shown dim when it fell back to `imported_at`.
- [x] 1b.10 GREEN — `src/perf/cli/output/reassure_list_pretty.py` [new]: box + `table_line`
  roster reusing `header_line`/`ColumnSpec`/`style`/`DIM`; regenerate golden.
- [x] 1b.11 GREEN — `README.md`: collapse `reassure` into exactly ONE row in the stale
  "six commands" table (mirroring `markers`'s one-row treatment; `reassure-import` is already
  absent).
- [x] 1b.12 GREEN — `docs/commands.md`: document the `reassure` sub-app overview plus
  `import` and `list` — flags, exit codes, `--json` payload shape (`entries`/`show`/`history`/
  `compare`/`run` are added by their own slices, per the "docs ship with the introducing
  slice" rule).
- [x] 1b.13 Verify slice: `./.venv/bin/pytest -q
  tests/contract/test_reassure_list_v1_contract.py
  tests/golden/test_reassure_list_pretty_golden.py
  tests/integration/test_cli_reassure_{list,deprecation}.py`.
- [x] 1b.14 Verify gates.

---

## PR1c — `entries` + configuring-flows.md/AGENTS.md/CLAUDE.md

**Branch**: `reassure-read/slice1c-entries` · **Base**: `reassure-read/slice1b-subapp-list`
(retarget to `main` once PR1b merges) · **Est. lines**: ~430 (High — see forecast)

- [x] 1c.1 RED — `tests/contract/test_reassure_entries_v1_contract.py` [new]: exact key set —
  per-entry `name`, `entry_type`, `runs`, independent duration/count `HistoryMetric`s.
- [x] 1c.2 GREEN — `src/perf/contracts/reassure_entries_v1.py` [new]: `SCHEMA_VERSION = 1`;
  pure `build_reassure_entries_payload(**kwargs)`.
- [x] 1c.3 RED — `tests/integration/test_cli_reassure_entries.py` [new]: valid `import-id`
  returns every entry with independent `n`s (5-count/8-duration case); unknown `import-id`
  exits `2`, no `--json` payload emitted.
- [x] 1c.4 GREEN — `src/perf/cli/commands/reassure.py`: add `reassure_entries_command` —
  validate `import_id` exists, then `store.reassure_entries(import_id)` → payload → render;
  exit `2` on unknown id; register as `"entries"`. **Deviation, flagged not silently done**:
  neither `reassure_imports`(windowed roster — an id outside `--limit` is not "unknown") nor
  `reassure_entries`'s own emptiness (ambiguous between "unknown" and "real, zero entries",
  and the spec requires different exits for each) can validate existence unambiguously. Added
  a FOURTH `Store` method, `reassure_import_exists(import_id) -> bool` (`domain/ports.py`,
  `adapters/store_sqlite.py`, `tests/fakes.py`, 3 new RED→GREEN tests in
  `tests/integration/test_store_reassure_read.py`) — one unbounded `SELECT 1 ... LIMIT 1`.
  Not in design A2's three-method list; documented at both the Protocol and adapter.
- [x] 1c.5 RED — `tests/golden/test_reassure_entries_pretty_golden.py` [new]: duration
  p50/p90 and count p50/p90 as SEPARATE columns (I1 in the UI).
- [x] 1c.6 GREEN — `src/perf/cli/output/reassure_entries_pretty.py` [new]: box + table with
  separate duration/count column groups; regenerate golden.
- [x] 1c.7 GREEN — `docs/commands.md`: add the `entries` subcommand section.
- [x] 1c.8 GREEN — `docs/configuring-flows.md`: document `reassure_path` (currently
  undocumented anywhere).
- [x] 1c.9 GREEN — `AGENTS.md`, `CLAUDE.md`: record the agent-facing `--json`-only contract for
  `reassure entries`. **Correction, not silently done**: PR1b's own task list (1b.0-1b.14)
  never included an `AGENTS.md`/`CLAUDE.md` task — grep-verified zero "reassure" mentions in
  either file before this slice. This task's premise ("extending the import/list entries 1b
  already added") does not hold; this is the FIRST such addition, covering `import`/`list`/
  `entries` together, not an extension of a prior one.
- [x] 1c.10 Verify slice: `./.venv/bin/pytest -q
  tests/contract/test_reassure_entries_v1_contract.py
  tests/golden/test_reassure_entries_pretty_golden.py
  tests/integration/test_cli_reassure_entries.py` → 37 passed.
- [x] 1c.11 Verify gates: ruff check/format, mypy, pytest --cov all green (1204 passed, up
  from 1164; coverage 95.43%, floor 93%). Real-CLI manual exercise against a temp db also
  performed (real import / unknown id / real-zero-entries import) — found and fixed one
  cosmetic `--help` text defect (a hyphen line-wrapped mid-word in the docstring rendered as
  a literal `independently- reduced` in `--help` output).

---

## PR2a — `reassure_series` Store Method

**Branch**: `reassure-read/slice2a-series-store` · **Base**: `reassure-read/slice1c-entries`
· **Est. lines**: ~300

- [x] 2a.1 RED — `tests/integration/test_store_reassure_read.py`: `reassure_series(name,
  limit)` returns points OLDEST→NEWEST, one per import containing `name`, 3 total queries, no
  join of the two sample tables (trace-hook).
- [x] 2a.2 RED — same file **[unmissable]**: two same-commit/same-branch imports (0006's real
  case) both containing `name` stay TWO distinct points. **Strengthened beyond the task text**:
  the two imports carry different measured values, and the test asserts each point keeps its
  own value in the right order — a bare `len(points) == 2` would still pass if the two had
  collapsed into one averaged point via `statistics.median_by_commit`, which is exactly the
  failure mode this guards against.
- [x] 2a.3 RED — same file: `name` in imports A and C but absent from B yields exactly two
  points; B contributes nothing, neither A's nor C's data shifts into B's slot — proves
  `reassure_series` only ever returns points for imports that CONTAIN `name` (this is what
  slice 2b's D5 fix below relies on).
- [x] 2a.3b RED — same file, **not in the original task list, added during apply**: with more
  imports containing `name` than `limit`, the returned points are the MOST RECENT `limit`,
  ordered oldest→newest, and the oldest excess imports are absent (distinct values per import
  so a wrong window direction fails loudly). A naive `ORDER BY ... ASC LIMIT ?` would instead
  return the OLDEST `limit` points — indistinguishable from correct on a small fixture where
  the import count is under `limit`, which is why no task in 2a.1–2a.3 as originally written
  would have caught it.
- [x] 2a.3c RED — same file, **not in the original task list**: `initial_update_count`'s
  `None` (never measured) vs `0` (measured, clean) survives on the nested `entry` without
  collapsing — the fact PR2b's D5 depends on.
- [x] 2a.4 GREEN — `src/perf/domain/model.py`: add frozen `ReassureSeriesPoint` per
  `design.md:116-129` — `entry: ReassureEntryRow` (a full nested row, carrying
  `initial_update_count`), `commit_hash`/`branch` as LABEL-ONLY fields.
- [x] 2a.5 GREEN — `src/perf/domain/ports.py`: add `reassure_series(self, name: str, limit:
  int) -> Sequence[ReassureSeriesPoint]` to `Store`.
- [x] 2a.6 GREEN — `src/perf/adapters/store_sqlite.py`: implement `reassure_series` —
  name-joined import window (DESC + LIMIT, then reversed to ASC in Python — see 2a.3b; the
  task text's "ASC, opposite of `reassure_imports`" describes only the RETURN order, not the
  windowing direction) + the same two independent batched reduction queries as
  `reassure_entries` (reused `_reduce_reassure_samples`, not duplicated).
- [x] 2a.7 GREEN — `tests/fakes.py`: `FakeStore.reassure_series`.
- [x] 2a.8 Verify slice: `./.venv/bin/pytest -q tests/integration/test_store_reassure_read.py`
  → 13 passed (8 pre-existing + 5 new).
- [x] 2a.9 Verify gates: ruff check/format, mypy, `pytest --cov` all green — 1209 passed (up
  from 1204), coverage 95.42% (floor 93%). Manual store-level exercise against a temp SQLite
  db also performed (no CLI surface in this slice): `reassure_series(name, limit=2)` correctly
  returned the two most recent imports oldest-first; `limit=10` returned all three in order;
  an unknown name returned `()`.

---

## PR2b — `reassure show` (D5, D8, A14) + Name Filter

**Branch**: `reassure-read/slice2b-show` · **Base**: `reassure-read/slice2a-series-store`
· **Est. lines**: ~430 — **budget decision deferred to apply time against the real diff (see
Review Workload Forecast)**

- [x] 2b.1 RED — `tests/integration/test_store_reassure_read.py`: `reassure_entries(import_id,
  name="X")` returns exactly the one entry named `"X"`; `name` absent from that import returns
  an empty sequence.
- [x] 2b.2 GREEN — `src/perf/domain/ports.py`: widen `Store.reassure_entries` to `(self,
  import_id: int, name: str | None = None) -> Sequence[ReassureEntryRow]`.
- [x] 2b.3 GREEN — `src/perf/adapters/store_sqlite.py`: add a `WHERE name = ?` clause (bound
  value), applied only when `name is not None`.
- [x] 2b.4 GREEN — `tests/fakes.py`: `FakeStore.reassure_entries` honors the `name` filter.
- [x] 2b.5 RED — `tests/contract/test_reassure_show_v1_contract.py` [new]: exact flat key set
  incl. the three D5 keys (`initial_update_count: int|null`, `baseline_initial_update_count:
  int|null`, `initial_update_state: str`) + duration/count summaries + declared-vs-actual
  `runs`; asserts NO key matching `*_delta_pct`/`*_pct` for the update count.
- [x] 2b.6 GREEN — `src/perf/contracts/reassure_show_v1.py` [new]: `SCHEMA_VERSION = 1`; pure
  `build_reassure_show_payload(**kwargs)`.
- [x] 2b.7 RED — `tests/integration/test_cli_reassure_show.py` [new]: D8 default (latest import
  overall) vs `--import <id>` override; `name` absent from the LATEST import exits `2`, no
  walk-back (A14); `name` absent from EVERY import exits `2`; a `0 -> 1` transition renders
  `initial_update_state == "introduced"`; a `NULL -> 0` transition renders `"unknown"` (never
  `"unchanged"` or a `0 -> 0` label), asserted `is None`, never falsy; a `name` present ONLY in
  the latest import (no prior import contains it) renders `"unknown"`/"no prior diagnostic",
  never a fabricated baseline.
- [x] 2b.8 GREEN — `src/perf/cli/commands/reassure.py`: add `reassure_show`. D8 default: `store.
  reassure_imports(1)` then `store.reassure_entries(latest_id, name)` (exit `2` if empty, A14).
  **D5 fix (the resolved gap)**: call `store.reassure_series(name, limit=2)` — because
  `reassure_series` only returns points for imports containing `name` (2a.3), its last two
  points ARE "the latest" and "the immediately preceding import that also contains `name`";
  read `points[-1].entry.initial_update_count` and `points[-2].entry.initial_update_count`
  (or `points[0]` alone, with baseline `None`, when only one point exists — "no prior
  diagnostic"). For `--import <id>` selecting a NON-latest import, call `store.reassure_series
  (name, limit=<uncapped>)`, locate the point matching `id`, and take the one immediately
  before it. No new store method, no new model field — both were already shipped in 1a/2a.
  Register as `"show"`.
- [x] 2b.9 RED — `tests/golden/test_reassure_show_pretty_golden.py` [new]: box + labelled
  key/value block + ONE D5 sentence line (never a table row, never a percentage) covering all
  five D5 states from `design.md`'s exact-wording table.
- [x] 2b.10 GREEN — `src/perf/cli/output/reassure_show_pretty.py` [new]: key/value block
  (mirrors `render_reassure_import`'s shape) + the D5 sentence + a `declared runs N != stored
  n M` line on disagreement; regenerate golden.
- [x] 2b.11 GREEN — `docs/commands.md`: document `reassure show` — `--import`, exit codes,
  `--json` shape, D5 sentence forms.
- [x] 2b.12 Verify slice.
- [x] 2b.13 Verify gates.

---

## PR3 — `reassure history`

**Branch**: `reassure-read/slice3-history` · **Base**: `reassure-read/slice2b-show-cli`
(PR2b was split at apply time into `slice2b-show-logic` and `slice2b-show-cli`
— this base name is the latter's tip)
· **Est. lines**: ~450 — **budget decision deferred to apply time against the real diff**

- [x] 3.1 RED — `tests/contract/test_reassure_history_v1_contract.py` [new]: exact key set —
  array of per-import points, each with `import_id`, `ordered_at`, `ordering_key`,
  `commit_hash` (label), independent duration/count `HistoryMetric` summaries.
- [x] 3.2 GREEN — `src/perf/contracts/reassure_history_v1.py` [new]: `SCHEMA_VERSION = 1`;
  pure `build_reassure_history_payload(**kwargs)`.
- [x] 3.3 RED — `tests/integration/test_cli_reassure_history.py` [new]: coverage-gap scenario
  yields exactly 2 series points; unknown `name` exits `2`.
- [x] 3.4 GREEN — `src/perf/cli/commands/reassure.py`: add `reassure_history` —
  `store.reassure_series(name, limit=<uncapped window>)` → payload → render; unknown `name`
  (empty series) exits `2`; register as `"history"`.
- [x] 3.5 RED — `tests/golden/test_reassure_history_pretty_golden.py` [new]: TWO sections (one
  per series), each with its own `chart_lines`/`sparkline`/table; empty-series, single-point,
  zero-variance edges.
- [x] 3.6 GREEN — `src/perf/cli/output/reassure_history_pretty.py` [new]: box + two per-series
  sections copying `history_pretty._metric_section:233-254`; x-labels: short `commit_hash`,
  else the date part of `ordered_at`, else `#<import_id>`; regenerate golden.
- [x] 3.7 GREEN — `docs/baselines-and-history.md`: add a CONTRASTING section (not an append) —
  reassure's per-import (D2) baseline rule vs. the flow world's per-commit rule this doc
  otherwise describes. Also updated `docs/commands.md` (its own text at line 276-277 promised
  `history` would be documented "once it ships" — this slice ships it) with a `reassure
  history <name>` section and the exit-code table, which task 3.7 as originally scoped did
  not mention; see the apply report for the file:line evidence.
- [x] 3.8 Verify slice.
- [x] 3.9 Verify gates.

---

## PR4a — `domain/reassure_compare.py` (Pure Verdict Logic)

**Branch**: `reassure-read/slice4a-compare-domain` · **Base**: `reassure-read/slice3-history`
· **Est. lines**: ~320

- [x] 4a.1 RED — `tests/unit/test_reassure_compare.py` [extended, not new — see 4a.5 note] **
  [unmissable — I2 guard 1]**: two `ReassureSeriesPoint`s sharing `commit_hash` AND `branch`
  (0006's real baseline/current pair) stay TWO points; p90 values `[5.0, 25.0, 20.0]` chosen so
  the collapsed-by-commit median (17.5) and the true plain median (20.0) differ; asserts
  `compare_series(...)`'s duration verdict uses the TRUE median (20.0), never 17.5.
- [x] 4a.2 RED — same file **[unmissable — I2 guard 2 — CORRECTED, AST not textual]**: the
  literal `assert "median_by_commit" not in Path(reassure_compare.__file__).read_text()` as
  originally scoped was broken two ways — it would fail immediately against the module's own
  docstring (which intentionally NAMES `median_by_commit` as the bug it defends against, useful
  documentation, not a violation), and it misattributed its own precedent: `test_domain_
  boundary.py`'s guard (`:17-38`) parses real `Import`/`ImportFrom` AST nodes, it does not do a
  substring check. Implemented instead as `test_module_never_imports_or_calls_median_by_commit`
  — parses the module with `ast` and asserts `median_by_commit` is never imported and never
  called, leaving the docstring warning intact. See the apply report for the full rationale.
- [x] 4a.3 RED — same file: fewer than `MIN_BASELINE_IMPORTS` (3) baseline points → both
  verdicts report insufficient-data, never `stable`.
- [x] 4a.4 RED — same file: `count` series floor is exactly `0.0` (D7); `higher_is_better` is
  `False` on both verdicts (A6).
- [x] 4a.5 RED — **already complete before this slice started** (PR2b shipped all nine D5
  state-table tests early, ahead of this slice's own module-extension work — see the module's
  and this test file's docstrings). Verified, not duplicated.
- [x] 4a.6 GREEN — `src/perf/domain/reassure_compare.py` [extended, not new — PR2b created it
  early for `derive_update_count_change`]: `SERIES_DURATION`/`SERIES_RENDER_COUNT` constants,
  `MIN_BASELINE_IMPORTS = 3` (A7), frozen `UpdateCountChange` (no delta field, not a `Verdict`
  — A13, pre-existing from PR2b) and `ReassureComparison`, and the single public
  `compare_series(points, *, threshold_pct, floors) -> ReassureComparison | None` per
  `design.md`'s "The Verdict Function" (plain `statistics.median` over per-import p90s,
  `classify()` per series with `higher_is_better=False`, `min_n=MIN_BASELINE_IMPORTS`, a
  comment on the `baseline_commit_n` naming friction).
- [x] 4a.7 GREEN — `src/perf/config/loader.py:66`: added explicit `"count": 0.0` to
  `DEFAULT_FLOORS` with a rationale comment; updated the three existing `test_config_loader.py`
  assertions that pinned the old 4-key floor dict literal (`test_compare_tuning_defaults_when_
  nothing_configured`, `test_perf_toml_overrides_threshold_and_partial_floor`,
  `test_full_floors_override_replaces_all_units`) to include `"count": 0.0`.
- [x] 4a.8 Verify slice: `./.venv/bin/pytest -q tests/unit/test_reassure_compare.py` — 17
  passed.
- [x] 4a.9 Verify gates — `ruff check .`, `ruff format --check .`, `mypy src/perf`,
  `pytest -q --cov=perf` all clean (1328 passed, 95.58% coverage, floor 93%).

---

## PR4b — `reassure compare` (D3, D7)

**Branch**: `reassure-read/slice4b-compare-cli` · **Base**:
`reassure-read/slice4a-compare-domain` · **Est. lines**: ~450 — **actual: 388 `src/`-only
changed lines, under the 400 gate**

- [x] 4b.1 RED — `tests/contract/test_reassure_compare_v1_contract.py` [new]: exact key set incl.
  the three flat D5 keys, a `verdicts` array in fixed order (`duration_ms`, `render_count`),
  `baseline_import_n`; D5 negatives (no `*_delta_pct`, NULL asserted `is None`).
- [x] 4b.2 GREEN — `src/perf/contracts/reassure_compare_v1.py` [new]: `SCHEMA_VERSION = 1`;
  pure `build_reassure_compare_payload(**kwargs)` (14/14 contract tests passing).
- [x] 4b.3 RED — `tests/integration/test_cli_reassure_compare.py` [new]: a regressed
  `render_count` still exits `0` with the regression verdict (D3); one-import `name` (no
  baseline window) exits `0` with an explicit insufficient-data state; unknown `name` exits
  `2`.
- [x] 4b.4 GREEN — `src/perf/cli/commands/reassure.py`: add `reassure_compare` —
  `store.reassure_series(name, config.baseline_n + 1)` (A8) →
  `reassure_compare.compare_series(...)` → payload → render; ALWAYS exit `0` except unknown
  `name` (`2`) / store failure (`3`); register as `"compare"`. Renamed the shared
  `_UnknownReassureHistoryName` → `_UnknownReassureSeriesName`, now used by both `history` and
  `compare` (11/11 CLI integration tests passing).
- [x] 4b.5 RED — `tests/golden/test_reassure_compare_pretty_golden.py` [new]: verdict table +
  the D5 sentence below the table, color forced off. Confirmed genuinely RED (3 missing-fixture
  `FileNotFoundError`s, 13 non-golden guards already green from 4b.4's renderer).
- [x] 4b.6 GREEN — `src/perf/cli/output/reassure_compare_pretty.py` [new]: box + verdict table
  reusing `table_line`/`header_line`/`Cell`/`arrow_and_pct`/`sparkline`/`GLYPH_*`; private
  `_row_glyph`/`_status_code`/`_status_word` trio (not promoted to `primitives.py` — rule of
  three not met); regenerated golden fixtures (16/16 golden tests passing). The D5 sentence is
  **reused verbatim** via a newly-public `reassure_show_pretty.d5_sentence` (renamed from
  `_d5_sentence`) rather than a second copy of the six-line wording table — precedent:
  `cli/output/flow_picker_terminal.py` already imports from `cli/output/flow_picker.py`.
- [x] 4b.7 **Already complete from PR3** — `docs/baselines-and-history.md`'s
  "`reassure`: one point per import, not per commit" section already existed. Verified and
  tightened: its closing sentence said "`reassure compare` (once it ships) reuses..." (future
  tense, written before this command existed) — rewritten to present tense with `compare_series`'s
  actual mechanics (`baseline_n + 1` fetch, plain median, `MIN_BASELINE_IMPORTS = 3`) and a link
  to the new `docs/commands.md#reassure-compare-name` section. No second section added, no
  contradiction with the first.
- [x] 4b.8 Verify slice — all reassure-scoped tests: 366 passed (`pytest -k reassure`).
- [x] 4b.9 Verify gates — `ruff check .`, `ruff format --check .`, `mypy src/perf` (73 files, no
  issues), `pytest -q --cov=perf` all clean (1369 passed, 95.60% coverage, floor 93%).

---

## PR5 — `reassure run` (D6, Last)

**Branch**: `reassure-read/slice5-run` · **Base**: `reassure-read/slice4b-compare-cli`
· **Est. lines**: ~420

- [x] 5.1 RED — `tests/unit/test_reassure_run.py` [new]: `run_reassure(runner, argv, *,
  on_line)` with a fake runner — non-zero returncode → no import attempted; `argv` is always a
  LIST, never a shell string.
- [x] 5.2 RED — same file: `detect_package_manager(root)` returns the right value per
  `yarn.lock`/`pnpm-lock.yaml`/`package-lock.json`, and the documented default when none exist.
- [x] 5.3 GREEN — implement `run_reassure(runner, argv, *, on_line)` on the house
  `SubprocessRunner.run_streamed` seam (A12); add a fake runner to `tests/fakes.py`.
- [x] 5.4 GREEN — `src/perf/config/loader.py`: add `reassure_command: Sequence[str] = ("npx",
  "reassure")` to `PerfConfig`, overridable ONLY as a TOML array.
- [x] 5.5 GREEN — `src/perf/cli/commands/init.py`: pure `detect_package_manager(root: Path) ->
  str` + a `reassure` wizard block reusing `_prompt_bundle_id`'s dim pre-filled-default idiom
  (`:435-453`), prompting for `reassure_path` and `reassure_command`.
- [x] 5.6 RED — `tests/integration/test_cli_init.py` (extend): fresh `perfvibe init` on a
  directory with no existing config produces a `perfvibe.toml` containing `reassure_path`.
- [x] 5.7 RED — `tests/integration/test_cli_reassure_run.py` [new]: `reassure run` and
  `reassure import <path>` against the same file produce the SAME `reassure_import_v1` payload
  shape and exit code; a failing subprocess exits `3`, no import attempted; a noisy fake
  command's stdout under `--json` still parses as exactly one JSON object; unset/invalid
  `reassure_command` exits `2`.
- [x] 5.8 GREEN — `src/perf/cli/commands/reassure.py`: add `reassure_run` — resolve
  `config.reassure_path`/`config.reassure_command`, run via `run_reassure`; non-zero →
  `emit_error` + exit `3` (no import); zero → delegate into the SAME parse-then-store path
  `reassure_import` uses, emit `reassure_import_v1` (A11, no new contract); register as
  `"run"`, the LAST subcommand.
- [x] 5.9 GREEN — `docs/commands.md`, `docs/configuring-flows.md`: document `reassure run` and
  `reassure_command`.
- [x] 5.10 Verify slice.
- [x] 5.11 Verify gates + full chain: `./.venv/bin/pytest -q --cov=perf` (>= 93%) with all ten
  slices merged.
- [x] 5.12 Runtime harness: `perfvibe reassure run --json` against a fake/real reassure
  invocation — confirm payload shape and exit discipline end-to-end.
