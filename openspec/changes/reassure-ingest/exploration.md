# Exploration: `reassure-ingest`

Ingest `@callstack/reassure` `.perf` files into the perf-vibe SQLite store.

**Phase:** `sdd-explore` · **Artifact store:** hybrid (Engram `sdd/reassure-ingest/explore`)
**Scope:** ingest only. Compare, history views, name/component filtering and budget
gating are explicitly out of scope and belong to follow-up changes.

---

## 1. Established input format

Verified against `callstack/reassure` GitHub source (`main`):
`packages/measure/src/config.ts`, `packages/measure/src/output.ts`,
`packages/compare/src/type-schemas.ts`.

Default path `.reassure/current.perf`, overridable via `REASSURE_OUTPUT_FILE`. CI
additionally writes a `baseline.perf` sibling by checking out the base ref.

The file is **JSON Lines** — one JSON object per line, `\n`-terminated.

An **optional** first line is a header:

```json
{"metadata": {"branch": "...", "commitHash": "...", "creationDate": "<ISO-8601>"}}
```

All three metadata fields are optional, and the header line itself is optional.

Every other line is a measurement entry:

| Field | Type | Notes |
| --- | --- | --- |
| `name` | `string` | Jest test-state name (the `describe > test` chain). **The only identity.** |
| `type` | `'render' \| 'function' \| 'async function'` | Defaults to `'render'`. |
| `runs` | `number` | |
| `meanDuration` | `number` | Derivable from `durations`. |
| `stdevDuration` | `number` | Derivable from `durations`. |
| `durations` | `number[]` | Milliseconds. Outlier-**filtered** — see the invariant below. |
| `warmupDurations` | `number[]?` | The pre-measurement warmup slice. |
| `outlierDurations` | `number[]?` | Key is **absent** (not empty) when `removeOutliers` is off. |
| `meanCount` | `number` | Derivable from `counts`. |
| `stdevCount` | `number` | Derivable from `counts`. |
| `counts` | `number[]` | Render/update counts — a **second series with a different unit**. **Not** outlier-filtered. |
| `issues` | `{ initialUpdateCount?: number, redundantUpdates?: number[] }?` | |

**There is no component field and no test-file field.** "By name" and "by test" are
the same string. A component dimension can only ever be *derived* (naming
convention or user-supplied mapping); it is never stored data.

### 1.1 Invariant: `durations` and `counts` are NOT index-aligned

Verified verbatim in `packages/measure/src/measure-helpers.tsx`,
`processRunResults`:

```ts
const { results, outliers } = options.removeOutliers ? findOutliers(runResults) : { results: runResults };

const durations = results.map((result) => result.duration);   // outlier-FILTERED
const outlierDurations = outliers?.map((result) => result.duration);

const counts = runResults.map((result) => result.count);      // NOT filtered
```

`removeOutliers` defaults to **`true`** (`packages/measure/src/config.ts`).
Therefore:

- `counts.length === runs` always — both are `runResults.length`.
- `durations.length === runs - outliers.length`, so `durations.length <= counts.length`.
- Index `i` of `durations` and index `i` of `counts` **do not refer to the same
  run** once any outlier is dropped.

**Nothing may zip the two series.** They are two independently-indexed series
that happen to share an entry, and storing them as index-aligned pairs writes
silently mismatched data — no exception, no error, just wrong numbers feeding
percentiles downstream. This is the second trap of the same family as the
component dimension: a wrong assumption that looks completely reasonable and
produces plausible-looking corrupt output.

Related edge cases:
- `findOutliers` short-circuits when `items.length <= 1`
  (`packages/measure/src/outlier-helpers.tsx`), so a single-run measurement never
  loses a duration.
- If every post-warmup run is classified an outlier, `durations` is empty while
  `counts` is not. A `.perf` file can legitimately carry `durations: []`.
- `runs` is exactly `counts.length`, so persisting it as its own column
  duplicates derivable data — the same anti-drift argument that rejects
  persisting `meanDuration`/`stdevDuration`.

## 2. Current state of the store

Two storage philosophies already coexist in `src/perf/db/schema.sql`:

- **Store raw, derive at read time** — `measure` keeps one row per occurrence
  (`schema.sql:58-63`) and the `run_metric_summary` view computes nearest-rank
  percentiles from those rows (`schema.sql:97-108`).
- **Store aggregates only** — `system_sample` keeps adapter-computed
  avg/min/peak columns and discards the raw per-sample series
  (`schema.sql:68-78`).

They are differentiated by whether the raw series has a natural aggregation
boundary owned by a single adapter.

Other relevant precedents:

- `run` performs **no dedup** on `git_commit` (`adapters/store_sqlite.py:372-416`).
  Repeated runs on one commit are expected and collapsed at read time by
  `domain.statistics.median_by_commit` (`domain/statistics.py:83`), not blocked at
  write time.
- Dimension tables upsert with `ON CONFLICT DO NOTHING`
  (`adapters/store_sqlite.py:290-327`).
- The CLI has four flat commands plus exactly one sub-app, `markers_app`
  (`cli/main.py:137-141`), created because two subcommands shipped
  simultaneously.
- `cli/commands/compare.py` orchestrates ports directly from the CLI with no
  `application/` use-case (`compare.py:28,53-80`); only `run` and `budget-check`
  have one. The split follows orchestration complexity, not a blanket rule.
- Migrations need zero Python: `SqliteStore._migrate` runs from `__init__`
  (`adapters/store_sqlite.py:196`). Filenames must satisfy
  `_parse_migration_version` (`store_sqlite.py:166`, digits-only prefix), and
  migration files must not set pragmas or bump `user_version` themselves
  (`migrations/0001_init.sql:3-7`). Next number is **0005**.

## 3. Affected areas

- `src/perf/db/migrations/0005_add_reassure_tables.sql` + mirror into `db/schema.sql`
- `src/perf/domain/model.py` — new frozen dataclasses (header, entry, parse result)
- `src/perf/domain/ports.py` — new `ReassureParser` Protocol; new
  `Store.save_reassure_import` method
- `src/perf/adapters/reassure_jsonl.py` — new parsing adapter
- `src/perf/adapters/store_sqlite.py` — `save_reassure_import` + helpers
- `src/perf/adapters/registry.py` — `build_reassure_parser` plain factory
- `src/perf/config/loader.py` — new `reassure_path` field
- `src/perf/cli/commands/reassure_import.py` + `cli/main.py` wiring
- `src/perf/contracts/reassure_import_v1.py`
- `tests/fixtures/reassure_sample.perf`, contract test, parser/store/CLI
  integration tests
- `tests/integration/test_schema.py` (add `MIGRATION_0005`) and
  `tests/integration/test_store_migrations.py` (every `== 4` becomes `== 5`)

## 4. Open questions — positions taken

### 4.1 Table shape

**Position:** store the **raw** series rather than reassure's own aggregates,
mirroring `measure`'s store-raw-derive pattern rather than `system_sample`'s
aggregate-only pattern.

**Revised after §1.1.** The original position said three tables with
`durations[]` and `counts[]` "paired by index" in a single `reassure_sample`
table. That is invalid — the two series are not index-aligned. The sample
storage must keep them **independent**, each with its own ordinal, and no schema
shape may imply a pairing that does not exist. The `(entry_id, series, idx,
value)` EAV shape is ruled out by this codebase's stated position
(`src/perf/db/schema.sql:65`, "Fixed columns > EAV"). The leading candidate is
two sibling fixed-column tables — `reassure_duration_sample(entry_id, idx,
duration_ms)` and `reassure_count_sample(entry_id, idx, render_count)`. Final
shape is `sdd-design`'s call.

Reassure's own `meanDuration` / `stdevDuration` / `meanCount` / `stdevCount` are
deliberately **not** persisted as columns: they are fully derivable, and keeping
them would create a second source of truth.

- Pro: a follow-up compare keeps percentile methodology consistent with
  `domain/statistics.py` and `domain/regression.py`; matches house pattern; no
  drift risk.
- Con: three tables plus indexes instead of one; more migration DDL.
- Effort: medium.

### 4.2 `warmupDurations` / `outlierDurations`

**Position:** persist verbatim as two nullable JSON-text passthrough columns on
`reassure_entry`. Diagnostic only — no typed domain field, no index, no query
surface. Effort: low.

### 4.3 Identity and idempotency

**Position:** `reassure_import.content_hash` — sha256 of the raw file bytes —
`UNIQUE`, with `ON CONFLICT DO NOTHING`. Re-importing a byte-identical file is a
no-op and exits 0.

Deliberately does **not** dedupe by `commitHash`, matching the existing `run`
precedent that allows multiple runs per commit. The header is optional, so
`commitHash` may be absent entirely and cannot serve as the key.

Limitation to state explicitly in the proposal: content-hash idempotency catches
only a byte-identical re-import, not a same-commit re-run with new measurement
noise. That is correct by design.

### 4.4 CLI surface

**Position:** a flat `reassure-import <path>` command, not a `reassure` sub-app.
`markers` got a sub-app only because two subcommands shipped at once
(`cli/main.py:137-141`); a hypothetical future compare or history command does
not justify one now.

Input is a **single file**, not a directory: reassure writes exactly one file per
run. The argument is required, with a config fallback.

Orchestration follows `compare.py`'s direct-port-call shape — no new
`application/` module, because the pipeline is two sequential port calls rather
than a multi-port loop like `run`.

### 4.5 Malformed-input policy

- Bad JSON on a line, missing required field, or unrecognized `type` →
  **skip that line and warn**. Never fatal.
- File not found or not readable → **exit 2** (usage error, mirroring
  `ConfigError`'s exit-2 for a bad `--config` path).
- Zero entries recovered from a readable file → **exit 0**, with a flag in the
  `--json` payload plus a stderr warning.
- Store or transaction failure, or any unexpected exception → **exit 3**.

### 4.6 Config

**Position:** add `reassure_path: str = ".reassure/current.perf"` to
`PerfConfig`. It is **not** run through `_under_base` — it is an input path (like
`flows[].maestro_path`), not a perfvibe output artifact.

### 4.7 Size forecast

| Area | Estimated authored lines |
| --- | --- |
| DDL + schema mirror | ~100 |
| Domain types + port | ~100 |
| Parser adapter | ~150 |
| Store method | ~80 |
| Registry + config | ~20 |
| CLI + wiring | ~100 |
| Contract module | ~35 |
| Tests | ~335 |
| **Total** | **~920** |

That is roughly **2.3x the 400-line review budget**. Chained delivery is required.

## 5. Recommended delivery split

Three slices along the hexagonal boundary the design already creates:

1. **Parsing / domain** (~280-320 lines) — migration + schema mirror, domain
   types, port, adapter, registry factory, fixture, parser tests, contract module
   and contract test.
2. **Persistence** (~220-260 lines) — `Store.save_reassure_import`, the
   `SqliteStore` implementation, store tests, and the two pinned schema-test
   edits.
3. **CLI** (~260-300 lines) — config field, CLI command, `main.py` wiring, CLI
   integration tests.

## 6. Risks

| Risk | Detail |
| --- | --- |
| Review budget | ~920 lines vs a 400-line budget. High risk; needs an explicit chained-PR decision before `sdd-tasks`. The revised two-table sample storage (§4.1) adds a little DDL on top. |
| Something downstream zips `durations` with `counts` | The two series are not index-aligned (§1.1). A compare that zips them would silently mis-attribute render counts to durations, with no error raised. Must be stated as a first-class invariant wherever the sample tables are documented. |
| Noise floor keyed by unit | `domain/calibration.py:203` looks the floor up by *unit*, not by metric (`floors.get(unit, 0.0)`). A future `"count"`-unit reassure compare with no configured floor silently gets `floor=0.0` → false negatives. Not this change's job, but the follow-up must budget for it. |
| Analyzer port is not a clean seam | `adapters/registry.py:221-225` — `build_analyzer` types on the concrete `SqliteStore`, not the `Store` Protocol, because `SqlAnalyzer` calls five methods that exist only there. A follow-up compare adapter reusing `Analyzer` will hit this pre-existing leak. |
| `name` uniqueness unverified | No verified invariant guarantees `name` is unique within one `.perf` file. `reassure_entry` intentionally carries no `UNIQUE(import_id, name)`; any future feature assuming one row per name must defend itself. |

## 7. Verdict

Ready for `sdd-propose`, with one caveat carried forward: the size forecast is
High risk against the 400-line review budget, and the three-slice split should be
agreed before `sdd-tasks` runs.
