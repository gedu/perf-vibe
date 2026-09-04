# reassure-read Specification

## Purpose

Make persisted `@callstack/reassure` data observable: list what was imported,
inspect one import's entries, show one test's latest state, chart one test's
history across imports, and compare one test against its own prior imports —
read-only, for users first and agents second, via a `reassure` sub-app.

## Non-Goals

- No gate (D3): `reassure compare` is show-only and MUST always exit `0`. No
  `reassure budget-check` and no `application/budget_check_flow.py` integration.
- No reassure data joins into the flow world's `compare`/`history`/`run` — reassure
  has no flow/device/mode dimension (see "Architecture decision" in the proposal).
- No read of `reassure_import.kind` in any query, filter, or comparison (locked by
  `0006_add_reassure_import_kind.sql`).
- No component/test-file dimension derivation from `name` (mirrors
  `reassure-ingest`'s own "No Component or Test-File Identity" requirement).
- This capability enters through `Store` read methods and pure `domain/`
  functions, NOT through `Analyzer`. `Analyzer.compare_latest`
  (`domain/ports.py:154`) is flow/device/mode-keyed and reassure has none of
  those dimensions (see the proposal's "Architecture decision" table).
  `openspec/specs/reassure-ingest.md` previously claimed the opposite; that
  sentence was corrected in this change and now reads accurately at its
  lines 16-20.

## Requirements

### Requirement: `reassure` Sub-App Surface With Deprecated Flat Alias (D1)

The system MUST expose a `reassure` sub-app (`cli/main.py`, `add_typer` pattern,
precedent `markers_app` at `cli/main.py:147`) with subcommands `import`, `list`,
`entries`, `show`, `history`, `compare`, `run`. The flat `reassure-import` command
MUST keep working, re-registering the identical function object as
`reassure import`, and MUST be marked `hidden=True` and `deprecated=True` on its
flat registration. The alias MUST be documented as deprecated and kept indefinitely
(no removal version stated).

#### Scenario: Flat alias still works, hidden from `--help`
- GIVEN `perfvibe reassure-import <path>` and `perfvibe reassure import <path>` for
  the same file
- WHEN either is run
- THEN both produce byte-identical `--json` payloads and exit codes, and
  `perfvibe --help` does NOT list `reassure-import` as a top-level command

#### Scenario: Sub-app lists all seven subcommands
- GIVEN `perfvibe reassure --help`
- WHEN invoked
- THEN `import`, `list`, `entries`, `show`, `history`, `compare`, `run` all appear

### Requirement: Chronological Ordering By `created_date`, `commit_hash` As Label Only (D2)

Every read model that orders imports MUST order by `reassure_import.created_date`
ascending/descending as the primary key, falling back to `imported_at` (never
`NULL`) when `created_date` is `NULL`. `commit_hash` MUST NEVER be used as a
grouping key, filter, or join condition anywhere in this capability. One import
MUST always produce exactly one series point — imports MUST NEVER be collapsed by
commit. `statistics.median_by_commit()` (`domain/statistics.py:83`) MUST NEVER be
called on reassure data, in any subcommand.

#### Scenario: Two same-commit, same-branch imports stay two distinct points
- GIVEN two imports of the same `name` where both `commit_hash` and `branch` are
  identical but `created_date` differs (verified real-world case:
  `0006_add_reassure_import_kind.sql`'s baseline/current pair)
- WHEN `reassure history <name>` is run
- THEN the series contains exactly TWO points, one per import — this scenario MUST
  FAIL if `median_by_commit` (or any commit-keyed collapse) is ever introduced

#### Scenario: `created_date` absent falls back to `imported_at`
- GIVEN an import whose header carried no `creationDate` (`created_date IS NULL`)
- WHEN it is ordered against other imports in `list` or `history`
- THEN its position is determined by `imported_at`, never by `NULL`-sorts-first/last
  default database behavior being mistaken for meaning

### Requirement: Independently-Indexed Per-Series Reduction

Every read model that reduces `durations[]` or `counts[]` (p50/p90/n) MUST reduce
each series **separately**. The two series MUST NEVER be zipped, paired, or
truncated to a common length when computing a summary — mirroring
`reassure-ingest`'s "No Cross-Series Index Pairing" requirement, extended here to
reads. `idx` MUST be treated only as an ordinal within its own series.

#### Scenario: Independent counting survives into a read model
- GIVEN an entry with 5 `counts` samples and 3 `durations` samples
- WHEN `reassure show <name>` or `reassure entries <import-id>` reports it
- THEN the duration summary's `n` is `3` and the count summary's `n` is `5`,
  neither forced to match the other

### Requirement: `reassure list` — Import Roster

`reassure list` MUST report, per import, ordered per D2 (most recent first):
an import identifier, `created_date`/`imported_at`, `branch`, `commit_hash` (as a
label), and its entry count. It MUST support `--limit`, defaulting to `50`.

#### Scenario: Default limit matches `history`, not `compare`
- GIVEN more than 50 imports exist and no `--limit` flag is passed
- WHEN `reassure list` runs
- THEN exactly 50 imports are returned, newest-first by D2 ordering

### Requirement: `reassure entries <import-id>` — One Import's Entries

`reassure entries <import-id>` MUST report every `reassure_entry` row for that
import: `name`, `entry_type`, declared `runs`, and each series' `n` (computed per
the independent-reduction requirement above). An unknown `import-id` MUST exit `2`.

#### Scenario: Unknown import id is a usage error
- GIVEN an `import-id` with no matching row
- WHEN `reassure entries <import-id>` runs
- THEN it exits `2` and no `--json` payload is emitted

### Requirement: `reassure show <name>` — Latest Detail With State-Transition Issues (D5, D8)

`reassure show <name>` MUST default to the entry matching `name` in the most recent
import by D2 ordering, and MUST accept `--import <id>` to select a specific import's
entry instead. It MUST report the independent duration/count summaries and the
declared-vs-actual `runs` relationship (mirroring the ingest mismatch it stores,
never repaired). `issues.initialUpdateCount` MUST be rendered as a state transition
against the immediately preceding import that also contains `name` (by D2
ordering), never as a `delta_pct`. A transition from `0` to a value `> 0` MUST be
labeled "extra mount render introduced". `NULL` (diagnostic absent) MUST NEVER be
treated as, or compared against, `0` (diagnostic present, zero).

#### Scenario: Mount-render regression is labeled, not a percentage
- GIVEN two imports for `name`, the earlier with `initialUpdateCount: 0` and the
  later with `initialUpdateCount: 1`
- WHEN `reassure show <name>` reports the later import
- THEN it shows "extra mount render introduced", and no `delta_pct` field is
  computed for this value anywhere in the payload

#### Scenario: NULL is never treated as zero
- GIVEN an entry whose earlier import has `issues` absent (`NULL`) and whose later
  import has `initialUpdateCount: 0`
- WHEN `reassure show <name>` reports the later import
- THEN it reports "no prior diagnostic to compare" rather than a `0 -> 0` or
  `NULL -> 0` transition label

#### Scenario: Unknown name is a usage error
- GIVEN a `name` with no matching entry in any import
- WHEN `reassure show <name>` runs
- THEN it exits `2`

### Requirement: `reassure history <name>` — Full Series

`reassure history <name>` MUST report one point per import containing `name`,
ordered per D2, each carrying independently-reduced duration and count summaries
(p50/p90/n). A `name` present in zero imports MUST exit `2`.

#### Scenario: Coverage gaps do not misattribute data
- GIVEN `name` present in imports A and C but absent from import B
- WHEN `reassure history <name>` runs
- THEN the series has exactly two points (A, C); import B contributes nothing, and
  neither A's nor C's data is shifted into B's position

### Requirement: `reassure compare <name>` — Show-Only Verdict (D3, D7)

`reassure compare <name>` MUST compare the most recent import's values for `name`
against a baseline window of the `baseline_n` prior imports for the same `name`
(same resolved `Config.baseline_n`, default `10`, shared with the flow world), using
`regression.classify()` and `statistics.median/percentile/robust_noise` — never
`median_by_commit`. It MUST always exit `0`, including on a detected regression or
on insufficient baseline data (reported explicitly in the payload, never as a
non-zero exit). `DEFAULT_FLOORS` (`config/loader.py:66`) MUST carry an explicit
`"count": 0.0` entry (rather than relying on `_effective_floor`'s implicit
`.get(unit, 0.0)`), so `render_count`'s floor is visible and user-overridable via
`[floors]` config, not an accidental fallback.

#### Scenario: Regression still exits 0
- GIVEN a `name` whose latest `render_count` regressed beyond `threshold_pct`
  against its baseline window
- WHEN `reassure compare <name>` runs
- THEN the payload reports the regression verdict and the command exits `0`

#### Scenario: Insufficient baseline data exits 0
- GIVEN `name` present in only one import (no baseline window)
- WHEN `reassure compare <name>` runs
- THEN the payload reports an explicit insufficient-data state and exits `0`, never
  a non-zero code

### Requirement: `reassure run` — Composes The Existing Ingest Path (D6)

`reassure run` MUST resolve the configured `reassure_path`
(`DEFAULT_REASSURE_PATH`, `config/loader.py:80`, or its config override) and invoke
the SAME import logic `reassure import <path>` uses, with no new persistence path.
Because it performs literally the same operation, `reassure run` MUST emit the
SAME `reassure_import_v1` payload (post-D4, `SCHEMA_VERSION = 3`) rather than a new
contract — a `reassure_run_v1` module would duplicate every field of
`reassure_import_v1` with zero new data, which the no-second-source-of-truth rule
already forbids. It MUST inherit `reassure import`'s exit-code discipline exactly
(`2` on missing/unreadable resolved path, `3` on store/transaction failure, `0`
otherwise). `reassure run` MUST be the last subcommand delivered (D6): it depends
on ingest's D4 change and adds no new read model of its own.

#### Scenario: `run` and `import` are behaviorally identical on the same file
- GIVEN `reassure_path` resolves to a file identical to one passed explicitly to
  `reassure import`
- WHEN `reassure run` and `reassure import <path>` are each run against a fresh
  database
- THEN both produce the same `reassure_import_v1` payload shape and the same exit
  code

### Requirement: `perfvibe init` Scaffolds `reassure_path`

The `init` wizard MUST include a `reassure` block that prompts for, or scaffolds
with, a `reassure_path` value (defaulting to `DEFAULT_REASSURE_PATH`) in the
generated `perfvibe.toml`.

#### Scenario: Fresh init includes a reassure_path entry
- GIVEN `perfvibe init <flows-dir>` on a directory with no existing config
- WHEN the wizard completes
- THEN the generated `perfvibe.toml` contains a `reassure_path` entry

### Requirement: Exit-Code Discipline Across All Subcommands

| Subcommand | `0` | `2` | `3` |
|---|---|---|---|
| `list` | always (empty roster is valid) | bad flags | DB failure |
| `entries` | valid import-id | unknown import-id, bad flags | DB failure |
| `show` | valid name (any import state) | unknown name, bad flags | DB failure |
| `history` | valid name (any coverage) | unknown name, bad flags | DB failure |
| `compare` | always (regression or insufficient data alike) | unknown name, bad flags | DB failure |
| `run` | successful import (any outcome) | resolved path missing/unreadable | store/transaction failure |

Exit `1` MUST NEVER be used by any `reassure` subcommand.

#### Scenario: No subcommand ever exits 1
- GIVEN any combination of valid/invalid input across all seven subcommands
- WHEN each is exercised
- THEN none of them ever exits `1` — that code stays exclusive to `budget-check`,
  which reassure does not implement (D3)

### Requirement: Five New `--json` Contracts, No Second Source Of Truth

`list`, `entries`, `show`, `history`, and `compare` MUST each ship their own
`contracts/reassure_<cmd>_v1.py` (module-level `SCHEMA_VERSION`, one pure
`build_<cmd>_payload(**kwargs) -> dict` builder calling no port) and its own
`tests/contract/test_reassure_<cmd>_v1_contract.py` pinning the exact key set, the
exact key count, "must NOT exist" negatives for any derivable field, JSON
round-trip losslessness, and a version-bump guard. No payload MUST carry a field
mechanically derivable from other fields already in the same payload (the rule
`reassure_import_v1` already enforces for `zero_entries`/`samples_imported`).
Stdout MUST stay byte-pure under `--json`; all warnings go to stderr only.

#### Scenario: Every new payload carries schema_version
- GIVEN any of `list`/`entries`/`show`/`history`/`compare` run with `--json`
- WHEN the payload is inspected
- THEN it carries a `schema_version` key and no key derivable from its siblings

### Requirement: Documentation Coverage

| Doc | Requirement |
|---|---|
| `README.md` | The "six commands" table MUST be corrected and MUST show `reassure` as exactly ONE row (mirroring how `markers` is presented as one row for two subcommands), not one row per subcommand |
| `docs/commands.md` | MUST document all seven `reassure` subcommands: flags, exit codes, `--json` payload shape |
| `docs/configuring-flows.md` | MUST document `reassure_path` (currently undocumented anywhere) |
| `docs/baselines-and-history.md` | MUST explicitly CONTRAST reassure's per-import baseline (D2: one import = one point) against the flow world's per-commit baseline — not merely append a section, since a reader who assumes shared semantics will misread `reassure compare` |
| `AGENTS.md`, `CLAUDE.md` | MUST record the agent-facing `--json`-only contract for all `reassure` subcommands |

Documentation for each subcommand MUST ship in the same slice that introduces it,
not as a trailing chore.

#### Scenario: reassure is discoverable by both audiences
- GIVEN `rg -i reassure README.md docs/ AGENTS.md CLAUDE.md` after this change
- WHEN run
- THEN matches exist in every one of those files, and `README.md`'s command table
  shows `reassure` as exactly one row
