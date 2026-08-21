```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:2145df67326aff6bd8a7a943667d3742c0e8d5ef22aadabd11fe939d07ffe3e0
verdict: pass_with_warnings
blockers: 0
critical_findings: 0
requirements: 9/9
scenarios: 17/17
test_command: ./.venv/bin/pytest -q --cov=perf
test_exit_code: 0
test_output_hash: sha256:bae1bdb4c3fb2cf7594852dd43f04633e953be2c2534f4427d267bca8f2f80c7
build_command: ./.venv/bin/mypy src/perf
build_exit_code: 0
build_output_hash: sha256:960745a059df2e6bf5556b518f6404c7707247bf3da084d48c3008145eb95a5d
```

# Verification Report: reassure-ingest

**Change**: `reassure-ingest` · **Mode**: full spec verification (proposal + spec + design
+ tasks all present) · **Verdict**: **PASS WITH WARNINGS** · **Artifact store**: hybrid

Verified read-only against `main` at `30c77ea`, clean tree. The worktree at
`perf-vibe-worktrees/reassure-ingest` was ignored as instructed — everything under review
is merged to `main` (PRs #53, #56, #57, #58, #59).

Receipt-driven review is disabled for this clone; no `gentle-ai review` operation was run
or referenced. CI is the gate.

## 1. Gate Evidence (verbatim)

```
$ ./.venv/bin/ruff check .
All checks passed!
=== EXIT: 0 ===

$ ./.venv/bin/ruff format --check .
137 files already formatted
=== EXIT: 0 ===

$ ./.venv/bin/mypy src/perf
Success: no issues found in 59 source files
=== EXIT: 0 ===

$ ./.venv/bin/pytest -q --cov=perf
TOTAL                                          3574    148    918     69    95%
Required test coverage of 93.0% reached. Total coverage: 95.17%
999 passed in 6.98s
=== EXIT: 0 ===
```

All four gates green. Coverage 95.17% against a 93% floor. `contracts/reassure_import_v1.py`
at 100%.

## 2. Task Completeness

All 47 task checkboxes across PR1, PR2, PR3, PR4a, and PR4b are `[x]`
(`openspec/changes/reassure-ingest/tasks.md`). Every task marked done was traced to shipped
code; no task claims work that is absent. Sampled confirmations:

| Task | Claim | Evidence |
|---|---|---|
| 1.3/1.4 | migration `0005` + `schema.sql` mirror | `src/perf/db/migrations/0005_add_reassure_tables.sql`; `src/perf/db/schema.sql:110-166` |
| 2.8-2.11 | domain types, port, adapter, factory | `src/perf/domain/model.py:511-577`; `domain/ports.py:105-111`; `adapters/reassure_jsonl.py`; `adapters/registry.py:261-267` |
| 3.7 | one-transaction store method + 4 helpers | `adapters/store_sqlite.py:315-438` |
| 4a.1 | migration `0006` (`kind`) | `src/perf/db/migrations/0006_add_reassure_import_kind.sql`; mirrored `schema.sql:118-126` |
| 4.0 | `kind` validated at adapter boundary | `adapters/store_sqlite.py:324-328` |
| 4.2/4.9/4.10 | contract, command, registration | `contracts/reassure_import_v1.py`; `cli/commands/reassure_import.py`; `cli/main.py` |
| 4.3 | `config.reassure_path` | `config/loader.py:80,120,324` |

## 3. Spec Compliance Matrix

9 requirements, 17 scenarios. Every scenario has a covering test that passed at runtime.

| # | Requirement | Verdict | Code | Test |
|---|---|---|---|---|
| R1 | JSON-Lines Parsing With Optional Header | PASS | `reassure_jsonl.py:110-115` (header detect), `:136-148` (`_parse_header`), `:174` (`type` default) | `test_reassure_jsonl.py:101,114,127,144,277` |
| R2 | Malformed-Line Tolerance | PASS (see W1) | `reassure_jsonl.py:94-120,151-205`; `runs` guard `:187-192` | `test_reassure_jsonl.py:155,174,258,293,303,313`; CLI `test_cli_reassure_import.py:128` |
| R3 | Independently-Indexed Sample Persistence | PASS | `store_sqlite.py:330` BEGIN / `:347` COMMIT / `:349-351` ROLLBACK; `:340-345` | `test_store_reassure.py:85,131,147,284` |
| R4 | No Cross-Series Index Pairing | PASS (strong) | `store_sqlite.py:344-345` two independent loops; `model.py:529-546` | `test_reassure_jsonl.py:64` + `test_store_reassure.py:85` + CLI `:97` |
| R5 | Diagnostic Passthrough Fields | PASS (weak, S5) | `reassure_jsonl.py:208-215` `_passthrough_json` | `test_reassure_jsonl.py:216,235`; `test_store_reassure.py:166,181` |
| R6 | Content-Hash Idempotency | PASS | `reassure_jsonl.py:78`; `store_sqlite.py:366,383-384` | `test_store_reassure.py:197,320`; `test_reassure_jsonl.py:189`; CLI `:74` |
| R7 | Exit-Code Discipline | PASS | `reassure_import.py:119-134,172-174` | `test_cli_reassure_import.py:49,60,114,158,180,198` |
| R8 | `reassure_import_v1` `--json` Contract | PASS (see S3/S4) | `contracts/reassure_import_v1.py:64-74` | `test_reassure_import_v1_contract.py:69,74,79,95`; CLI `:128,141-142` |
| R9 | No Component or Test-File Identity | PASS (weak, W2) | no such column: `schema.sql`/`0005`/`0006` carry it only in comments | indirect only — `test_reassure_jsonl.py:72,93,228,240` |

### Scenario-level detail on the items called out for scrutiny

**(2) Non-alignment invariant guarded at BOTH layers — CONFIRMED.**
Parse layer: `tests/integration/test_reassure_jsonl.py:64`
`test_non_alignment_load_bearing_counts_and_durations_have_true_lengths`. Persistence
layer: `tests/integration/test_store_reassure.py:85`
`test_non_alignment_load_bearing_persists_two_independent_series` — asserts exactly 6
duration rows and 8 count rows, `idx` contiguous from 0 *within each table*, and values
equal to the source series. A third end-to-end guard exists at
`tests/integration/test_cli_reassure_import.py:97`, asserting
`duration_samples_imported != count_samples_imported` through the real CLI.

`rg 'zip\(' src/perf/` returns three hits, none in the reassure path:
`domain/calibration.py:155` (delta pairs), `cli/output/budget_check_pretty.py:93` (table
columns), and `domain/model.py:534` — which is the invariant docstring *forbidding* it.
Nothing zips, pairs, pads, or truncates the two series.

**(3) `runs` stored as declared, never reconciled — CONFIRMED.** Persisted as its own
`NOT NULL` column (`schema.sql:137`); `test_store_reassure.py:131` proves `runs: 10`
persists alongside exactly 3 count rows. Absent `runs` is a skip, never synthesised:
`reassure_jsonl.py:187-189` returns `REASON_MISSING_FIELD`, guarded by
`test_reassure_jsonl.py:313`
`test_absent_runs_is_skipped_and_never_synthesised_from_counts`.

**(4) Absent vs empty — CONFIRMED.** `_passthrough_json` (`reassure_jsonl.py:208-215`)
returns `None` on absent key and `json.dumps(...)` on present. Both SQL states asserted
separately: `test_store_reassure.py:166` (`NULL`) and `:181` (`'[]'`). Parse-layer
counterpart at `test_reassure_jsonl.py:216`.

**(5) Nine-key contract — CONFIRMED, key names match the spec exactly.**
`contracts/reassure_import_v1.py:64-74` emits `schema_version`, `path`, `content_hash`,
`kind`, `already_imported`, `entries_imported`, `entries_skipped`,
`duration_samples_imported`, `count_samples_imported` — byte-identical to the set in
`spec.md:202-205`. `test_exact_nine_keys_no_more_no_fewer` (`:69`) is a set-equality
assertion, so adding a key does fail it. `samples_imported` and `zero_entries` asserted
absent (`:74`, `:79`).

**(6) Exit codes — CONFIRMED, each has its own test.** Unreadable/missing -> `2`
(`test_cli_reassure_import.py:49` `--json`, `:60` pretty, both asserting no payload on
stdout). Zero entries -> `0` (`:114`). Store failure -> `3` (`:158`, via a fake store
injected at the `build_store` registry seam — not a patched internal). Skip-and-warn never
fatal -> `0` with 6 warnings (`:128`). Duplicate -> `0` (`:74`). Invalid `--kind` -> `2`,
explicitly distinguished from `3` (`:198`). A dedicated sweep asserts `1` never appears
(`:180`).

**(7) stdout byte-purity — CONFIRMED.**
`test_mixed_quality_file_warns_per_skipped_line_and_stdout_is_json_pure` (`:128`) parses
`result.stdout` as JSON, asserts `result.stderr.count("warning:") == 6` and
`"warning:" not in result.stdout`. The exit-2 and exit-3 tests additionally assert
`result.stdout.strip() == ""`.

**(8) Idempotency, and a rolled-back import does not consume the hash — CONFIRMED.**
`test_store_reassure.py:197` proves the duplicate path returns `None` with zero new rows
across all four tables. Critically, `test_mid_transaction_failure_rolls_back...` at
`:284` goes further than the spec scenario requires: after the forced *real*
`sqlite3.IntegrityError` (a `name=None` second entry against `name TEXT NOT NULL` — not a
monkeypatched helper), lines `:313-317` re-import the same `content_hash` and assert a
non-`None` `import_id`, proving the rolled-back hash was not consumed.

**(9) `kind` derivation — CONFIRMED.** `derive_reassure_kind`
(`cli/commands/reassure_import.py:61-73`) is pure and basename-only, unit-tested directly
at `tests/unit/test_reassure_import_cli.py:19,24,29,34` (including a test that the
*directory* is ignored). Validated at the adapter boundary before `BEGIN`
(`store_sqlite.py:324-328`), guarded by
`test_invalid_kind_raises_value_error_before_any_row_is_written`
(`test_store_reassure.py:341`). `--kind` override tested at
`test_cli_reassure_import.py:217`, derivation-from-basename at `:229`.

**(10) Migrations — CONFIRMED.** `test_schema_sql_and_migrations_are_fully_equivalent`
(`tests/integration/test_schema.py:174`) applies `0001, 0002, 0003, 0005, 0006` and
compares the full introspected schema against `schema.sql`; its docstring (`:177`)
explicitly records "`0004` is data-only". `MIGRATION_0004` is correctly absent from the
constants block (`:20-26`). Fresh DB reaches `user_version == 6`
(`test_store_migrations.py:31`), with the six pinned sites at `:31,:89,:143,:179,:233,:251`
all updated to `6`.

## 4. Design Coherence

| Design decision | State | Note |
|---|---|---|
| Two independently-indexed sample tables | Implemented as designed | `schema.sql:150-163` |
| mean/stdev NOT persisted | Held | no such column; no such field on `ReassureEntry` |
| `runs` persisted as declared cardinality | Held | `schema.sql:137-142` |
| Flat CLI command, no `application/` use-case | Held | `reassure_import.py` calls two ports directly |
| No new `cli/output/reassure_pretty.py` | Held | local `_render_import_pretty` (`:87-98`) |
| `--json` payload shape | **Superseded, correctly** | `design.md`'s nested payload is stale; `tasks.md:255-269` and `spec.md:200-219` both record the supersession to the flat nine-key shape. Spec is the authority, as design itself states. |
| `issues` not persisted | Held, deliberate | `design.md:57` rejected-alternative row with a hook point (see S1/S2) |

## 5. Known Open Items — accuracy confirmed

- **`issues` deliberately not persisted**: accurately recorded as a *deliberate omission
  with a hook point*, not an oversight — `design.md:57` is a rejected-alternative table row
  ("Not persisted... Hook point: a `0006` adds `issues_json` if a diagnosis surface ever
  needs it"). Nothing in `src/perf/` references `issues`. Two documentation defects attach
  to it (S1, S2) that will affect the PR5 follow-up.
- **Viewing/compare surface is a non-goal**: explicit at `spec.md:12-18` ("Non-Goals") and
  in the proposal. Confirmed accurate.
- **Pre-existing debt `domain/calibration.py:203`**: confirmed — `floor = floors.get(unit,
  0.0)` keys the noise floor by *unit*, not metric.
- **Pre-existing debt `adapters/registry.py:221-225`**: confirmed, and it is *documented*
  rather than accidental — the `build_analyzer` docstring (`:224-228`) states "Takes
  `SqliteStore`, not `Store`" and names the five read-model methods a segregated read port
  would need.

## 6. Adversarial Artifact Sweep — no survivors

Swept the artifacts, DDL comments, and shipped docstrings for claims about the `.perf`
format not traceable to reassure's source or to observed data.

- **Fabricated `" > "` name delimiter**: eradicated as a live claim. The four surviving
  mentions (`tasks.md:188,221`; `spec.md:251-253`) all explicitly label it as fabricated
  and corrected. `spec.md:251-253` names the `"Login screen > renders correctly"` example
  as fabricated. The `>` at `reassure_import.py:11` is unrelated (a doc cross-reference).
- **`runs` optional/required contradiction**: resolved. `spec.md:57-63` now states `runs`
  is REQUIRED and records the earlier contradictory revision explicitly, citing reassure's
  own `packages/compare/src/type-schemas.ts`.
- **"paired by index" premise**: `exploration.md:150-151` records the earlier draft as
  invalid rather than restating it; `proposal.md:10,23,64` state the non-alignment
  correctly with a `measure-helpers.tsx` `processRunResults` citation.
- **Monkeypatch-the-thing-under-test task**: corrected in place — `tasks.md:150-159`
  (task 3.5) records the correction, and `4a.6` logs it. The shipped test uses a real
  `sqlite3.IntegrityError`.
- **`redundantUpdates` typed as a scalar**: fixed. The fixture carries it as an array —
  `[1, 2, 3]` on line 2, `[]` on lines 3 and 4.
- **Fixture realism**: entry names carry no delimiter of any kind
  (`"WidgetPanel Performance Tests WidgetPanel renders correctly"`), every good entry
  carries the full real key set including `issues`, and the 6 malformed lines yield
  exactly the 3 good entries / 7 duration samples / 12 count samples the CLI tests assert.

No unsourced format claim found.

## 7. Findings

### CRITICAL (0)

None. Nothing blocks archive.

### WARNING (2)

**W1 — `type` falsy-value coercion deviates from the Malformed-Line Tolerance requirement.**
`src/perf/adapters/reassure_jsonl.py:174` reads
`entry_type = data.get("type") or "render"`. The `or` treats every *falsy present* value as
absent, so `type: ""`, `type: null`, `type: 0`, and `type: false` are all silently accepted
as `'render'`. `spec.md:52-54` requires a skip when "`type` is present but not one of
`'render' | 'function' | 'async function'`". Only truthy non-members are skipped.
Verified by direct read-only probe against the shipped parser:

```
type=""         -> ACCEPTED as entry_type='render'
type=null       -> ACCEPTED as entry_type='render'
type=0          -> ACCEPTED as entry_type='render'
type=false      -> ACCEPTED as entry_type='render'
type="mount"    -> SKIPPED ((1, 'unknown_type'),)
type=absent     -> ACCEPTED as entry_type='render'
```

Graded WARNING, not CRITICAL: no spec *scenario* fails (the `type: "mount"` case in the
mixed-quality scenario is handled correctly), no measurement data is corrupted
(`durations`/`counts` are untouched), and the input is not reachable from real reassure
output because its zod enum rejects these upstream. But the branch is unspecified,
untested, and the inline comment at `:174` ("absent/None -> 'render'") documents only half
of what the code does.

**W2 — the "No Component or Test-File Identity" requirement has no dedicated covering
test.** R9 is satisfied *by construction* — `rg -i 'component|test_file'` across
`schema.sql`, `0005`, and `0006` returns only comments, and the exact-nine-key contract
test forecloses a payload key. Name-verbatim behavior is proved indirectly: the parse tests
look entries up by their exact full undelimited names
(`test_reassure_jsonl.py:72,93,228,240`). But no test asserts the requirement directly, and
nothing would fail if a `component` column were added to *both* `schema.sql` and the
migration — the equivalence test only catches one-sided drift. The spec itself calls this
the same family of load-bearing trap as the non-alignment invariant (`spec.md:135-136`),
and that sibling invariant got explicit guards at both layers while this one got none.

### SUGGESTION (7)

**S1 — `design.md:57`'s `issues_json` hook point names a migration number already
consumed.** It says "a `0006` adds `issues_json`", but `0006` shipped as
`0006_add_reassure_import_kind.sql`. The PR5 follow-up that persists `issues` will need
`0007`. This will actively misdirect the very follow-up the hook point exists to serve.

**S2 — `spec.md` never records that `issues` is deliberately not persisted.** The spec
lists `issues` among the parsed field set (`:30`) and among the never-required fields
(`:66`), but the deliberate-omission decision lives only in `design.md:57`. A reader of the
behavior contract alone cannot tell whether the omission is intentional. Worth stating in
the spec before PR5 reverses it.

**S3 — `test_contract_rejects_a_shape_change_without_version_bump` does not test what its
name claims.** `tests/contract/test_reassure_import_v1_contract.py:107-113` is a duplicate
of `test_exact_nine_keys_no_more_no_fewer` plus `assert payload["schema_version"] >= 1`.
Because that second assertion passes for *any* version, the test cannot distinguish a
shape change *with* a bump from one *without*. The real guard is the exact-set assertion at
`:69`, which is sound — so the contract is protected, but by a different test than the one
named for the job.

**S4 — no end-to-end assertion that the CLI's stdout key set equals the nine.** The
contract test exercises only the pure builder. The CLI tests read individual keys but never
assert set equality on the real payload. Structurally safe today because
`reassure_import.py:152-161` passes straight through the builder, but a second key source
or an extra `typer.echo` would not be caught by the contract test.

**S5 — R5's payload-exclusion clause is covered only implicitly.** No test names
`warmupDurations`/`outlierDurations` as forbidden payload keys the way `:74`/`:79` name
`samples_imported`/`zero_entries`. The exact-nine-key set does exclude them, so the
behavior holds; the intent is just not pinned by name.

**S6 — recorded coverage figure is stale by 0.02pp.** `tasks.md:340` and the
`apply-progress` Engram record both state 95.19%; the measured value on `main` at `30c77ea`
is 95.17%.

**S7 — `test_pretty_mode_reports_kind_and_counts` is too loose to fail usefully.**
`tests/integration/test_cli_reassure_import.py:152` asserts
`"entries_imported" in result.output or "imported" in result.output`. The disjunction's
second arm is a substring of the first, so the assertion reduces to `"imported" in output`
— which would pass on almost any plausible output, including a malformed or reordered
pretty view. Per `perf-cli-output`'s output contract, pretty rendering should be pinned by
a color-off golden file.

## 8. Verdict

**PASS WITH WARNINGS.** All 9 requirements and all 17 scenarios have code and a covering
test that passed at runtime. All four gates are green at 95.17% coverage. No CRITICAL
finding; nothing blocks archive. W1 is a real, evidence-backed deviation from the
Malformed-Line Tolerance requirement in a narrow, unreachable-from-real-input branch; W2 is
a missing guard on a requirement the spec itself designates load-bearing. Both are
appropriate as follow-ups alongside the already-decided PR5 (`issues` persistence), which
should also pick up S1 and S2.