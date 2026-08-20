# reassure-ingest Specification

## Purpose

Parse a `@callstack/reassure` `.perf` JSON-Lines file, persist it idempotently into
dedicated tables (precedent: `adapters/store_sqlite.py:372-416` no-dedup-by-commit
ingestion), and report the outcome via `perfvibe reassure-import <path>` — a flat CLI
command calling ports directly, `cli/commands/compare.py:28,53-80`-style. This
capability stores data and prints a confirmation only; it judges, compares, and gates
nothing.

## Non-Goals

Comparing reassure runs against history; history/trend views; filtering by name,
component, or test file; budget gating on reassure metrics. Each is a distinct
follow-up change entering through `Analyzer` (compare/history) or
`application/budget_check_flow.py` (gating).

## Requirements

### Requirement: JSON-Lines Parsing With Optional Header

The system MUST parse the input as JSON Lines. IF the first line parses as a JSON
object with a top-level `metadata` key, it MUST be treated as the header and MUST NOT
count as an entry; `branch`, `commitHash`, and `creationDate` inside `metadata` are each
independently optional, and no downstream behavior MUST depend on `commitHash` being
present. A file with no header line MUST parse identically to one with an empty header.
Every other line is a measurement entry with fields exactly: `name`, `type`, `runs`,
`meanDuration`, `stdevDuration`, `durations`, `warmupDurations`, `outlierDurations`,
`meanCount`, `stdevCount`, `counts`, `issues`. `type` MUST default to `'render'` when
absent.

#### Scenario: Header absent, or present with a field subset
- GIVEN either (a) a file whose first line is already a measurement entry, or (b) a
  header `{"metadata": {"branch": "main"}}` with no `commitHash`/`creationDate`
- WHEN imported
- THEN parsing proceeds with no error; a present header is not counted as an entry

#### Scenario: type defaults to render
- GIVEN an entry line with no `type` key
- WHEN imported
- THEN the entry is treated as `type: 'render'`

### Requirement: Malformed-Line Tolerance

A line MUST be skipped (with a per-line stderr warning) and MUST NEVER be fatal to the
import when: it is not valid JSON; `name` is missing or not a string; `durations` is
missing or not an array (an EMPTY array `[]` is valid and MUST NOT cause a skip —
"required" means present-and-correctly-typed, not non-empty; a `.perf` file legitimately
carries `durations: []` when every post-warmup run is classified an outlier); `counts`
is missing or not an array (an empty array is likewise valid, though an empty `counts`
is not expected in practice); or `type` is present but not one of `'render' | 'function'
| 'async function'`. `runs`, `meanDuration`, `stdevDuration`, `meanCount`, `stdevCount`,
`warmupDurations`, `outlierDurations`, and `issues` are never required — their absence
never causes a skip. `counts` being present for `'function'`/`'async function'` entries
is relied upon from reassure's own zod schema (`packages/compare/src/type-schemas.ts`,
which lists `counts` as required for all three `type` values), not from an observed
real sample of those two types.

#### Scenario: Mixed-quality file imports every good line
- GIVEN a file with one invalid-JSON line, one line missing `durations`, one line with
  `type: "mount"`, and three valid entries
- WHEN imported
- THEN the three valid entries are persisted, three warnings are printed to stderr (one
  per bad line), and the command exits `0`

### Requirement: Independently-Indexed Sample Persistence

`durations[]` and `counts[]` MUST be persisted as two SEPARATELY indexed series, each
keeping its own ordinal — never as index-aligned pairs. `durations` is built from the
outlier-**filtered** set and `counts` from the **unfiltered** set (`removeOutliers`
defaults to `true`), so `durations.length` MAY be less than `counts.length`, and index
`i` of one does NOT refer to the same run as index `i` of the other. An entry with
`durations: []` MUST still be persisted, with zero duration samples, never skipped —
its `counts` series remains valid data. `meanDuration`, `stdevDuration`, `meanCount`,
and `stdevCount` MUST NOT be persisted: they are *statistics*, recomputed from the raw
arrays by the canonical methodology (`domain/statistics.py`), and a stored copy would
drift against that computation — the same reason the store never re-derives a
percentile from a cached mean.

`runs` MUST be persisted as its own column, unlike the four statistics above. `runs` is
not a statistic — it is a DECLARED CARDINALITY that no percentile path ever recomputes,
the same concept as `run.iterations` (`src/perf/db/schema.sql:38`, `"to detect partial
coverage (n < iterations)"`). Storing it is what makes a mismatch between the declared
`runs` and the actual sample counts DETECTABLE; deriving it as `COUNT(*)` over the
persisted samples would make a truncated or hand-edited `.perf` file indistinguishable
from a complete one. The whole import MUST run in a single transaction; any store or
unexpected failure MUST roll back the ENTIRE import, leaving zero rows.

#### Scenario: Empty durations still persists the entry
- GIVEN an entry with `durations: []` and `counts: [4, 5, 6]` (every post-warmup run was
  an outlier)
- WHEN imported
- THEN the entry is persisted with zero duration samples and three count samples; the
  entry is NOT skipped

#### Scenario: Declared `runs` and actual sample count may mismatch, and that is recorded
- GIVEN an entry declaring `runs: 10` but carrying only 3 values in `counts`
- WHEN imported
- THEN `runs = 10` is persisted alongside exactly 3 count-sample rows; the mismatch is
  recorded verbatim, never silently repaired (e.g. padded to 10, or `runs` rewritten to
  3), and never causes the line to be skipped — this change stores data, it does not
  judge it. Whether anything warns about the mismatch is OUT OF SCOPE for ingest.

#### Scenario: Failure leaves zero rows
- GIVEN a forced store failure partway through an otherwise-valid import
- WHEN the command runs
- THEN it exits `3`, and a subsequent import of the same file is treated as fresh (not
  `already_imported`), proving nothing committed

### Requirement: No Cross-Series Index Pairing

The system MUST NOT zip, pair, align, truncate, or pad `durations[]` and `counts[]` to a
common length, at parse time or at persistence time. Index `i` of `durations` MUST NOT
be treated, stored, or read as referring to the same run as index `i` of `counts`. This
is the same family of trap as the component-identity one above: a plausible-looking
assumption that would silently corrupt data with no exception raised.

#### Scenario: Differently-sized series persist at their own true lengths
- GIVEN a fixture entry with `counts` of length 5 and `durations` of length 3 (two
  outliers removed)
- WHEN imported and the persisted samples are read back through the store
- THEN exactly 5 count samples and exactly 3 duration samples exist, at their own
  ordinals, with neither series truncated, padded, or zipped to match the other's
  length

### Requirement: Diagnostic Passthrough Fields

`warmupDurations` and `outlierDurations`, when present, MUST be persisted verbatim and
MUST NOT be exposed through the `reassure_import_v1` payload or any query/filter
surface introduced by this change.

#### Scenario: Passthrough fields are not in the reported payload
- GIVEN an entry with `warmupDurations` and `outlierDurations` present
- WHEN imported with `--json`
- THEN neither key appears anywhere in the `reassure_import_v1` payload

### Requirement: Content-Hash Idempotency

The system MUST compute a sha256 hash of the raw file bytes and MUST insert zero rows
when a byte-identical file was already imported (`ON CONFLICT DO NOTHING` on a unique
hash). A duplicate import MUST still exit `0`.

#### Scenario: Re-importing an identical file is a no-op
- GIVEN a file already imported successfully
- WHEN the identical file is imported again
- THEN it exits `0`, `already_imported` is `true`, and `entries_imported`,
  `duration_samples_imported`, and `count_samples_imported` are all `0`

#### Scenario: A byte-different file is a fresh import
- GIVEN a file whose bytes differ from any prior import (even by one appended line)
- WHEN imported
- THEN `already_imported` is `false` and imported counts are `> 0`

### Requirement: Exit-Code Discipline

| Condition | Exit |
|---|---|
| Path missing or unreadable | `2` |
| Readable file yields zero valid entries | `0`, `entries_imported: 0` + `already_imported: false` in the payload, stderr warning |
| Store/transaction failure or unexpected exception | `3` |
| Skipped bad lines with ≥1 valid entry imported | `0` |
| Duplicate (byte-identical) re-import | `0` |

Exit `1` MUST NEVER be used. Skipped bad lines alone MUST NEVER change the exit code.
The "payload flag" for the zero-entries case is `entries_imported == 0 AND
already_imported == false` — no dedicated boolean key is introduced for it (see the
`--json` contract requirement below for why).

#### Scenario: Missing file is a usage error
- GIVEN a path that does not exist
- WHEN `perfvibe reassure-import <path>` runs
- THEN it exits `2` and no `--json` payload is emitted

#### Scenario: Zero recovered entries still exits 0
- GIVEN a readable file where every line is malformed
- WHEN imported
- THEN it exits `0`, the payload shows `entries_imported: 0` and `already_imported:
  false`, and stderr carries a warning

### Requirement: reassure_import_v1 --json Contract

`--json` output MUST be `reassure_import_v1` with top-level keys EXACTLY:
`schema_version`, `path`, `content_hash`, `already_imported`, `entries_imported`,
`entries_skipped`, `duration_samples_imported`, `count_samples_imported`. There MUST be
no `samples_imported` key — one count cannot describe two independently-sized series,
so the payload reports each series separately. There MUST be no `zero_entries` key
either: that state is `entries_imported == 0 AND already_imported == false`, fully
derivable from two fields already in the payload — a dedicated boolean would repeat the
exact second-source-of-truth problem this change already rejects for the statistics
fields (`meanDuration`/`stdevDuration`/`meanCount`/`stdevCount`). No other top-level key
MUST exist; no field may be added or
removed without a `schema_version` bump. Stdout MUST stay byte-pure under `--json` —
all warnings go to stderr only.

#### Scenario: Exact key set, series counted independently
- GIVEN any successful `reassure-import --json` invocation of one entry with
  `durations: [10, 12]` (length 2) and `counts: [1, 1, 1]` (length 3)
- WHEN the payload is inspected
- THEN its top-level keys are exactly the eight listed above, no more, no fewer, and
  `duration_samples_imported` is `2` while `count_samples_imported` is `3` — neither is
  forced to match the other

#### Scenario: Zero-entries signal is derived, not a dedicated key
- GIVEN a readable file where every line is malformed
- WHEN imported with `--json`
- THEN the payload shows `entries_imported: 0` and `already_imported: false`, and no
  `zero_entries` key exists anywhere in the payload

#### Scenario: Stdout carries only the payload
- GIVEN a `.perf` file with skipped bad lines
- WHEN imported with `--json`
- THEN stdout is exactly the JSON payload and every warning appears on stderr instead

### Requirement: No Component or Test-File Identity

The reassure format has NO component field and NO test-file field. The system MUST
treat `name` (Jest's `describe > test` chain) as the SOLE identity — "by name" and "by
test" refer to the exact same string. No column, `--json` key, or CLI flag introduced
by this change MUST represent a derived component or test-file dimension as if it were
stored data.

#### Scenario: Name is reported verbatim, nothing else
- GIVEN an entry named `"Login screen > renders correctly"`
- WHEN imported and reported
- THEN it is persisted and identified by that exact string, with no separate
  component/test-file field anywhere in the schema or payload

