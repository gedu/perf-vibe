# Design: Ingest reassure `.perf` Files

## Load-Bearing Invariant: `durations[]` and `counts[]` Are NOT Index-Aligned

**This overrides `exploration.md` §4.1 and `proposal.md` In Scope, both of which say
"paired by index". That premise is factually wrong and the coordinator is correcting
those two files; this design does not pair them anywhere.**

Verified against `@callstack/reassure`,
`packages/measure/src/measure-helpers.tsx` → `processRunResults`:

```ts
const warmupResults = inputResults.slice(0, options.warmupRuns);
const runResults    = inputResults.slice(options.warmupRuns);
const { results, outliers } = options.removeOutliers ? findOutliers(runResults) : { results: runResults };
const durations = results.map((r) => r.duration);      // OUTLIER-FILTERED set
const counts    = runResults.map((r) => r.count);      // UNFILTERED post-warmup set
return { runs: runResults.length, /* … */ durations, counts };
```

`removeOutliers` defaults to **`true`** (`packages/measure/src/config.ts`). Therefore:

| # | Invariant | Consequence for this design |
|---|---|---|
| 1 | `len(counts) == runs` always (both are `runResults.length`) | `runs` is the count series' cardinality, not the durations' |
| 2 | `len(durations) == runs - len(outliers)`, so `len(durations) <= len(counts)`; equality only when no outlier was detected or `removeOutliers` is off | Any schema with one row per index and both values is structurally wrong |
| 3 | `durations[i]` and `counts[i]` **do not describe the same run** once any outlier is dropped | Zipping them writes plausible-looking corrupt data — no exception, no error, just wrong numbers feeding percentiles later. **Nothing downstream may zip these two series.** |
| 4 | `outlierDurations` is `undefined` — key **ABSENT** — when `removeOutliers` is off (`outliers?.map(...)`), which is a different state from `[]` (present, none found) | The passthrough column preserves the distinction: SQL `NULL` = key absent, `'[]'` = present-but-empty |
| 5 | `findOutliers` short-circuits when `items.length <= 1` (`packages/measure/src/outlier-helpers.tsx`) and returns every item as a result | A 1-run measurement never loses its duration: `len(durations) == len(counts) == 1` |
| 6 | If every post-warmup run is classified an outlier, `durations` is `[]` while `counts` is not | Specified below: the entry is **persisted with zero duration samples**, never skipped |

This is the reassure equivalent of the component-dimension trap already recorded in the
proposal: a wrong assumption that looks entirely reasonable and produces plausible-looking
corrupt output. It is asserted in the schema shape (two separately-indexed sibling tables,
so there is no row that could carry a false pairing), in the domain type's docstring, and
in the risks table below.

## Technical Approach

Four additive tables in `0005_add_reassure_tables.sql` (raw-series shape, mirroring
`measure`'s store-raw-derive pattern at `schema.sql:58-63`), three frozen dataclasses in
`domain/model.py`, one new `ReassureParser` Protocol plus one new `Store` method in
`domain/ports.py`, one parsing adapter, one `SqliteStore` transaction, one flat CLI
command. No `application/` use-case: the pipeline is exactly two sequential port calls,
so it follows `cli/commands/compare.py:28,53-80` (direct port calls from the CLI), not
`run`'s use-case. Nothing here judges, compares, or gates.

## Architecture Decisions

| Decision | Choice | Rejected | Rationale |
|---|---|---|---|
| Raw sample storage | **Two sibling fixed-column tables**, `reassure_duration_sample` and `reassure_count_sample`, each with its OWN `idx` | (a) one `reassure_sample(idx, duration_ms, render_count)` paired by index; (b) EAV `(entry_id, series, idx, value)`; (c) a `series` discriminator + one `value` column; (d) two JSON-text arrays on `reassure_entry` | (a) is the corruption bug above — a single row carrying both values *asserts* a pairing that does not exist. (b)/(c) contradict `schema.sql:65` ("Fixed columns > EAV") and force a residual predicate on every read. (d) destroys the percentile query surface that is the entire reason to store raw (`schema.sql:97-108`). Two tables cost one extra table and one extra (UNIQUE-supplied) index, and make the independence structural rather than a comment nobody reads. |
| Empty `durations` (invariant 6) | Persist the entry with **zero** duration-sample rows | Skip the entry as malformed | `counts`, `name` and `runs` are still valid data; skipping would discard a real measurement line. An entry with no duration series is legitimate input, and the follow-up compare must read it as "no duration series", never as `0 ms`. |
| `runs` column | **Persisted**, despite `runs == len(counts)` | Drop it and derive `COUNT(*) FROM reassure_count_sample` | Not the same case as `meanDuration`/`stdevDuration`. Those are *statistics* whose recomputation from raw samples is the whole methodology point, so a stored copy would drift against `domain/statistics.py`. `runs` is a **declared scalar** that no percentile path ever recomputes; keeping it makes "declared cardinality ≠ stored cardinality" detectable, which is exactly how a truncated or hand-edited `.perf` file announces itself. A mismatch is integrity information to surface later, never something to silently repair. |
| Store return type | `int \| None` — new `import_id`, `None` = byte-identical duplicate | A `ReassureImportOutcome` dataclass; `(int, bool)` tuple | Proposal scopes exactly three new dataclasses (header, entry, parse result). Counts are derivable from the `ReassureParseResult` the CLI already holds. `None`-means-nothing matches `Analyzer.compare_latest` (`ports.py:140-142`) and `get_run_summary` (`store_sqlite.py:464`). |
| Duplicate detection | `cur.rowcount == 0` after `ON CONFLICT(content_hash) DO NOTHING` | A `SELECT` by hash first; a `lastrowid` check | A pre-`SELECT` is a second round trip and a TOCTOU window inside the same transaction. `lastrowid` retains a **stale** value after a no-op insert — reading it would report a bogus `import_id`. |
| `issues` field | Not persisted, not carried in the domain type | A third `issues_json` passthrough column | Proposal's Approach settled exactly two passthrough columns. Hook point: a `0006` adds `issues_json` if a diagnosis surface ever needs it. |
| Sample-table indexes | None beyond each table's `UNIQUE (entry_id, idx)` | A separate `INDEX … (entry_id)` per table | The UNIQUE index is already `entry_id`-leading, which is the only access path ("all samples of this entry"). Speculative indexes are rejected. |
| Registry shape | Plain `build_reassure_parser` factory | A `REASSURE_PARSERS` name-keyed map + `_build` | `registry.py:174-177` states the rule outright: one implementation → one factory, no map. Mirrors `build_context_provider` (`registry.py:166-185`). |
| Parser exception | `ReassureParseError(RuntimeError)` local to `adapters/reassure_jsonl.py` | A shared `domain` exception; bare `OSError` to the CLI | Follows `FlashlightParseError` (`sampler_flashlight.py:55`): an adapter-specific failure lives with its adapter, and the CLI maps it to one exit code. |
| Pretty rendering | Local `_render_import_pretty(payload)` in the command module | A new `cli/output/reassure_pretty.py` | One confirmation block; mirrors `_render_doctor_pretty` (`markers.py:300-319`). Rule of three not met. |
| Finite-number guard | Local `_is_finite_number` in the adapter | Import `sampler_flashlight._is_finite_number:41-48`; promote to `domain/` | Adapters never import one another. A 3-line predicate does not earn a shared home. |
| Call order | Parse first, open the store second | Build the store, then parse | A bad path must not create a SQLite file as a side effect; it also keeps exit `2` (usage) strictly ahead of exit `3` (runtime). |

## DDL

`src/perf/db/migrations/0005_add_reassure_tables.sql` (no pragmas, no `user_version` —
the runner owns both, `migrations/0001_init.sql:3-7`), mirrored verbatim into
`src/perf/db/schema.sql` under its own section banner.

```sql
-- Additive DDL only — four NEW tables plus three indexes; NO existing table,
-- column, index, view or row is touched (so `run`/`compare`/`budget-check`/
-- `history` cannot be affected by this migration or by reverting it).
-- Reassure (`@callstack/reassure`) deliberately does NOT reuse the
-- flow x device x metric -> measure star: it has no device dimension and
-- carries TWO INDEPENDENT value series (durations[] in ms, counts[] of
-- renders) against a single `duration_ms` column. Raw samples are stored and
-- aggregates derived at read time, matching `measure` (schema.sql:58-63), NOT
-- `system_sample`'s aggregate-only shape (schema.sql:68-78). Picked up by the
-- existing `PRAGMA user_version`-driven runner (`SqliteStore._migrate`).

-- ===== REASSURE IMPORTS (one row per ingested .perf file) =====
CREATE TABLE reassure_import (
  import_id    INTEGER PRIMARY KEY,
  content_hash TEXT NOT NULL UNIQUE,   -- sha256 of the RAW file bytes: the whole
                                       -- idempotency key. UNIQUE + ON CONFLICT DO
                                       -- NOTHING makes a byte-identical re-import a
                                       -- no-op. Deliberately NOT keyed on commit_hash
                                       -- (many runs per commit are expected --
                                       -- store_sqlite.py:372-416 -- and the header is
                                       -- optional, so commit_hash may be absent).
  imported_at  TEXT NOT NULL,          -- ISO-8601 UTC from the injected Clock
                                       -- (store_sqlite.py:394), sorts as text
  source_path  TEXT NOT NULL,          -- the .perf path as given, for provenance only
  branch       TEXT,                   -- header metadata.branch      (all three are
  commit_hash  TEXT,                   -- header metadata.commitHash   optional, and the
  created_date TEXT                    -- header metadata.creationDate header line
                                       -- itself is optional)
);

-- ===== REASSURE ENTRIES (one row per measurement line) =====
CREATE TABLE reassure_entry (
  entry_id          INTEGER PRIMARY KEY,
  import_id         INTEGER NOT NULL REFERENCES reassure_import(import_id) ON DELETE CASCADE,
  name              TEXT NOT NULL,     -- Jest's `currentTestName` (space-joined, NO delimiter). The ONLY
                                       -- identity: reassure has NO component field and
                                       -- NO test-file field, so any component dimension
                                       -- is DERIVED, never stored. Intentionally NOT
                                       -- UNIQUE(import_id, name) -- per-file uniqueness
                                       -- of `name` is unverified; a follow-up assuming
                                       -- one row per name must defend itself.
  entry_type        TEXT NOT NULL DEFAULT 'render',  -- 'render'|'function'|'async function'
  runs              INTEGER NOT NULL,  -- reassure's DECLARED `runs` (== len(counts) ==
                                       -- runResults.length). Kept even though derivable:
                                       -- no percentile path recomputes it, and a
                                       -- declared-vs-stored cardinality mismatch is how a
                                       -- truncated/hand-edited .perf file announces
                                       -- itself. NOT the meanDuration/stdevDuration case
                                       -- (those are statistics that WOULD drift, and are
                                       -- deliberately absent -- derive them from the
                                       -- sample tables below).
  warmup_durations  TEXT,              -- verbatim JSON array passthrough, DIAGNOSTIC ONLY
  outlier_durations TEXT               -- (no typed domain field, no index, no query
                                       -- surface). NULL = the JSON key was ABSENT
                                       -- (`outlierDurations` is `undefined` when
                                       -- removeOutliers is off); '[]' = present but
                                       -- empty. Those two states are NOT collapsed.
);

-- ===== RAW SERIES: TWO SIBLING TABLES, EACH WITH ITS OWN ORDINAL =====
-- LOAD-BEARING: `durations[]` and `counts[]` are NOT index-aligned and MUST NEVER be
-- zipped. `durations` comes from the OUTLIER-FILTERED set, `counts` from the UNFILTERED
-- post-warmup set (measure-helpers.tsx `processRunResults`), and `removeOutliers`
-- defaults to TRUE -- so len(durations) <= len(counts), and index i of one series does
-- NOT describe the same run as index i of the other. A single table carrying both values
-- per row would ASSERT that false pairing and silently corrupt every later percentile.
-- Two separately-indexed tables make the independence structural. Each `idx` is an
-- ordinal WITHIN ITS OWN SERIES only; it is not a run identifier and is not comparable
-- across the two tables. Neither table needs its own extra index: the UNIQUE constraint
-- is already `entry_id`-leading, which is the only access path.
CREATE TABLE reassure_duration_sample (
  duration_sample_id INTEGER PRIMARY KEY,
  entry_id           INTEGER NOT NULL REFERENCES reassure_entry(entry_id) ON DELETE CASCADE,
  idx                INTEGER NOT NULL,  -- ordinal within durations[] only
  duration_ms        REAL NOT NULL,     -- durations[idx], milliseconds
  UNIQUE (entry_id, idx)
);
CREATE TABLE reassure_count_sample (
  count_sample_id INTEGER PRIMARY KEY,
  entry_id        INTEGER NOT NULL REFERENCES reassure_entry(entry_id) ON DELETE CASCADE,
  idx             INTEGER NOT NULL,  -- ordinal within counts[] only
  render_count    REAL NOT NULL,     -- counts[idx]: a SECOND series with a DIFFERENT unit
                                     -- ('count'). REAL, not INTEGER: reassure types it
                                     -- `number[]`, so a non-integral value must never be
                                     -- silently truncated.
  UNIQUE (entry_id, idx)
);

-- ===== INDEXES =====
-- The follow-up compare/history queries this change exists to enable are:
--   (a) one test's duration series over time:
--       SELECT d.duration_ms FROM reassure_entry e
--       JOIN reassure_duration_sample d ON d.entry_id = e.entry_id
--       WHERE e.name = ? ORDER BY e.import_id;
--       (the count series is the SAME query against reassure_count_sample -- a
--        SEPARATE query, never a join of the two sample tables)
--   (b) the baseline window: SELECT import_id FROM reassure_import
--       ORDER BY imported_at DESC LIMIT ?;   (same shape as history_runs on
--       run.started_at)
--   (c) this change's own confirmation read + the FK cascade:
--       SELECT COUNT(*) FROM reassure_entry WHERE import_id = ?;
-- (a) needs a name-leading composite; (b) needs imported_at; (c) needs
-- import_id-leading (SQLite does not auto-index foreign keys). Nothing else is
-- indexed: content_hash and both (entry_id, idx) pairs already have UNIQUE
-- indexes, and no query filters entry_type, branch, commit_hash or runs.
CREATE INDEX idx_reassure_entry_name   ON reassure_entry(name, import_id);
CREATE INDEX idx_reassure_entry_import ON reassure_entry(import_id);
CREATE INDEX idx_reassure_import_time  ON reassure_import(imported_at);
```

## Domain Types (`domain/model.py`)

Carries partial-coverage information on the result object rather than returning a bare
list — the `MarkerParseResult` (`model.py:434-449`) / `SystemSampleParseResult`
(`model.py:452-475`) precedent. All fields are primitives or `Sequence`s of primitives:
the module stays pure.

```python
@dataclass(frozen=True)
class ReassureHeader:
    """The optional first-line `{"metadata": {...}}` of a `.perf` file. All
    three fields are optional — nothing may depend on `commit_hash`."""

    branch: str | None = None
    commit_hash: str | None = None
    created_date: str | None = None   # reassure's ISO-8601 `creationDate`, verbatim


@dataclass(frozen=True)
class ReassureEntry:
    """One measurement line. `name` is the ONLY identity (no component, no
    test file).

    `durations` and `counts` are TWO INDEPENDENT raw series and are NOT
    index-aligned: reassure builds `durations` from the outlier-FILTERED set
    and `counts` from the UNFILTERED post-warmup set (`processRunResults` in
    `measure-helpers.tsx`), with outlier removal ON by default. So
    `len(durations) <= len(counts) == runs`, and `durations[i]` does NOT
    describe the same run as `counts[i]`. NEVER `zip()` them, never assume
    equal length, and never treat `idx` as a run identifier — doing so
    produces plausible-looking corrupt data with no error. `durations` may be
    empty (every post-warmup run classified an outlier) while `counts` is not;
    that is valid input, NOT a malformed entry.

    reassure's own mean/stdev are derivable from these series and deliberately
    absent, so there is only one source of truth."""

    name: str
    entry_type: str                 # JSON `type`; defaults to 'render' at parse time
    runs: int                       # reassure's DECLARED runs (== len(counts))
    durations: Sequence[float]      # outlier-filtered series
    counts: Sequence[float]         # unfiltered post-warmup series — NOT aligned above
    warmup_durations_json: str | None = None   # opaque passthrough text, never parsed
    outlier_durations_json: str | None = None  # here. `None` = JSON key ABSENT;
                                               # `"[]"` = present but empty.


@dataclass(frozen=True)
class ReassureParseResult:
    """Result of `ReassureParser.parse()`. `content_hash` is the sha256 of the
    raw file bytes (computed where the bytes are read — the adapter — since it
    must hash the EXACT bytes, before decoding). `skipped` pairs each rejected
    line's 1-based number with a reason from this adapter's own vocabulary;
    `partial_coverage` is `bool(skipped)`, and `diagnostic` explains a zero-
    or partial-coverage import in one actionable sentence (`None` on a clean
    full-coverage parse)."""

    header: ReassureHeader | None
    entries: Sequence[ReassureEntry]
    content_hash: str
    skipped: Sequence[tuple[int, str]]
    partial_coverage: bool
    diagnostic: str | None = None
```

## Ports (`domain/ports.py`)

Pure: primitives plus the domain types above, no adapter import, no `Path`/file object.
`parse` takes `str` exactly like `SystemSampler.parse` (`ports.py:84`); the adapter
widens to `str | Path` (`sampler_flashlight.py:133` precedent).

```python
class ReassureParser(Protocol):
    """Parses a reassure `.perf` JSON-Lines file into a `ReassureParseResult`
    (never a bare list) so partial coverage and per-line skip reasons travel
    with the data. Tolerant per line, strict per file: a malformed line is
    skipped and reported; an unreadable file raises."""

    def parse(self, path: str) -> ReassureParseResult: ...


class Store(Protocol):
    ...
    def save_reassure_import(
        self, result: ReassureParseResult, source_path: str
    ) -> int | None: ...
```

`save_reassure_import` returns the new `import_id`, or `None` when
`result.content_hash` was already present (byte-identical re-import → zero rows
inserted, exit `0`).

## Adapter — `adapters/reassure_jsonl.py`

Constructor keyword-only and all-optional (`sampler_flashlight.py:71`); the class
registers directly as its own factory and never inherits `ReassureParser`
(structural typing only).

```python
class ReassureParseError(RuntimeError):
    """The file could not be read at all (missing, unreadable, or not UTF-8) —
    mapped by the CLI to exit 2. A per-LINE problem never raises: it is skipped
    and reported in `ReassureParseResult.skipped`."""

class ReassureJsonlParser:
    def __init__(self, *, max_line_bytes: int = _MAX_LINE_BYTES) -> None: ...
    def parse(self, path: str | Path) -> ReassureParseResult: ...
```

Strategy, in order:

1. `raw = Path(path).read_bytes()`; **sha256 here** (`hashlib.sha256(raw).hexdigest()`)
   over the exact bytes, before any decode or normalisation. `OSError` (including
   `FileNotFoundError`/`PermissionError`/`IsADirectoryError`) and `UnicodeDecodeError`
   → `ReassureParseError` chained from the original.
2. `raw.decode("utf-8").splitlines()`, enumerated 1-based. Blank/whitespace-only lines
   are ignored silently (not "skipped" — they are not data).
3. A line longer than `max_line_bytes` is skipped with `REASON_OVERSIZED` without being
   handed to `json.loads` (perf-cli-standards rule 5: bound line length).
4. Line 1 only: an object carrying `metadata` and no `name` is the header; unknown
   metadata keys ignored, each of the three fields independently optional. Any other
   line-1 object is treated as an entry.
5. Every other line: `json.loads` **only** — never `eval`/`exec`. Reason vocabulary as
   module constants, mirroring `REASON_*` in `markers_adb_logcat`:
   `REASON_INVALID_JSON`, `REASON_NOT_OBJECT`, `REASON_MISSING_FIELD`,
   `REASON_UNKNOWN_TYPE`, `REASON_INVALID_VALUE`, `REASON_OVERSIZED`.
6. Entry validation: `name` non-empty `str`, `runs` an `int`, `durations` and `counts`
   each a list of finite numbers — validated **independently, with no length
   relationship asserted between them** (local `_is_finite_number`, mirroring
   `sampler_flashlight.py:41-48`, because `json.loads` accepts `NaN`/`Infinity`). An
   EMPTY `durations` is valid (invariant 6) and yields an entry with no duration
   samples; it is never a skip reason. Absent `type` → `"render"`; a `type` outside
   `{"render", "function", "async function"}` → skip with `REASON_UNKNOWN_TYPE`.
7. `warmupDurations`/`outlierDurations`: `None` when the key is **absent**,
   `json.dumps(value)` when present (so `[]` round-trips as `"[]"`), preserving the
   absent-vs-empty distinction of invariant 4. Never validated beyond "is a list".
8. The adapter **prints nothing**. It returns `skipped`; the CLI does the warning.

## Registry — `adapters/registry.py`

```python
def build_reassure_parser(*, max_line_bytes: int = _MAX_LINE_BYTES) -> ReassureParser:
    """`ReassureParser` has exactly one implementation — a single factory, no
    name-keyed map needed (see `build_context_provider`). Kwonly params mirror
    `ReassureJsonlParser.__init__` exactly."""

    return ReassureJsonlParser(max_line_bytes=max_line_bytes)
```

No `_build`/`Mapping` entry: `registry.py:174-177` states the rule for exactly this
case, and a hypothetical second `.perf` dialect is not shipping.

## Transaction Boundary — `adapters/store_sqlite.py`

Pattern followed **exactly**: `SqliteStore.save_run` (`store_sqlite.py:258-286`) —
literal `conn.execute("BEGIN")`, private helpers per insert step, `COMMIT`, and
`except Exception: conn.execute("ROLLBACK"); raise`. The connection is autocommit
(`isolation_level=None`, `store_sqlite.py:214-222`), so those literals own the boundary.

```
save_reassure_import(result, source_path):
    BEGIN
      INSERT INTO reassure_import (...) ON CONFLICT(content_hash) DO NOTHING   ← rowcount==0 ⇒ duplicate
      if duplicate: COMMIT; return None                                        ← zero rows written
      for entry in result.entries:
          INSERT INTO reassure_entry (...)
          for idx, duration in enumerate(entry.durations):  INSERT INTO reassure_duration_sample
          for idx, count    in enumerate(entry.counts):     INSERT INTO reassure_count_sample
          ^ TWO independent loops over TWO independent series — never one zipped loop
      COMMIT → return import_id
    except Exception: ROLLBACK; raise                                          ← 0 rows, always
```

New private helpers: `_insert_reassure_import`, `_insert_reassure_entry`,
`_insert_reassure_duration_samples`, `_insert_reassure_count_samples`. Every value bound
with `?`; all identifiers static literals. `imported_at` from
`self._clock.now_utc_iso()` (`store_sqlite.py:394`). A `None` `lastrowid` after a real
insert raises `RuntimeError`, mirroring `store_sqlite.py:408-416`.

## CLI — `cli/commands/reassure_import.py`

Flat command, registered next to the other four in `cli/main.py:112-135` via
`app.command(name="reassure-import", context_settings={"help_option_names": ["--help",
"-h"]})(reassure_import_command)` — **not** `add_typer` (`main.py:137-141` earned a
sub-app only because two subcommands shipped together).

```python
def reassure_import(
    ctx: typer.Context,
    path: str | None = _PATH_ARGUMENT,   # module-level typer.Argument (ruff B008)
) -> None:
```

Resolution: `resolved = path or config.reassure_path` — the argument is the primary
input, with the config field (default `.reassure/current.perf`) as the fallback. It is
an INPUT path, so it is NOT run through `_under_base` (`config/loader.py:287`), matching
`flows[].maestro_path` (`loader.py:275`).

Reused helpers, verbatim: `ctx.obj["output"]`/`["config"]` (`main.py:101`);
`emit_error` (`errors.py:201`) and `emit_warning` (`errors.py:217`) for every message;
`render_json` (`json_reporter.py:33`); `NON_TTY_NUDGE` + `output.should_nudge_stderr`
(`context.py:35-40`) on the pretty path; `build_reassure_parser`/`build_store`;
`build_reassure_import_payload`; and the `_close_store` warning shape copied from
`history.py:167-174` (a close failure never changes the exit code).

| Outcome | Handling | Exit |
|---|---|---|
| Parsed ≥1 entry, inserted | payload `already_imported: false` with non-zero `entries_imported` / `duration_samples_imported` / `count_samples_imported` | `0` |
| Byte-identical re-import | store returned `None` → payload `already_imported: true`, all three imported counts `0` | `0` |
| Readable file, zero entries | payload `entries_imported: 0` + `already_imported: false` (the zero-entries signal is derived from those two, never a dedicated key) + `emit_warning` | `0` |
| Some lines skipped | good lines imported + one bounded `warning:` per skipped line, plus one summary count | `0` |
| `ReassureParseError` (missing/unreadable/undecodable) | `emit_error(..., hint="check the path or pass it explicitly")` | `2` |
| Store/transaction/render failure or any other exception | `emit_error` (`# noqa: BLE001` safety net) | `3` |

Never `1`. Per-line warnings carry only the line number and a fixed reason token — never
the raw line — so this satisfies the proposal's per-line requirement without the raw
enumeration `perf-cli-output` rule 5 forbids; the trailing summary count is what a human
reads.

## Contract — `contracts/reassure_import_v1.py`

`SCHEMA_VERSION = 1`; pure builder, `build_reassure_import_payload(**kwargs)`, mirroring
`markers_doctor_v1.py:46-81`.

**FLAT, exactly 8 top-level keys, no nested objects and no arrays:**

```
{"schema_version": int, "path": str, "content_hash": str,
 "already_imported": bool, "entries_imported": int, "entries_skipped": int,
 "duration_samples_imported": int, "count_samples_imported": int}
```

This shape is confirmed by, and reconciled against, the delta spec at
`openspec/changes/reassure-ingest/specs/reassure-ingest/spec.md:183-194` — that
requirement is the authority on the payload, and this section was rewritten to match it
(design and spec ran concurrently; the earlier nested draft here predated the spec's
finalisation). Four decisions carried in it:

| Choice | Reasoning |
|---|---|
| **Flat, not nested** — no `header{}`, no `summary{}` | Every contract test in this repo pins the exact key set at *every* nesting level (`tests/contract/test_compare_v1_contract.py:184` — removing, renaming **or adding** a key fails). Each nesting level is therefore another permanently pinned surface to maintain. One flat level is cheaper to pin and cheaper to keep honest. |
| **Two series counters**, never one `samples_imported` | Downstream of this design's own non-alignment correction: once `durations[]` and `counts[]` are independently indexed, one number cannot describe two differently-sized series — and a single counter would itself re-imply the pairing the schema just eliminated. `duration_samples_imported` and `count_samples_imported` are free to differ (spec scenario: `2` vs `3`). |
| **No `zero_entries` key** | It is a pure derived AND of `entries_imported == 0` and `already_imported == false`. Persisting a derived boolean is exactly the second-source-of-truth problem this design already invokes to reject storing `meanDuration`/`stdevDuration`; applying that principle to the DDL but not to the payload would be inconsistent. The consumer derives it. |
| **`already_imported` (bool), not `import_id`/`imported`** | The idempotency outcome is the whole public signal. A surrogate row id is an internal detail of the store and is not part of the machine contract — so the CLI computes `already_imported = (returned_import_id is None)` and never publishes the id itself. |

Two consequences of the flat shape, stated so nobody "reconciles" them the wrong way
later:

- **Per-line skip detail is STDERR-only.** The payload carries the `entries_skipped`
  count; the per-line `(line, reason)` pairs and `ReassureParseResult.diagnostic` are
  rendered as `emit_warning` lines on stderr and appear nowhere in the payload. That is
  also the better reading of `perf-cli-output` rule 5 — the machine contract summarises,
  the human stream carries the detail. `ReassureParseResult.skipped`/`diagnostic` remain
  fully justified as domain fields: they feed those warnings.
- **`path` (payload) and `source_path` (DDL column + store method parameter) are
  deliberately different names** and neither should be renamed to match the other: one is
  the pinned public key, the other is internal storage.

## Data Flow

    perfvibe reassure-import <path>          (cli/commands/reassure_import.py)
      │  resolved = path or config.reassure_path
      ├─ build_reassure_parser() → ReassureJsonlParser.parse(resolved)
      │     read_bytes → sha256 → decode → per-line json.loads → skip+record
      │     └→ ReassureParseResult(header, entries, content_hash, skipped, …)
      │        ReassureParseError ─────────────────────────────→ emit_error → exit 2
      ├─ build_store(config.db_path) → SqliteStore.save_reassure_import(result, path)
      │     BEGIN → import (ON CONFLICT DO NOTHING) → entries
      │             → duration samples ─┐  two independent loops,
      │             → count samples ────┘  never zipped
      │             → COMMIT
      │     └→ import_id | None            any exception → ROLLBACK → emit_error → exit 3
      └─ build_reassure_import_payload(…) → render_json (STDOUT) | _render_import_pretty
         result.skipped ─────────────────→ emit_warning (STDERR only)  → exit 0

## File Changes

| File | Action | Description |
|---|---|---|
| `src/perf/db/migrations/0005_add_reassure_tables.sql` | Create | The DDL above (4 tables, 3 indexes); no pragmas, no `user_version` |
| `src/perf/db/schema.sql` | Modify | Mirror the DDL under a `REASSURE` banner |
| `src/perf/domain/model.py` | Modify | 3 frozen dataclasses, with the non-alignment invariant in `ReassureEntry`'s docstring |
| `src/perf/domain/ports.py` | Modify | `ReassureParser` Protocol + `Store.save_reassure_import` |
| `src/perf/adapters/reassure_jsonl.py` | Create | `ReassureParseError` + `ReassureJsonlParser` |
| `src/perf/adapters/store_sqlite.py` | Modify | `save_reassure_import` + 4 private helpers |
| `src/perf/adapters/registry.py` | Modify | `build_reassure_parser` |
| `src/perf/config/loader.py` | Modify | `reassure_path: str = ".reassure/current.perf"`, NOT `_under_base` |
| `src/perf/contracts/reassure_import_v1.py` | Create | `--json` payload builder — the flat 8-key shape, two independent series counters, no nesting |
| `src/perf/cli/commands/reassure_import.py` | Create | Flat command + local pretty renderer |
| `src/perf/cli/main.py` | Modify | `app.command(name="reassure-import")` registration |
| `tests/fixtures/reassure_sample.perf` | Create | Real sample: an entry where `len(durations) < len(counts)`, one with `durations: []`, one with `outlierDurations` absent, plus the malformed cases |
| `tests/integration/test_schema.py` | Modify | `MIGRATION_0005` constant (`:20-22`) + `executescript` line (`:184-186`) |
| `tests/integration/test_store_migrations.py` | Modify | Six `== 4` → `== 5` (lines 30, 86, 138, 174, 228, 245) + reword the three chain-enumerating comments (86, 138, 245) |
| `tests/unit/test_reassure_jsonl.py`, `tests/contract/test_reassure_import_v1_contract.py`, `tests/integration/test_store_reassure.py`, `tests/integration/test_cli_reassure_import.py` | Create | See Testing Strategy |

`tests/unit/test_domain_boundary.py:41` and `test_application_boundary.py:30` glob their
directories, so the new domain code is boundary-checked with no opt-in.

## Testing Strategy

| Layer | What to test | Approach |
|---|---|---|
| Unit | Parser: header present/absent/partially populated; invalid JSON; unknown `type`; missing `name`/`runs`; `NaN`/`Infinity`; oversized line; sha256 over exact bytes; missing/unreadable path raises `ReassureParseError` | Real fixture files in `tmp_path`; no I/O fake needed (the adapter IS the I/O edge) |
| Unit | **Non-alignment (load-bearing)**: `len(durations) < len(counts)` parses into two sequences of their own real lengths, with NO padding, NO truncation and NO `None` filler; `durations: []` with non-empty `counts` yields a valid entry | Fixture entry with 8 counts and 6 durations → assert both lengths exactly |
| Unit | `outlierDurations` absent → `None`; present-and-empty → `"[]"` | Two fixture entries, direct field assertion |
| Unit | `build_reassure_import_payload` returns exactly the 8 flat keys — no `samples_imported`, no `zero_entries`, no nested object anywhere | Direct call; assert `set(payload) == {…}` and that every value is a scalar |
| Integration | **Non-alignment at rest**: an 8-count/6-duration entry persists exactly 6 `reassure_duration_sample` and 8 `reassure_count_sample` rows, each with contiguous `idx` from 0 within its OWN table, and no row asserts a cross-series pair | Real `SqliteStore` on `tmp_path`; per-table `COUNT(*)` + `idx` list assertions |
| Integration | Insert counts; byte-identical re-import inserts zero rows and returns `None`; a forced mid-transaction failure leaves **0** rows in all four tables | Failure injected by a patched helper |
| Integration | `user_version` 4 → 5; `schema.sql` ≡ migration chain | Existing `test_schema_sql_and_migrations_are_fully_equivalent` + `MIGRATION_0005` |
| Contract | The exact 8-key `reassure_import_v1` top-level set (flat — one level to pin), with `duration_samples_imported` free to differ from `count_samples_imported`; STDOUT byte-pure under `--json` while every warning and per-line skip reason goes to STDERR | `CliRunner` with separated streams; the spec's `durations: [10, 12]` / `counts: [1, 1, 1]` case asserts `2` vs `3` |
| Integration (CLI) | Exit `0` (imported / duplicate / zero-entry / partial), `2` (missing path), `3` (forced store failure); never `1` | `CliRunner`, fixture + `--db tmp_path` |

RED before GREEN for every row; 93% coverage floor (`AGENTS.md:40`).

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED test |
|---|---|---|---|
| Untrusted input parsing (`.perf` JSONL) | Applicable | `json.loads` only, never `eval`/`exec`; line length bounded by `max_line_bytes`; malformed line skipped, never fatal | Fixture with invalid JSON, an oversized line, and a `NaN` value → all skipped, good lines imported, exit `0` |
| **Silent data corruption via false series pairing** | Applicable | Two separately-indexed sibling tables; two independent insert loops; the invariant asserted in the DDL comment, the domain docstring and the payload's separate counters | The 8-count/6-duration test above, at both the parse and the persistence layer |
| SQL injection | Applicable | Every value bound with `?`; all table/column names static literals; `name`/`entry_type` are bound VALUES | Import an entry whose `name` is `x'); DROP TABLE run;--` → stored verbatim, `run` table intact |
| `--db` path handling | Applicable | Opened as a local SQLite file only, never executed/imported; migrations loaded only from the package `db/migrations/` | Existing store tests cover; no new surface |
| Input path handling | Applicable | `reassure_path` is read as bytes only, never executed; not re-anchored via `_under_base` | Directory-as-path and unreadable-file cases → exit `2` |
| Subprocess / shell | N/A | This change spawns no process. | — |
| Routing / VCS-PR automation / executable classification | N/A | None present. | — |

## Migration / Rollout

Additive `0005` only; `SqliteStore._migrate` (`store_sqlite.py:234`, called from
`__init__` at `:196`) applies it in one cascade on open, so any store opened after this
lands on version 5. Reverting the code leaves four unused tables; a `0006` drop
migration removes them if desired. No feature flag: the command is inert until invoked.

## Risks

| Risk | Likelihood | Mitigation / owner |
|---|---|---|
| **A follow-up compare zips `durations` with `counts`** and silently mis-attributes render counts to durations. The arrays look like siblings, the shorter one is shorter only when an outlier was removed, and a zip raises nothing | High (the assumption is the natural one) | Structural: two tables, two `idx` domains, separate payload counters, invariant stated in the DDL comment AND `ReassureEntry`'s docstring AND the risks table. The follow-up compare MUST read each series with its own query and MUST NOT join the two sample tables. Flagged here for that change explicitly |
| An entry with `durations: []` is read as `0 ms` instead of "no duration series" | Med | Specified above as valid input; the follow-up compare must treat an empty duration series as absent data, the way `partial_coverage` already works elsewhere |
| The concurrently-written delta spec (`specs/`) may still say "paired by index", inherited from the uncorrected exploration/proposal | High | `sdd-tasks` must reconcile: this design is the corrected authority on sample storage. Flagged for the orchestrator |
| `runs` disagrees with the stored count-sample cardinality (truncated/hand-edited file) | Low | `runs` is persisted precisely so the mismatch is detectable; this change stores both and repairs neither |
| Noise floor keyed by unit, not metric (`domain/calibration.py:203`) | Med | Out of scope here — see Forward-Looking Notes |
| `build_analyzer` types on concrete `SqliteStore` (`adapters/registry.py:221-225`) | Low | Pre-existing leak — see Forward-Looking Notes |
| Slice 1 exceeds the 400-line review budget | High | Sub-split 1a/1b described below |

## Slice Boundaries

Exploration §5's three-slice split is confirmed in shape but **revised in two places**.

**Revision 1 (correctness, not preference): the pinned schema-test edits move from
Slice 2 to Slice 1.** `_migrate` (`store_sqlite.py:234`) cascades every pending
migration on open, so the moment `0005` exists on disk the six `== 4` assertions at
`test_store_migrations.py:30,86,138,174,228,245` fail — regardless of whether
`save_reassure_import` exists. Exploration §5 put those edits in the persistence slice,
which would ship a red `main`. The DDL and its pinned tests are one atomic unit.

**Revision 2: the contract module moves from Slice 1 to the CLI slice.** Its key set is
determined by what the CLI actually reports (`already_imported`, `entries_imported`,
`entries_skipped`, and the two per-series `*_samples_imported` counters) — pinning it
before the store method and the command exist means either guessing or churning a
`schema_version`-pinned file twice. Confirmed still correct after the flat-payload
reconciliation: `already_imported` is derived from the store's `int | None` return, so
the contract cannot be pinned before that return exists.

| # | Lands | Est. authored lines | Independently reviewable because | Independently revertable because |
|---|---|---|---|---|
| 1 | `0005` migration (4 tables, 3 indexes), `schema.sql` mirror, `MIGRATION_0005` + `executescript` line, the six `== 5` updates and three comment rewordings, 3 dataclasses, `ReassureParser` Protocol, `adapters/reassure_jsonl.py`, `build_reassure_parser`, fixture, parser unit tests **including the non-alignment tests** | ~430-460 | Additive DDL plus one pure-decision parser with a real fixture; nothing consumes the tables yet, so the whole suite stays green | Delete the migration + adapter + dataclasses; no other module imports them |
| 2 | `Store.save_reassure_import` on the Protocol, `SqliteStore` implementation + 4 helpers, store integration tests (insert / per-series row counts / idempotent re-import / rollback-leaves-zero-rows) | ~230-260 | One transaction method against tables that already exist on `main`, tested end-to-end against a real SQLite file | Drop the method; the tables stay unused — exactly the proposal's rollback plan |
| 3 | `config.reassure_path`, `contracts/reassure_import_v1.py` + contract test, `cli/commands/reassure_import.py`, `main.py` wiring, CLI integration tests | ~300-340 | The user-facing surface over two ports that already exist and are already tested | Unregister the command in `main.py`; the ports remain callable but unreached |

Total ~960-1060 authored lines (the two-table correction adds ~50 over exploration
§4.7's ~920: one extra table, one extra insert helper, and the non-alignment tests at
both layers). Slice 1 is `High` against the 400-line budget, Slice 2 `Low`, Slice 3
`Medium`. **If every slice must be strictly under budget, split Slice 1 into 1a
(migration + `schema.sql` mirror + the two pinned test files, ~145 lines — a
self-contained schema-only PR) and 1b (domain types + Protocol + adapter + registry +
fixture + parser tests, ~300).** That is the only sub-split that keeps a DDL change
atomic with its pinned tests. The final call is the orchestrator's at `sdd-tasks`.

**PR numbering.** Under that recommended sub-split the delivery order is PR1 = 1a
(schema), PR2 = 1b (domain + parser), PR3 = persistence, **PR4 = CLI + contract** — so
`contracts/reassure_import_v1.py` and its contract test land in PR4, as `sdd-tasks`
expects. Without the sub-split the same unit is PR3. Either way the contract module is
always the last slice, never the first.

## Forward-Looking Notes (known debt — NOT fixed here)

- **Noise floor keyed by unit, not metric.** `domain/calibration.py:203` resolves the
  floor with `floors.get(unit, 0.0)`, so a `"count"`-unit reassure series with no
  configured floor silently gets `floor=0.0` → false negatives. Untouched here (this
  change computes no verdict). Hook point: the follow-up compare change, at that exact
  call site, plus a `"count"` default in `DEFAULT_FLOORS` (`config/loader.py`). Note the
  compound risk: the count series is exactly the one this defect hits.
- **`Analyzer` is not a clean substitution seam.** `build_analyzer`
  (`adapters/registry.py:221-225`) types on the concrete `SqliteStore`, not the `Store`
  Protocol, because `SqlAnalyzer` calls five methods only it has. Untouched here (no
  analyzer is built). Hook point: whichever follow-up first needs a second `Analyzer`
  implementation must resolve it at `registry.py:221-225` — either by narrowing to a
  read-only Protocol or by adding a second concretely-typed factory.

## Open Questions

None blocking. Every position was settled by the proposal's Settled Constraints table,
by the verified non-alignment correction above, or decided in the decisions table with
its rejected alternative recorded.
