# Proposal: Read, Chart and Compare Persisted reassure Data (`reassure-read`)

## Intent

`perfvibe reassure-import` has been shipping since PR #59. It writes and nothing
reads. `rg -i reassure` returns **zero** matches across `README.md`,
`docs/commands.md`, `docs/configuring-flows.md`, `docs/baselines-and-history.md`,
`AGENTS.md` and the project `CLAUDE.md` — so for a user skimming the docs, and for
an agent reading `CLAUDE.md` + `README.md`, the command does not exist. The data is
in SQLite; the value is not reachable.

The whole reason reassure data was landed here (rather than left to reassure's own
two-file diff) was to get a **durable series over time**. That series is currently
unobservable. This change makes it observable: list what was imported, inspect one
test, chart one test over time, and compare one test against its own history.

Success is measured against a single sentence the user set as the requirement:
**usable by users first, agents second, and fully documented.** Documentation is a
deliverable of each slice, not a trailing chore.

## Scope

### In Scope

- **`reassure` sub-app** (`import|list|entries|show|history|compare|run`) following
  the `markers` precedent (`cli/main.py:147`), with the shipped flat
  `reassure-import` kept working as a **deprecated, hidden** alias.
- **`Store` read methods** for: imports list, entries per import, one entry's
  detail, one name's series across imports, one name's baseline window.
- **New frozen read models** in `domain/model.py`. `HistoryMetric`
  (`domain/model.py:326`) is reused as a dimension-free leaf; `HistoryRun`
  (`domain/model.py:345`) is **not** reused — it bakes in `run_id` and
  `git_commit`-as-identity.
- **A pure `domain/` comparison function** for `reassure compare`, reusing
  `regression.classify()` (`domain/regression.py:23`) and `domain/statistics.py`'s
  `median`/`percentile`/`robust_noise`.
- **D4 duplicate-name detection** in `adapters/reassure_jsonl.py` — a write-path
  behavior change, delivered first because every read model assumes per-import name
  uniqueness.
- **D7**: an explicit `"count": 0.0` entry in `DEFAULT_FLOORS`
  (`config/loader.py:66`).
- **Five `_v1` contract modules + five `tests/contract/` tests**, five renderers +
  goldens.
- **Documentation, per slice**: `README.md` (fix the stale "six commands" heading and
  collapse reassure to ONE row, mirroring how `markers` is presented),
  `docs/commands.md`, `docs/configuring-flows.md` (`reassure_path`, documented
  nowhere today), `docs/baselines-and-history.md` (per-import vs per-commit
  baselines), `AGENTS.md`, `CLAUDE.md`.

### Out of Scope

- **No gate (D3).** `reassure compare` is show-only and always exits `0`. No
  `reassure budget-check`, no `application/budget_check_flow.py` integration. The
  repo already staged it this way once: `compare` shipped before `budget-check`.
- **No reassure data inside `compare`/`history`/`run`** — the dimension mismatch is
  the reason, not an oversight.
- **No read of `reassure_import.kind` in any query** (`0006` forbids it).
- **No component/grouping derivation** from test names (`0005`: grouping is a project
  naming convention, never stored).

## Architecture decision: no analyzer port

`openspec/specs/reassure-ingest.md:16-17` states this exact follow-up enters "through
`Analyzer` (compare/history) or `application/budget_check_flow.py` (gating)". **That
sentence is wrong and must be corrected when that spec is next touched.** It was
written before D1 established that reassure has no flow/device/mode dimension.

| Option | Verdict | Evidence |
|---|---|---|
| Sibling `ReassureAnalyzer` port | **Rejected** | `python-architecture` rule 3 forbids a Protocol with one implementation. A port earns its place by isolating an **I/O seam**; the reassure comparison is pure math over rows `Store` already fetches, so there is no second seam. The existing `Analyzer` is itself a leaky precedent: `AnalyzerSql.__init__` takes a concrete `SqliteStore` (`adapters/analyzer_sql.py:65`) and `registry.build_analyzer` types on `SqliteStore`, not the `Store` Protocol. Copying that shape copies a known debt. |
| Generalize `Analyzer` | **Rejected, and dangerous** | Every parameter of `compare_latest(flow_name, device_key, mode)` (`domain/ports.py:154`) is a dimension reassure lacks; the body reads `latest_run(flow, device_key, mode)` (`analyzer_sql.py:92`) and `count_baseline_exclusions(..., latest.git_commit)` (`:133`). Generalizing forces sentinel values, and the shared `CompareResult` is consumed by `contracts/compare_v1.py` and `budget_check_v1` — dragging reassure into the gating contract D3 excludes. Worse, `AnalyzerSql` calls `median_by_commit` at `:196` and `:253` on both families; sharing that class is the most direct route to this change's highest-severity risk. |
| **CLI → `Store` read methods + pure `domain/` functions** | **CHOSEN** | Direct precedent, in this exact feature: `cli/commands/reassure_import.py:1-5` — "Two sequential port calls, no `application/` use-case"; and `openspec/specs/reassure-ingest.md:6-9` blesses "a flat CLI command calling ports directly". The closest analogue, `cli/commands/history.py:81`, calls `store.history_runs(...)` then a renderer, with no analyzer. Keeps the fix-a-bug-in-one-file property (`python-architecture` rule 1) and adds no fake beyond extending `FakeStore`. |

## Capabilities

### New Capabilities

- `reassure-read`: the `reassure` sub-app surface — `list`, `entries`, `show`,
  `history`, `compare`, `run` — their read models, ordering rules, exit-code
  discipline, five `--json` contracts, and the deprecation of the flat
  `reassure-import` alias. `reassure run` lives here rather than in its own
  capability: one command does not earn a spec file, and it composes the existing
  ingest path rather than introducing a new one.

### Modified Capabilities

- `reassure-ingest`: **D4** — duplicate `name` within one import is detected, warned,
  and **all** copies dropped (never "keep the first", which yields a silently wrong
  series). This changes already-shipped write-path behavior and needs its own delta.

## Approach

Ordering is D2 and it is not negotiable: `created_date` primary, `imported_at`
fallback, `commit_hash` a **label only**. One import = one series point.
`db/migrations/0006_add_reassure_import_kind.sql` records verified evidence that a
real `baseline.perf`/`current.perf` pair declared the *same* commit and the *same*
branch, with the baseline's timestamp three hours **newer** — so files do not arrive
in chronological order and no "is first" flag may ever be cached.

`durations` and `counts` are reduced **separately** (p50/p90/n per series). They are
never zipped: `durations` is outlier-filtered, `counts` is unfiltered post-warmup,
and `idx` is an ordinal within its own series.

D5: `issues.initialUpdateCount` renders as a **state transition** ("extra mount render
introduced" for `0 -> 1`), never a `delta_pct`; `NULL` and `0` stay distinct.
D8: `reassure show <name>` defaults to the most recent import by `created_date`
(fallback `imported_at`), with `--import <id>` to select another — mirroring how
`compare` defaults to the latest run.
D7: `DEFAULT_FLOORS["count"] = 0.0` with a comment explaining **why** it is zero — the
floor exists to suppress timing jitter, render counts are deterministic and have no
jitter to suppress, so `threshold_pct` alone is the correct guard. `_merge(dict(DEFAULT_FLOORS), ...)`
(`config/loader.py:276`) merges partial `[floors]` overrides on top of the defaults, so
the key survives user config and becomes overridable. No flow-world metric carries unit
`count`, so this is a zero-behavior-change explicitness fix for `compare`/`budget-check`.

## Agent contract (non-negotiable)

Per the project `CLAUDE.md`, the pretty view is **never** a stable contract. Each new
command ships:

- a `--json` payload carrying `schema_version`;
- its own `contracts/reassure_<cmd>_v1.py` with a module-level `SCHEMA_VERSION` and one
  pure flat-dict builder that calls no port;
- its own `tests/contract/test_reassure_<cmd>_v1_contract.py` pinning the exact key set,
  the exact key **count**, explicit "this key must NOT exist" negatives, JSON round-trip
  losslessness, and a version-bump guard.

The **no-second-source-of-truth** rule applies: `zero_entries` and `samples_imported`
were refused from `reassure_import_v1` for being mechanically derivable. Any derivable
field is refused here too.

## Delivery

Nine stacked PRs. Layer-splitting (store PR → CLI PR) is house precedent:
`openspec/config.yaml:91` records this capability's predecessor shipping as
"PR-A domain -> PR-B store -> PR-C CLI". Each `a` slice ships its own integration
tests and its `b` slice follows immediately in the chain, so no PR merges dead code
for long.

| # | Content | Est. |
|---|---|---|
| 0 | D4 duplicate-name detection in the parser (+ per-entry line-number tracking `ReassureEntry` does not carry today) | 60-100 |
| 1a | `Store` read methods (imports list, entries) + `SqliteStore` impls + read models | 180-230 |
| 1b | `reassure_app` scaffolding + `list`/`entries` + 2 contracts + goldens + deprecated alias + README/`docs/commands.md`/`reassure_path` docs | 220-280 |
| 2 | `reassure show <name>` — D5 state-transition rendering, D8 import selection | 200-250 |
| 3a | `Store` per-name series read method + pure per-series p50/p90/n reduction | 180-220 |
| 3b | `reassure history <name>` + contract + boxed-chart renderer (follow `ebe808f`) + golden + docs | 200-250 |
| 4a | D7 floor + baseline-window read method + `domain/` pure verdict function + the `median_by_commit` guard test | 200-250 |
| 4b | `reassure compare <name>` + contract + table renderer (follow `6bc8b3e`) + golden + `docs/baselines-and-history.md` | 200-250 |
| 5 | `reassure run` + init-wizard reassure block (D6: last) | 200-300 |

Slice 0 must precede every read model. Slice 4 is the riskiest.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/perf/adapters/reassure_jsonl.py` | Modified | D4 post-loop duplicate grouping + line-number tracking |
| `src/perf/domain/ports.py` | Modified | Five `Store` read methods. **No new Protocol.** |
| `src/perf/domain/model.py` | Modified | New frozen read models; reuse `HistoryMetric`, not `HistoryRun` |
| `src/perf/domain/reassure_compare.py` | New | Pure verdict function over already-fetched rows |
| `src/perf/adapters/store_sqlite.py` | Modified | Read methods mirroring `history_runs`/`_history_measure_summaries` |
| `src/perf/config/loader.py` | Modified | `DEFAULT_FLOORS["count"] = 0.0` + why-comment |
| `src/perf/cli/main.py` | Modified | `hidden=True`/`deprecated=True` on the flat command; `add_typer(reassure_app)` |
| `src/perf/cli/commands/reassure*.py` | New/Modified | Sub-app + six subcommands; `import` re-registers the same function object |
| `src/perf/contracts/`, `tests/contract/` | New | Five `_v1` modules + five tests |
| `src/perf/cli/output/`, `tests/golden/` | New | Five renderers + goldens |
| `README.md`, `docs/*.md`, `AGENTS.md`, `CLAUDE.md` | Modified | Per-slice docs |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| **`median_by_commit` reuse** (`domain/statistics.py:83`) silently merges two genuinely distinct imports — it works mechanically and raises nothing | High | **Hard guard.** It MUST NEVER touch reassure data. Slice 4a ships a dedicated test that fails if a same-commit pair collapses. `openspec/specs/reassure-ingest.md:367` already records it as structurally unsuitable. Design and tasks must both carry this. |
| D4 changes an existing `--json` key's MEANING: if duplicates are counted into `entries_skipped`, `reassure_import_v1` must bump `SCHEMA_VERSION` 2 → 3 per `openspec/specs/reassure-ingest.md:289-294`. A new non-derivable `entries_dropped_duplicate_name` key would also bump | High | Decide in `sdd-spec`, not `sdd-apply`. Either path bumps; slice 0 must widen the exact-set contract test in the same PR. |
| **D4 rollback is not data-neutral.** Data imported while D4 is live has duplicates dropped; reverting does not restore them, and `content_hash` idempotency (`ON CONFLICT DO NOTHING`) makes re-importing the same bytes a no-op | Med | Recovery requires deleting the `reassure_import` row before re-import. Document this in the D4 delta and the rollback plan. |
| Nine stacked PRs is a long chain; a mid-chain retarget is easy to get wrong | Med | Each PR targets the immediately-previous branch; verify the diff is clean before review. Slices 0, 2 and 5 are independently mergeable if the chain stalls. |
| `docs/baselines-and-history.md` readers assume reassure shares per-commit baseline semantics | Med | Slice 4b must contrast per-import vs per-commit explicitly, not just add a section. |
| `default_higher_is_better()` (`domain/model.py:47`) is a global un-namespaced lookup | Low | Inert: `duration_ms` and `render_count` both correctly default to lower-is-better, and reassure never joins `compare`/`history`. Document, do not fix. |

## Rollback Plan

No migration: the schema is already at `0007` and this change adds none, so there is
nothing to un-apply. Every slice is code-only (plus one config default) and
independently revertable by reverting its PR.

- Reverting any read slice (1a-4b) removes commands and read methods only. No stored
  row, no existing `--json` payload, and no `run`/`compare`/`budget-check`/`history`
  behavior is touched.
- Reverting slice 1b restores the flat `reassure-import` to visible, non-deprecated —
  it was never removed, only hidden.
- **Slice 0 (D4) is the exception**: see the rollback risk above. Revert restores
  duplicate persistence for future imports, but does not recover duplicates already
  dropped.

## Dependencies

- No new packages. Typer is pinned `>=0.12` (`pyproject.toml`) and natively supports
  `hidden`/`deprecated` on `Typer.command()`, so the alias needs no vendored code.
- Project gates unchanged: strict TDD (RED first, for the right reason),
  `./.venv/bin/pytest -q --cov=perf` with `fail_under = 93`,
  `./.venv/bin/ruff check .`, `./.venv/bin/mypy src/perf`.

## Success Criteria

- [ ] `perfvibe reassure list|entries|show|history|compare --json` each emit a payload
      carrying `schema_version`, pinned by its own `tests/contract/` test with an exact
      key set and key count.
- [ ] `perfvibe reassure-import` still works, is hidden from `--help`, and emits a
      deprecation notice; `perfvibe reassure import` runs the identical code path.
- [ ] Two imports sharing a `commit_hash` AND a `branch` produce **two** distinct
      series points in `reassure history` — proven by a test that fails if
      `median_by_commit` is introduced.
- [ ] A `.perf` file with a duplicated entry `name` imports with that name **absent**
      entirely and a stderr warning, and the import still exits `0`.
- [ ] `reassure compare` exits `0` on a regression (never `1`), and reports a
      `render_count` change against `threshold_pct` with a `0.0` floor.
- [ ] `rg -i reassure README.md docs/ AGENTS.md CLAUDE.md` returns matches; the
      "six commands" heading is corrected and reassure occupies exactly **one** table
      row; `reassure_path` is documented.
- [ ] `ruff`, `mypy`, and the 93% coverage floor all pass on every slice.

## Open Questions (for the user, before `sdd-spec`)

1. **D4 accounting.** Does a dropped duplicate count into `entries_skipped` (changing
   that key's meaning), or get its own `entries_dropped_duplicate_name` key? Both bump
   `reassure_import_v1` to `SCHEMA_VERSION = 3`. Assumption if unanswered: a new
   dedicated key, because it is not derivable from any existing key.
2. **`reassure list` default window.** `compare` uses `baseline_n = 10`; `history` uses
   `--limit 50`. Assumption if unanswered: `--limit 50`, matching `history`, since
   `list` is an export surface and not a gate input.
3. **Deprecation horizon.** Does the hidden `reassure-import` alias get a stated
   removal version, or stay indefinitely? Assumption if unanswered: kept indefinitely
   and documented as deprecated — it is shipped public surface with unknown consumers.
