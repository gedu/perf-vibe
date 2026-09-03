```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:7467b1b97c103332a2034f822b37f1f373b3d3ad5486ef781dafdb378f7eebe3
verdict: fail
blockers: 2
critical_findings: 2
requirements: 12/15
scenarios: 23/24
test_command: ./.venv/bin/pytest -q --cov=perf
test_exit_code: 0
test_output_hash: sha256:2c7058ab578c39b037f97564179a76a226d85914f4f1ecbbdfbce6623d232f58
build_command: ./.venv/bin/mypy src/perf
build_exit_code: 0
build_output_hash: sha256:25499469eab292b707aaafb426bb37ffc20ebb6f29a73f5d9ab147221e899cc4
```

# Verification Report: reassure-read

**Change**: `reassure-read` · **Mode**: full spec verification (proposal + spec + design +
tasks all present) · **Verdict**: **FAIL** · **Artifact store**: hybrid

Verified read-only against worktree `perf-vibe-worktrees/reassure-read`, branch
`reassure-read/slice5-run`, tip `c4d9ff1`, tree clean. Base `main` at `d0149c8`. Fourteen
commits. Nothing was modified; the only write is this report.

Every number below was re-measured, not taken from the apply reports. Every runtime claim
was produced by invoking the real `perfvibe` binary against throwaway SQLite databases with
fixtures written for this verification, not the implementers' scenarios.

**The verdict is FAIL on two CRITICAL findings.** Both are narrow, both are real, and
neither is in the invariants the change spent its design budget on: those hold. The
implementation quality is high; the failures are at the two edges nobody re-read as a
whole, namely `reassure run`'s unguarded subprocess spawn and the agent-facing docs that
never learned `run` exists.

---

## 1. Gate Evidence (re-measured, verbatim)

| Gate | Command | Exit | Output |
|---|---|---|---|
| Lint | `./.venv/bin/ruff check .` | `0` | `All checks passed!` |
| Format | `./.venv/bin/ruff format --check .` | `0` | `173 files already formatted` |
| Types | `./.venv/bin/mypy src/perf` | `0` | `Success: no issues found in 73 source files` |
| Tests | `./.venv/bin/pytest -q --cov=perf` | `0` | `1390 passed in 9.42s` · `Required test coverage of 93.0% reached. Total coverage: 95.57%` |

The reported 1390 / 95.57% / floor 93 are accurate. No hang was observed; the suite ran in
under 10 s on both invocations.

Diff shape: 85 files, 11 422 insertions / 55 deletions overall; `src/` alone is 22 files,
2 916 insertions / 23 deletions.

---

## 2. Task Completion

All checkboxes across PR0 through PR5 in `docs/specs/reassure-read/tasks.md` are `[x]`.
Working tree is clean and PR5 is committed as `c4d9ff1`. The Engram `apply-progress` record
still describes PR5 as "uncommitted, branch `reassure-read/slice5-run`", which is now stale
but harmless.

Task text matches code state everywhere I sampled, including the deviations the tasks
themselves flagged: 1c.4's fourth store method, 2a.3b/2a.3c's unplanned RED tests, 4a.2's
AST-rather-than-substring guard, 4b.6's `d5_sentence` promotion, and PR2b's split into
`slice2b-show-logic` / `slice2b-show-cli`.

---

## 3. Requirement and Scenario Compliance

15 requirements, 24 `#### Scenario:` blocks (13 requirements / 18 scenarios in the new
`reassure-read` capability; 2 requirements / 6 scenarios in the `reassure-ingest` delta).

### New capability: `reassure-read`

| Requirement | Status | Evidence |
|---|---|---|
| Sub-App Surface With Deprecated Flat Alias (D1) | **PASS** | `perfvibe reassure --help` lists all seven; `perfvibe --help` omits `reassure-import`; the flat form emits `DeprecationWarning: The command 'reassure-import' is deprecated. use ...` on **stderr** only; `diff` of the two `--json` payloads is byte-identical. `src/perf/cli/main.py:139-159`, `src/perf/cli/commands/reassure.py:608-616`. Tests: `tests/integration/test_cli_reassure_deprecation.py` |
| Chronological Ordering by `created_date` (D2) | **PASS** | See section 4, I2 |
| Independently-Indexed Per-Series Reduction (I1) | **PASS** | See section 4, I1 |
| `reassure list` Import Roster | **PASS** | `--limit` default 50 (`reassure.py:162-164`, `test_default_limit_is_50`); roster carries `import_id`/`created_date`/`imported_at`/`branch`/`commit_hash`/`entry_count`; empty roster exits `0`. One `--limit` gap, see W-4 |
| `reassure entries <import-id>` | **PASS** | Unknown id exits `2` with no payload (verified: `entries 999` produced `Error: no reassure import with id 999`, exit 2). A real import with zero entries exits `0` with `[]`. Independent `n`s confirmed |
| `reassure show <name>` (D5, D8) | **PASS** | All five D5 states exercised end to end against a live DB (section 4). A14 no-walk-back verified: a name absent from the newest import exits `2`, never a silent fallback |
| `reassure history <name>` | **PASS** | Coverage-gap and same-commit cases verified live (section 4); unknown name exits `2` |
| `reassure compare <name>` (D3, D7) | **PASS** (with W-1, W-2) | Confirmed regression (+800.0% duration, +600.0% render_count) exits `0`. Insufficient-data exits `0`. `DEFAULT_FLOORS["count"] = 0.0` present and surfaced in the payload as `"floor": 0.0`. `median_by_commit` never reached |
| `reassure run` (D6) | **FAIL** | Payload/exit-code equivalence with `import` verified byte-identical; noisy-child stdout purity verified. **But the requirement's "MUST inherit `reassure import`'s exit-code discipline exactly (2/3/0)" is violated, see C-1** |
| `perfvibe init` Scaffolds `reassure_path` | **PASS** | A fresh `perfvibe init flows --yes` on an empty directory wrote `reassure_path = '.reassure/current.perf'` and `reassure_command = ['npx', 'reassure']` |
| Exit-Code Discipline Across All Subcommands | **FAIL** | See C-1 |
| Five New `--json` Contracts, No Second Source Of Truth | **PASS** (with W-2) | All five `contracts/reassure_*_v1.py` exist with module-level `SCHEMA_VERSION` and one pure builder calling no port. All five contract tests pin the exact key set and exact count, carry a `json.dumps`/`json.loads` round-trip, carry negatives, and carry a `test_contract_rejects_a_shape_change_without_version_bump`. Stdout byte-purity verified on every subcommand including `run` |
| Documentation Coverage | **FAIL** | See C-2 and section 6 |

### Delta: `reassure-ingest`

| Requirement | Status | Evidence |
|---|---|---|
| `reassure_import_v1` --json Contract (11 keys, v3) | **PASS** | Live payload has exactly 11 keys and `schema_version: 3`. `openspec/specs/reassure-ingest.md:260-261,285,297` carries the amendment verbatim; the inaccurate "enters through `Analyzer`" sentence is corrected at `:16-20` |
| Duplicate Entry-Name Detection And Dropping (D4) | **PASS** | See the scenario table below |

### Scenario matrix

23 of 24 scenarios have a passing covering test **and** were independently reproduced at
runtime where reproducible.

| # | Scenario | Result | How verified |
|---|---|---|---|
| 1 | Flat alias still works, hidden from `--help` | PASS | Live CLI plus `test_cli_reassure_deprecation.py` |
| 2 | Sub-app lists all seven subcommands | PASS | `perfvibe reassure --help` |
| 3 | Two same-commit, same-branch imports stay two points | PASS | Live: two imports with identical `commit_hash: same01` **and** identical `created_date`; `history` returned two points carrying their own distinct values (p90 100.0 and 200.0) |
| 4 | `created_date` absent falls back to `imported_at` | PASS | Live: a header-less import produced `created_date: null`, `ordering_key: "imported_at"`, ordered by `imported_at` |
| 5 | Independent counting survives into a read model | PASS | Live: `show "Alpha renders"` gave `duration.n = 2`, `count.n = 3` |
| 6 | Default limit matches `history`, not `compare` | PASS | `test_default_limit_is_50` |
| 7 | Unknown import id is a usage error | PASS | Live: exit `2`, no payload |
| 8 | Mount-render regression is labeled, not a percentage | PASS | Live: `extra mount render introduced (0 -> 2)`; no `*_pct` key for the update count in any payload |
| 9 | NULL is never treated as zero | PASS | Live plus `test_unknown_never_conflated_with_unchanged_zero`. See section 4 |
| 10 | Unknown name is a usage error (`show`) | PASS | Live: exit `2` |
| 11 | Coverage gaps do not misattribute data | PASS | Live: name in A and C, absent from B, gave exactly 2 points with no shift |
| 12 | Regression still exits `0` | PASS | Live: two REGRESSION verdicts, exit `0` |
| 13 | Insufficient baseline data exits `0` | PASS | Live: exit `0` with an explicit `insufficient-data` state |
| 14 | `run` and `import` are behaviorally identical | PASS | Live: `diff` of the two `--json` payloads is identical, both exit `0` |
| 15 | Fresh init includes a `reassure_path` entry | PASS | Live |
| **16** | **No subcommand ever exits `1`** | **FAIL** | **`reassure run` exits `1` with a traceback when the configured command is missing or non-executable. See C-1** |
| 17 | Every new payload carries `schema_version` | PASS | All five payloads inspected live |
| 18 | reassure is discoverable by both audiences | PASS | `rg -ic reassure` matches in README.md (2), CLAUDE.md (4), AGENTS.md (8) and every `docs/` file; README's table shows `reassure` as exactly one row. The scenario's literal check passes; the requirement table it belongs to does not, see C-2 |
| 19 | Exact key set, series counted independently (ingest) | PASS | Live: 11 keys, with `duration_samples_imported` and `count_samples_imported` independent on an asymmetric fixture |
| 20 | Zero-entries signal is derived, not a key | PASS | No `zero_entries` or `samples_imported` key in any payload |
| 21 | Stdout carries only the payload | PASS | Live on all seven, including `run` with a deliberately noisy child |
| 22 | Duplicate drops have their own key | PASS | Live: 2 malformed lines plus 3 copies of `"X"` gave `entries_skipped: 2`, `entries_dropped_duplicate_name: 3`, `entries_imported: 1` |
| 23 | All copies of a duplicated name are dropped | PASS | Live: zero `X` rows persisted; exactly one stderr warning `duplicate name "X": all 3 entries dropped`; exit `0` |
| 24 | Non-duplicated entries in the same file are unaffected | PASS | Live: `Uniq1` survived intact |

---

## 4. The Load-Bearing Invariants

These are what the change is about. All four hold. I attacked each rather than reading it.

### I1 - never zip `durations` and `counts`

**Holds, structurally.**

- **No read model carries a raw sample array.** Runtime field inspection of all three:
  `ReassureImportRow` (9 scalar fields), `ReassureEntryRow` (`duration`/`count` are
  `HistoryMetric | None`, nothing else array-shaped), `ReassureSeriesPoint`
  (`entry: ReassureEntryRow` plus scalars). There is nothing to zip.
- **No SQL joins the two sample tables.** A programmatic scan of every SQL literal in
  `store_sqlite.py` found zero statements naming both `reassure_duration_sample` and
  `reassure_count_sample`. The only text containing both is the `reassure_entries`
  docstring saying they are never joined. Two separate SELECTs at `:570`/`:578`
  (`reassure_entries`) and `:665`/`:673` (`reassure_series`).
- **Query-count trace hooks assert it.** `tests/integration/test_store_reassure_read.py:152-205`
  installs a real `sqlite3.set_trace_callback` and asserts `len(queries) == 2` and `== 3`,
  plus a no-join assertion over the executed SQL text.
- **No view pairs them.** `reassure entries` renders DUR P50 / DUR P90 / DUR N / CNT P50 /
  CNT P90 / CNT N as six separate columns; `reassure history` renders two independent
  sections, each with its own chart, sparkline and table.
- **Runtime proof:** an entry with 2 durations and 3 counts reported `duration.n = 2` and
  `count.n = 3`. An entry with both series empty reported `duration: null, count: null`,
  not zero-valued metrics.

### I2 - never `median_by_commit`

**Both guards exist and both were mutation-proven to fail if the bug were introduced.**

- **Guard 1 (behavioural)** is
  `test_two_points_sharing_commit_and_branch_each_contribute_to_plain_median`
  (`tests/unit/test_reassure_compare.py:170-196`). **The fixture does discriminate.** It
  contains same-commit imports (`aaa1111` twice) and I verified the arithmetic against the
  real functions: `statistics.median_by_commit` over the fixture's pairs yields
  `{aaa1111: 15.0, bbb2222: 20.0}`, whose median is 17.5, while `statistics.median([5,25,20])`
  is 20.0. The test asserts `baseline_value == 20.0`, so the collapsed value fails it.
- **Guard 2 (AST)** is `test_module_never_imports_or_calls_median_by_commit` (`:199-237`).
  I replayed its exact logic against two mutated copies of the module source, one an
  attribute-call mutation and one an import mutation. Both were caught; the real source
  passes. The correction from a substring check to an AST check is right and the reasoning
  in its docstring is accurate.
- **Runtime proof:** two imports sharing `commit_hash` and `created_date` stayed two
  distinct points in `history`, each keeping its own value.
- **Scope caveat:** see W-3. Guard 2 covers `reassure_compare.py` only, while the spec says
  "in any subcommand".

### `None` is not `0`

**Holds end to end.** Traced DB to domain to both views:

- The store reads `issues_initial_update_count` straight through
  (`store_sqlite.py:592-601`, `:697-708`); nullable column, nullable field.
- `derive_update_count_change` (`reassure_compare.py:101`) branches on `is None` first,
  never on falsiness. A repo-wide grep for `if not baseline`, `if not latest` and
  `if not initial_update_count` in `src/perf/` returns zero hits.
- Payload builders emit the raw `int | None`; the `json_reporter` sanitizer only touches
  non-finite floats.
- Pretty view: `d5_sentence` (`reassure_show_pretty.py:84-108`) returns the
  "diagnostics unavailable" line for `unknown` and `None` for the omitted both-zero case.
  They are different code paths.
- **All five states reproduced live** against a purpose-built DB: `introduced` (0 to 2),
  `resolved` (1 to 0), `changed` (1 to 2), `unchanged` non-zero (2 to 2, rendering
  `mount render count unchanged (2)`), and `unknown` (rendering
  `mount render diagnostics unavailable (not measured in one of the two imports)`).
  The `unchanged`-both-zero case emits no line at all.
- `test_reassure_show_pretty_golden.py` has 20 tests, 14 of them independent behavioural
  assertions rather than golden comparisons, including per-state exact-wording checks and
  `test_unknown_never_conflated_with_unchanged_zero`.

### Exit-code discipline - **VIOLATED for `reassure run`**

I swept every subcommand against an empty DB, a corrupt DB file, a DB path that is a
directory, an unreadable (mode 000) DB, oversized integers, and malformed flags:

```
list (empty db)                    exit=0     entries 1 (empty db)     exit=2
show X (empty db)                  exit=2     history X (empty db)     exit=2
compare X (empty db)               exit=2
list/entries/show/history/compare/import (corrupt db)      exit=3 (all)
list (db=/tmp, a directory)        exit=3     list (unreadable db)     exit=3
show --import notanint             exit=2     import /nonexistent.perf exit=2
import <a directory>               exit=2     list --limit abc         exit=2
```

Six of seven subcommands hold 0/2/3 under everything I could throw at them.
`reassure run` does not. See C-1.

### `--json` stdout byte-purity

**Holds, including `reassure run`.** A child emitting five deliberately hostile lines
(plain text, a JSON-shaped object, an unmatched opening brace, a warning, and `Done.`)
produced stdout that `json.load` parsed as exactly one 11-key object, with all five noisy
lines on stderr verbatim. Every error path emits to stderr and leaves stdout empty.

---

## 5. CRITICAL Findings - these block archive

### C-1 - `reassure run` exits `1` with an unhandled traceback when the configured command is not runnable

**This is the change's own headline invariant failing on its most likely first-run path.**

`reassure_run` (`src/perf/cli/commands/reassure.py:558-605`) calls `run_reassure(...)` at
`:586` with no try/except. `run_reassure` (`:215`) calls
`SubprocessRunner.run_streamed`, which calls `subprocess.Popen`
(`adapters/process.py:187`). `Popen` raises `FileNotFoundError` when the binary does not
exist and `PermissionError` when it is not executable. Neither is caught anywhere;
`cli/main.py` installs no global handler, so Typer/Click let the exception escape and the
process exits `1` after printing a rich traceback.

Reproduced against the real binary:

```
# perfvibe.toml: reassure_command = ["definitely-not-a-real-binary-xyz"]
$ perfvibe --json reassure run
exit=1
FileNotFoundError: [Errno 2] No such file or directory: 'definitely-not-a-real-binary-xyz'

# perfvibe.toml: reassure_command = ["./notexec.sh"]   (mode 644)
$ perfvibe --json reassure run
exit=1
PermissionError: [Errno 13] Permission denied: './notexec.sh'
```

Every sibling command guards this correctly (`emit_error` then exit `3`); `reassure_run`
is the only one that does not.

**Why this matters more than an edge case:** the default `reassure_command` is
`("npx", "reassure")`, and `perfvibe init` writes that default into every generated
`perfvibe.toml`. On any machine without Node on PATH, the very first
`perfvibe reassure run` a user or CI job ever executes hits this. The correct code is `3`
("runtime/tooling failure"); `reassure run`'s own requirement demands it, and the
neighbouring "subprocess exited non-zero" branch at `:591-599` already does exactly that.

**Requirements violated:**

- "Exit-Code Discipline Across All Subcommands": *"Exit `1` MUST NEVER be used by any
  `reassure` subcommand."*
- Scenario "No subcommand ever exits 1": *"GIVEN any combination of valid/invalid input
  across all seven subcommands ... THEN none of them ever exits `1`."*
- "`reassure run` - Composes The Existing Ingest Path (D6)": *"MUST inherit `reassure
  import`'s exit-code discipline exactly (`2` on missing/unreadable resolved path, `3` on
  store/transaction failure, `0` otherwise)."*

**Why no test caught it.** `tests/integration/test_cli_reassure_run.py:218-226`
(`test_exit_1_never_appears_anywhere_in_this_suite`) monkeypatches
`RealSubprocessRunner.run_streamed` with a fake returning `returncode=1`. By replacing the
method it can never reach `Popen`, so the only way the spawn can fail is structurally
unreachable from that test. The test's name promises coverage of the scenario; its
mechanism excludes the one case that breaks it. Every other `run` test either monkeypatches
`run_streamed` or uses `sys.executable`, which always exists.

**It also makes the shipped documentation false.** `docs/commands.md:599` states *"Like
every other command, `reassure` never exits `1`."* `AGENTS.md:34-35` states *"Same
exit-code discipline as above: 0/2/3 only, never 1."* An agent that trusts that contract
will misclassify a missing-toolchain failure.

### C-2 - `reassure run` is absent from the agent-facing contract in `AGENTS.md` and `CLAUDE.md`

The Documentation Coverage requirement states: *"`AGENTS.md`, `CLAUDE.md` | MUST record the
agent-facing `--json`-only contract for **all** `reassure` subcommands."*

`rg -n "reassure run|reassure_command|reassure_path" AGENTS.md CLAUDE.md README.md`
returns **zero matches in all three files**. Both agent contracts enumerate exactly six
subcommands:

- `AGENTS.md:31` reads `perfvibe reassure import|list|entries|show|history|compare`
- `CLAUDE.md:20` reads `perfvibe reassure import|list|entries|show|history|compare`

`run` is the seventh, and it is the one with the most agent-relevant hazards: it is the
only subcommand that spawns an external process, the only one where child stdout could
corrupt the `--json` contract, and per C-1 the only one that can currently exit `1`. The
two files that exist specifically to keep an agent from misreading this CLI are the two
that never learned it shipped.

PR5's own task list (`tasks.md:574`, task 5.9) names only `docs/commands.md` and
`docs/configuring-flows.md`. The spec's "Documentation for each subcommand MUST ship in the
same slice that introduces it" rule was followed for those two files and silently skipped
for the `AGENTS.md`/`CLAUDE.md` row. This is the same omission PR1c caught and corrected
for slices 1b/1c (task 1c.9), recurring at the end of the chain with nobody left to catch
it.

---

## 6. Documentation Assessment (verified as a requirement, not a footnote)

The stated goal was "usable by users first, agents second, and fully documented." Judged
against that:

**What is genuinely good, and better than the requirement asked for:**

- `docs/commands.md` documents all seven subcommands with dedicated sections (`:289`
  import, `:314` list, `:340` entries, `:372` show, `:429` history, `:493` compare, `:552`
  run), each with flags, worked pretty-output examples, and full `--json` key sets. A
  consolidated exit-code section at `:586-599` covers all seven.
- **The agent-facing contract for `reassure compare`'s exit code is impossible to misread.**
  `docs/commands.md:510-519` is a blockquoted warning; `AGENTS.md:46-56` restates it at
  length ("An agent that gates a CI step on `reassure compare`'s exit code will never
  observe a failure from this command, no matter how severe the regression");
  `CLAUDE.md:24-27` restates it again; the `reassure_compare_v1` module docstring (`:7-11`)
  restates it a fourth time. That is the requirement met emphatically.
- **`docs/baselines-and-history.md:25-70` genuinely contrasts; it does not merely append.**
  It opens by naming what the preceding sections describe ("the flow world"), states the
  divergence explicitly, shows a worked two-imports-one-commit example, explains why the
  rules differ (reassure has no flow/device/mode dimension, and imports do not arrive in
  commit order), and enumerates which flow-world concepts (`min_baseline_commits`,
  distinct-commit windowing, `-dirty`) do not apply. A reader who assumes shared semantics
  is corrected in the section's second sentence.
- `README.md:125-139`: the stale "six commands" table is corrected to "The seven commands"
  and `reassure` appears as exactly one row, mirroring `markers`. Requirement met.
- `docs/configuring-flows.md:85` and `:107` document `reassure_path` and `reassure_command`.

**What is documented but wrong, or shipped but not documented:**

| # | Issue | Location |
|---|---|---|
| C-2 | `reassure run` absent from `AGENTS.md`/`CLAUDE.md` | see above |
| C-1 | "`reassure` never exits `1`" is now false | `docs/commands.md:599`, `AGENTS.md:34-35` |
| W-1 | The `sample_n < 3` insufficient-data trigger is undocumented | `docs/commands.md:507` documents only the baseline-import trigger |
| W-5 | README calls the whole reassure family "Read-only" | `README.md:134`; `import` and `run` both persist |
| S-1 | `reassure --help` exposes internal SDD jargon to end users | `src/perf/cli/commands/reassure.py` docstrings |

Nothing else is documented that does not exist. I spot-checked every `--json` key set in
`docs/commands.md` against a live payload: all correct, including `reassure run`'s
"same `reassure_import_v1`, no separate `reassure_run_v1`" claim.

---

## 7. WARNING Findings - do not block archive, should be filed

### W-1 - `MIN_BASELINE_IMPORTS` silently doubles as a minimum-samples-per-run threshold

`_classify_series` passes the constant as `min_n` (`reassure_compare.py:254-256`), and
`regression.classify:61` tests `baseline_commit_n < min_n **or** sample_n < min_n`. So a
name with a perfectly adequate baseline reports `insufficient-data` whenever the *latest
import's own series* holds fewer than 3 samples.

Reproduced: a name present in 5 imports (`baseline_import_n: 3`, at the threshold) but
measured with `runs: 1` reported `"status": "insufficient-data"` on both series. A reassure
suite legitimately configured with `runs: 2` can therefore never produce a verdict from
`reassure compare`, and nothing tells the user why.

This is implemented exactly as `design.md:203` specifies, so it is not a deviation. The
design never noticed the double duty. But the constant's name describes half of what it
does, and `docs/commands.md:507` documents only the baseline-import half.

### W-2 - `reassure_compare_v1` carries `baseline_commit_n`, duplicating `baseline_import_n` under the one name D2 exists to avoid

Live payload: top-level `"baseline_import_n": 4`, and every verdict carries
`"baseline_commit_n": 4`. They are equal *by construction*, because
`reassure_compare.py:254` passes `baseline_commit_n=baseline_import_n`. So the per-verdict
key is mechanically derivable from a field already in the same payload, which the
"Five New `--json` Contracts, No Second Source Of Truth" requirement forbids. The contract
test (`test_reassure_compare_v1_contract.py:67,81`) pins it in rather than catching it.

Beyond the letter: this is the exact commit-flavoured vocabulary that D2 ("`commit_hash`
MUST NEVER be used as a grouping key"), A7 (rejecting `config.min_baseline_commits`
*because its name binds it to commit semantics*) and `docs/baselines-and-history.md` all
work to keep out of reassure. The design's answer was "`ReassureComparison.baseline_import_n`
carries the honest name outward", but the dishonest name went outward too, in the same
payload, at the same value.

**Not classified CRITICAL** because the same payload already carries
`initial_update_state`, which is likewise derivable from `initial_update_count` plus
`baseline_initial_update_count` and is nonetheless *mandated* by D5. The derivability rule
is already knowingly relaxed in this contract, so treating this instance as
archive-blocking would be inconsistent. A strict reading of the requirement makes it
CRITICAL; the archiver should make that call deliberately rather than inherit it.

### W-3 - the AST `median_by_commit` guard is narrower than the requirement it defends

Spec (D2): *"`statistics.median_by_commit()` MUST NEVER be called on reassure data, in any
subcommand."* Guard 2 parses exactly one module,
`Path(reassure_compare.__file__)`. `cli/commands/reassure.py`, the reassure read methods in
`adapters/store_sqlite.py`, and the five renderers are unguarded; a commit-keyed collapse
introduced in any of them passes both guards. The precedent it follows
(`tests/unit/test_domain_boundary.py`) walks a module *set*, not a single file. Widening it
to every file matching `reassure` is a few lines.

### W-4 - `reassure list --limit -1` leaks SQLite `LIMIT` semantics as "unbounded"

`--limit 0` returns an empty roster, which is defensible. `--limit -1` returns **every**
import, because the value goes straight to SQLite's `LIMIT ?`, where a negative value means
no limit. The value is never validated.

This is internally inconsistent with the codebase's own stated position: the comment at
`reassure.py:186-188` explicitly rejects `-1` as a sentinel because it "relies on
adapter-specific semantics no other caller in this codebase depends on", and then the CLI
hands a user-supplied `-1` to exactly that behaviour. The spec's exit-code table lists `2`
for "bad flags"; a negative limit is a plausible reading of one.

### W-5 - `reassure compare` silently walks back to an older import; `reassure show` refuses to (A14)

The two commands disagree on the question A14 was written to settle, in the same
capability, on the same database:

```
# 'Solo renders' is in imports 1-4; the newest import is 5 and does NOT contain it.
$ perfvibe reassure show "Solo renders"
Error: no reassure entry named Solo renders in the most recent import
  hint: no walk-back to an older import - pass --import <id> or check perfvibe reassure history <name>
exit=2

$ perfvibe reassure compare "Solo renders"
latest_import_id = 4   verdicts = ['insufficient-data', 'insufficient-data']
exit=0
```

`compare_series` treats `points[-1]` as "latest", but `reassure_series` only returns imports
that *contain* the name, so "latest" silently means "the newest import that still measured
this test". That is precisely the walk-back A14 rejects with: *"Walking back invents an
ordering rule nobody asked for and would make two invocations a day apart silently read
different imports."* That reasoning applies with more force to `compare`, which renders a
verdict.

The spec's own compare requirement says "compare **the most recent import's** values for
`name`". `latest_import_id` in the payload does make it discoverable, but nothing flags it,
no test covers it (`test_cli_reassure_compare.py` has 11 tests, none for this case), and no
doc mentions it. Correct behaviour is a deliberate choice, either exiting `2` like `show`
or reporting the walk-back explicitly, but the current state is an unexamined
inconsistency.

Related: `README.md:134` describes the whole reassure family as "Read-only", which is wrong
for `import` and `run`.

### W-6 - untrusted `.perf` entry names emit raw terminal control sequences into the pretty views

`reassure-read` is the first code in this repo that renders reassure entry names to a
terminal, and nothing sanitises them. Verified over a real pty: a `name` whose JSON
contained the escape sequences `ESC [ 3 1 m` and `ESC [ 0 m` plus a BEL byte produced those
exact bytes in the rendered `reassure entries` row, with the SGR sequences intact and the
BEL preserved. The row bytes captured from the pty begin
`b'\xe2\x94\x82   \x1b[31mRED\x1b[0m\x07bell   render   1   1.0 ...'`.

When output is redirected, Click's `echo` strips ANSI incidentally, so this is invisible to
every test in the suite, all of which capture rather than allocate a tty.

`name` is fully attacker-controlled: it is a test name inside a third-party-generated
`.perf` file. A crafted name using cursor-movement or erase-line sequences can overwrite
already-printed lines, including a REGRESSION row in `reassure compare`'s table or the D5
sentence. On terminals supporting OSC 8 or OSC 52 the surface is wider. The realistic
scenario is a CI reviewer running `perfvibe reassure entries` on a PR whose author chose
the test name.

`design.md`'s threat matrix lists "Untrusted `.perf` parsing (D4 path)" as Applicable but
scopes it to JSON validity and line length, and lists "Live subprocess output reaching
stdout". It never considers persisted untrusted text reaching a *rendered* view, because
before this change no such view existed. The `--json` path is unaffected, since
`json.dumps` escapes the control bytes.

### W-7 - CI has never executed this code

`.github/workflows/ci.yml:37` pins `python-version: '3.11'`; the worktree venv is 3.12.13
and no 3.11 interpreter exists on this machine. Every gate result in section 1 was produced
on an interpreter that does not gate merges.

**Mitigating evidence I gathered rather than asserting:** `requires-python = ">=3.11"`,
ruff `target-version = "py311"`, zero occurrences of 3.12-only constructs
(`itertools.batched`, PEP 695 `type` aliases, PEP 695 generic parameter syntax), and every
`src/` and `tests/` file compiles cleanly under `compile(..., _feature_version=11)`. So the
*syntactic* risk is close to zero. What remains untested is behavioural: `sqlite3`,
`subprocess`, `json` and `statistics` stdlib differences, and the exact `typer` 0.27
resolution CI performs. Residual risk is low but nonzero, and it is not measurable from
here.

### W-8 - `reassure entries` with an out-of-range integer id exits `3`, not `2`

`perfvibe reassure entries 99999999999999999999` exits `3`. The value overflows SQLite's
64-bit integer binding, the `OverflowError` is caught by the generic handler, and the
result is classified as a store failure. The spec's table assigns `2` to "unknown
import-id, bad flags". Cosmetic, but it is the one place in the read commands where a bad
flag is reported as infrastructure trouble.

### W-9 - PR3 landed at 416 `src/` changed lines, over the 400-line budget, with no recorded exception

Measured per-slice `src/` churn (`git diff --numstat`, additions plus deletions):

```
PR0 169 | PR1a 214 | PR1b 324 | PR1c 348 | PR2a 152
PR2b-i 209 | PR2b-ii 343 | PR3 416 | PR4a 246 | PR4b 388 | PR5 248
```

`tasks.md:109-118` deferred the 2b/3/4b budget decision "to apply time against the REAL
diff". 2b (split) and 4b (388) came in under. PR3 came in at 416, and there is no record in
`tasks.md`, the apply-progress artifact, or the commit message of that overage being
observed or accepted. 16 lines is immaterial as review load; the missing decision is the
finding.

---

## 8. Known Items - confirmed, refuted, or re-characterised

| # | Self-reported item | Verdict |
|---|---|---|
| 1 | `reassure_show_pretty.py` written before its golden RED test | **Confirmed; real risk LOW.** A golden written after the code can only lock in what the code emitted. Here it is backstopped: `test_reassure_show_pretty_golden.py` has 20 tests and 14 are *independent* behavioural assertions rather than fixture comparisons, including one exact-wording assertion per D5 state, `test_unknown_never_conflated_with_unchanged_zero`, `test_unchanged_both_zero_emits_no_d5_line_at_all`, `test_series_with_zero_samples_renders_as_absent_never_zero` and `test_color_off_emits_no_ansi_in_any_state`. All six states have fixtures. I compared the emitted wording against `design.md:376-383` line by line: it matches, with ASCII `->` substituted for the design's arrow glyph, applied consistently in both views. The one thing the lapse did leave uncaught is W-6, but a golden test would not have found that either. **Accurately characterised; hides nothing larger.** |
| 2 | `design.md:148`'s query-budget row for `reassure show` is stale | **Confirmed, and worse than stated.** Measured with a live trace hook: the D8 default path executes **8** queries (`reassure_imports(1)` = 2, `reassure_entries(id, name)` = 3, `reassure_series(name, 2)` = 3), not the documented 5. The `--import <id>` path is 3, as documented. The row's real defect is that it predates the D5 reorder and never accounts for `reassure_series` at all. The table's actual claim, "CONSTANT per command, never per row", still holds: 8 is constant. Design-artifact drift, not a behavioural defect. |
| 3 | `chart_lines`' default `col_w` (8) collides for labels wider than 7 | **Confirmed and correctly scoped.** `primitives.py:254` `CHART_COL_W = 8` is unchanged; `reassure_history_pretty.py:166` computes `col_w = max(CHART_COL_W, max(len(label)) + 1)`, so this renderer is safe for any label length. The only other caller (`budget_check_pretty.py:252`) passes its own `_COL_W`. The hazard is genuinely latent-for-future-callers-only. Lowest-severity item in the list. |
| 4 | Worktree is 3.12.13, CI pins 3.11 | **Confirmed.** See W-7 for the quantified confidence impact and the mitigating evidence. |
| 5 | `reassure_import_exists` is a fourth `Store` method A2 did not anticipate | **Confirmed, and the deviation is correct.** The justification at `store_sqlite.py:511-516` and `ports.py:153` holds under scrutiny: `reassure_imports` is a *windowed* roster (an id outside `--limit` is not "unknown"), and `reassure_entries`'s emptiness is genuinely ambiguous between "unknown id" (exit `2`) and "real import, zero entries" (exit `0`), a distinction the spec requires and which I verified live in both directions. Flagged in task 1c.4 rather than absorbed. `design.md:135-139` and A2's "THREE, not five" are now stale; that is an artifact-accuracy issue, not a design violation. |
| 6 | `openspec/config.yaml`'s PR-size convention amended mid-chain to count `src/` only | **Confirmed** (`config.yaml:90-97`, commit `6fd288c`). The rationale is recorded in the file and is sound: a flat total-line ceiling penalises the strict-TDD discipline this repo mandates, and the note names the concrete slices that tripped it. Amending a budget rule during the chain the rule is measuring is a governance smell worth stating plainly, but the change was made in its own commit, documented, and applied consistently afterwards. See W-9 for the one slice that exceeded even the amended budget. |

---

## 9. SUGGESTIONS

- **S-1** `reassure --help` renders the command docstrings verbatim, so end users read
  `(spec "reassure list - Import Roster")`, `D2`, `A8`, `A14`, `design.md` references and
  `config.reassure_command` at the first surface they touch. Task 1c.11 shows the team
  already knew these docstrings reach `--help` (it fixed a mid-word hyphen wrap there).
  Splitting the SDD rationale into a module-level comment and leaving a plain sentence as
  the docstring would serve the "users first" goal at essentially no cost.
- **S-2** `reassure entries`' pretty table breaks column alignment when a name exceeds the
  40-character NAME column: the row is not truncated, so every subsequent column shifts
  right (observed with a 46-character name). Long reassure test names are the norm, not the
  exception.
- **S-3** `reassure show`'s payload carries `baseline_initial_update_count` but no
  identifier for *which* import it came from, and no `ordered_at`. An agent cannot report
  what the state transition was measured against.
- **S-4** `reassure history`'s chart labels both same-commit points `same01`, which is
  correct per the label rule but visually implies one point. Appending the import id when
  labels collide would preserve D2's meaning in the view.
- **S-5** `emit_error` strips backticks from the message, so a name containing them is
  echoed mangled in the not-found error. Cosmetic.
- **S-6** Refresh `design.md:135-139` (three store methods to four) and `:148` (query
  budget) during archive so the design artifact matches what shipped.

---

## 10. Design Coherence

| Decision | Held? | Note |
|---|---|---|
| A1 one public function in `reassure_compare.py` | Partly | Two public functions: `compare_series` and `derive_update_count_change`, the latter needed by `show` from PR2b before `compare_series` existed. Documented in the module docstring and in task 4a.6. Reasonable; the grab-bag risk A1 guards against has not materialised |
| A2 three store methods | No | Four, because of `reassure_import_exists`. Correct deviation, flagged (known item 5) |
| A3 reuse `HistoryMetric` | Yes | Confirmed by runtime field inspection |
| A4 adapter groups, pure `statistics` reduces | Yes | `_reduce_reassure_samples`, reused by both `reassure_entries` and `reassure_series` |
| A5 `kind` never SELECTed | Yes | Absent from every read model and every reassure read query |
| A6 literal `higher_is_better=False` | Yes | `reassure_compare.py:251`; payload shows `"direction": "lower-is-better"` on both verdicts |
| A7 `MIN_BASELINE_IMPORTS`, not `config.min_baseline_commits` | Yes | But see W-1 |
| A8 reuse `config.baseline_n` | Yes | `reassure.py:515`, `store.reassure_series(name, limit=config.baseline_n + 1)` |
| A9 line numbers in a parallel local, `ReassureEntry` untouched | Yes | Including the corrected scope note about `ReassureParseResult` |
| A10 native `deprecated="..."` string, no shim | Yes | Custom suffix confirmed live on typer 0.27.2 |
| A11 reuse `reassure_import_v1` for `run` | Yes | Byte-identical payloads verified |
| A12 `SubprocessRunner.run_streamed`, argv list | Yes | But the seam is unguarded, see C-1 |
| A13 `UpdateCountChange` has no delta field and is not a `Verdict` | Yes | No `*_pct` key for the update count in any payload |
| A14 no walk-back in `show` | Yes for `show` | Not applied to `compare`, see W-5 |
| Typer floor-and-ceiling pin | Yes | `typer>=0.27,<0.28` with the ruff-incident rationale |

---

## 11. Verdict

**FAIL.** 2 CRITICAL, 9 WARNING, 6 SUGGESTION.

**Blocking archive:**

- **C-1**: `reassure run` exits `1` on a missing or non-executable configured command,
  violating three separate MUST clauses and the change's own most-repeated invariant. The
  scenario that should cover it exists by name but is structurally unable to reach the
  failing code path. The default configuration makes this the likely first-run experience.
- **C-2**: `reassure run` is absent from `AGENTS.md` and `CLAUDE.md`, violating the
  Documentation Coverage requirement's explicit "all `reassure` subcommands" scope, on the
  one subcommand whose hazards most need an agent-facing contract.

Both are small to fix and both sit in slice 5. Neither touches the invariants: I1, I2,
None-vs-0 and `--json` byte-purity all hold under direct attack, and the two
`median_by_commit` guards were mutation-proven to discriminate. Six of the seven
subcommands hold exit-code discipline against every hostile input I could construct.

**This change is not ready to archive.** It is close. Return to `sdd-apply` for slice 5:
fix C-1 (wrap `run_reassure` and map `OSError` to `emit_error` plus exit `3`) with a test
that does **not** monkeypatch `run_streamed`, fix C-2, correct the two now-false
"never exits 1" sentences, and re-verify. The WARNINGs, particularly W-5 (the
`show`/`compare` walk-back contradiction) and W-6 (terminal-control-sequence injection),
deserve tickets but should not gate this chain.
