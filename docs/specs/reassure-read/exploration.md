# Exploration: `reassure-read`

Make persisted `@callstack/reassure` data readable and comparable — for humans and
for AI agents.

**Phase:** `sdd-explore` · **Artifact store:** hybrid (Engram `sdd/reassure-read/explore`)
**Predecessor:** `reassure-ingest`, whose own exploration explicitly deferred this work:
"Compare, history views, name/component filtering and budget gating are explicitly out
of scope and belong to follow-up changes"
(`docs/specs/reassure-ingest/exploration.md:6-7`). This is that follow-up.

---

## 1. Current state: reassure is write-only

`perfvibe reassure-import` parses a `.perf` JSON-Lines file and persists it
idempotently. Nothing can read it back.

| Surface | Reassure support |
|---|---|
| `domain/ports.py` `Store` | `save_reassure_import()` only — no read method |
| `domain/ports.py` `ReassureParser` | `parse()` only |
| `cli/commands/compare.py` | zero references |
| `cli/commands/history.py` | zero references |
| `cli/commands/budget_check.py` | zero references |
| `cli/commands/run.py` | zero references |

`save_reassure_import` has exactly one caller (the import command);
`ReassureParser` has exactly one caller (the adapter registry factory).

### The documentation gap is total

`rg -i reassure` returns **zero** matches in `README.md`, `docs/commands.md`,
`docs/configuring-flows.md`, `docs/baselines-and-history.md`, `AGENTS.md`, and the
project `CLAUDE.md`. The only prose that mentions reassure is
`docs/specs/reassure-ingest/*` — SDD process artifacts, not user or agent
documentation.

Two consequences:

- `README.md:125` reads "## The six commands" and is **already stale before this
  change**: seven commands ship today, and `reassure-import` is simply absent from
  the table.
- `reassure_path` (`DEFAULT_REASSURE_PATH = ".reassure/current.perf"`,
  `config/loader.py:80`) is undocumented everywhere.

For an agent reading `CLAUDE.md` + `README.md` + `docs/commands.md`, the command
does not exist.

---

## 2. Schema recap (three additive migrations)

`reassure_import` — `content_hash` UNIQUE (the whole idempotency key), `imported_at`,
`source_path`, `branch`, `commit_hash`, `created_date`, `kind`.

`reassure_entry` — `import_id` FK, `name` (the ONLY identity, deliberately **not**
`UNIQUE(import_id, name)`), `entry_type`, `runs`, `warmup_durations`,
`outlier_durations`, `issues_initial_update_count`, `issues_redundant_updates`.

`reassure_duration_sample` — `entry_id`, `idx`, `duration_ms`.

`reassure_count_sample` — `entry_id`, `idx`, `render_count`.

### Two invariants every read model must respect

1. **`durations` and `counts` are NOT index-aligned and must never be zipped.**
   `durations` is the outlier-FILTERED series; `counts` is the UNFILTERED
   post-warmup series. `len(durations) <= len(counts)`, `durations[i]` and
   `counts[i]` describe different runs, and `idx` is an ordinal within its own
   series — not a run id (`domain/model.py:525`,
   `db/migrations/0005_add_reassure_tables.sql:32`).
2. **`runs` is DECLARED by the file and never reconciled** against stored samples.
   That independence is what makes a truncated `.perf` detectable
   (`db/migrations/0005_add_reassure_tables.sql:23-28`).

---

## 3. Locked decisions (D1–D6)

Made with the user before this phase; recorded in Engram
`sdd/reassure-read/decisions`.

- **D1 — CLI surface: a `reassure` sub-app**, following the `markers` precedent
  (`cli/main.py:147`). Shape: `reassure import|list|entries|show|history|compare|run`.
  The flat `reassure-import` must keep working as a deprecated alias — it is shipped
  public surface.
- **D2 — Ordering.** `created_date` is the primary chronological key,
  `imported_at` the fallback. `commit_hash` is a LABEL only: never a grouping key,
  filter, or join. One import = one series point. No per-commit medians.
- **D3 — No gate in v1.** `reassure compare` is show-only, always exit `0`. No
  `reassure budget-check`. The repo already staged it this way once: `compare`
  shipped before `budget-check`.
- **D4 — Duplicate entry names.** Detect duplicate `name` within an import, warn,
  and drop **all** copies — never "keep the first", which yields a silently wrong
  series.
- **D5 — `issues.initialUpdateCount` is a state transition**, not a metric.
  Report `0 -> 1` as "extra mount render introduced", never as a `delta_pct`.
  Preserve `NULL` vs `0` (`0007_add_reassure_entry_issues.sql`).
- **D6 — `reassure run` is the LAST slice.** Rejected as a `run` driver: `run`
  persists into `run`/`measure`/`system_sample` keyed by flow+device+mode, and
  reassure has none of those dimensions.

### Why D2 is not negotiable

`db/migrations/0006_add_reassure_import_kind.sql` already settled the model and
states it emphatically: perf-vibe models a TIME SERIES ordered by `created_date`,
and `kind` "MUST NEVER drive a query, a filter, a join, or a comparison."

It records verified evidence from real files: a `baseline.perf` and a
`current.perf` declared the **same** `commitHash` and the **same** `branch`,
differing only in `creationDate` — and the *baseline* file's timestamp was three
hours **newer** than the *current* file's. Files do not arrive in chronological
order, so no "is first" flag may ever be cached.

---

## 4. What transfers from the flow world, and what does not

### Reusable as-is

| Symbol | Location | Why it transfers |
|---|---|---|
| `regression.classify()` | `domain/regression.py:23` | Pure, dimension-agnostic: floats in, `Verdict` out |
| `statistics.median()` | `domain/statistics.py` | Pure reduction |
| `statistics.percentile()` | `domain/statistics.py` | Pure reduction |
| `statistics.robust_noise()` | `domain/statistics.py` | Pure scatter estimate |
| `HistoryMetric` | `domain/model.py:326` | `{metric_name, p50, p90, n, unit}` — dimension-free leaf |

### Must NOT be reused

**`statistics.median_by_commit()`** (`domain/statistics.py:83`) is the single
biggest trap in this change. Its whole purpose is collapsing repeated same-commit
runs into one point — which is precisely what D2 forbids. Applied to reassure it
would mechanically "work" and silently merge two genuinely distinct imports,
because 0006's own verified evidence proves same-commit collisions are real.

**`HistoryRun`** (`domain/model.py:345`) bakes in `run_id` and
`git_commit`-as-identity semantics. Reusing it invites someone to treat
`git_commit` as a key out of muscle memory. Reassure needs its own read model.

**`warmup_k`** does not transfer. Reassure's `durations[]` is already reassure's
own outlier-filtered, post-warmup set, and its `idx` is an ordinal within its own
series — not a perf-vibe-controlled iteration index.

### A real config gap

`DEFAULT_FLOORS = {"ms": 5.0, "mb": 5.0, "pct": 3.0, "fps": 2.0}`
(`config/loader.py:66`) has **no key for render counts**. `_effective_floor` does
`self._floors.get(unit, 0.0)` (`adapters/analyzer_sql.py:156`), so an unrecognised
unit silently receives a `0.0` static floor. With `adaptive_floor` on and at least
three baseline medians it still widens to `2 * robust_noise(...)`, so it is not
completely defenceless — but the static anti-false-positive floor is gone. This
must be decided before `reassure compare` ships.

### A latent, currently inert collision

`default_higher_is_better()` (`domain/model.py:47`) is a global, un-namespaced
lookup over metric names, listing only `{fps_avg, fps_min}`. `duration_ms` and
`render_count` both correctly default to lower-is-better, so there is no collision
today — and reassure never joins `compare`/`history`, so the risk stays inert.
Worth documenting, not worth fixing now.

---

## 5. Contract house pattern

From `contracts/reassure_import_v1.py` and its contract test (which itself cites
`markers_doctor_v1` as precedent):

- module-level `SCHEMA_VERSION: int`
- one pure `build_X_payload(**kwargs) -> dict` builder that never calls a port
- a flat dict, asserted flat by the test
- a dedicated `tests/contract/test_X_v1_contract.py` pinning exact keys and types,
  the exact key count, explicit "this key must NOT exist" negatives for derivable
  fields, JSON round-trip losslessness, and a version-bump guard

`reassure_import_v1` is already at `SCHEMA_VERSION = 2`. `zero_entries` and
`samples_imported` were explicitly **refused** as keys because they are mechanically
derivable. That "no second source of truth" rule gates every new reassure read
contract too.

---

## 6. Affected areas

| Area | Work |
|---|---|
| `domain/ports.py` | New `Store` read methods: list imports, entries per import, entry detail, series by name, baseline window by name |
| `domain/model.py` | New frozen read models; reuse `HistoryMetric` as a leaf, do not reuse `HistoryRun` |
| `adapters/store_sqlite.py` | Read methods mirroring `history_runs`/`_history_measure_summaries`; per-entry p50/p90/n over each series separately; never `median_by_commit` |
| `adapters/reassure_jsonl.py` | D4 duplicate detection — genuinely new logic (see below) |
| `cli/main.py` | `hidden=True` on the flat registration + `app.add_typer(reassure_app, name="reassure")` |
| `cli/commands/reassure*.py` | New sub-app hosting six subcommands |
| `contracts/` + `tests/contract/` | Five new `_v1` modules and tests |
| `cli/output/` + `tests/golden/` | Five new renderers and goldens |
| `config/loader.py` | `DEFAULT_FLOORS` count-unit decision |
| Docs | `README.md`, `docs/commands.md`, `docs/configuring-flows.md`, `docs/baselines-and-history.md`, `AGENTS.md`, `CLAUDE.md` |

### D4 is more work than it looks

The decision was framed as reuse of existing warning plumbing. The CLI side is
indeed unchanged — `cli/commands/reassure_import.py` already iterates `skipped`
generically. But the parser side is new: `_parse_entry`/`parse()` append every
entry unconditionally today, so D4 needs a post-loop pass grouping by name, plus
per-entry line-number tracking that `ReassureEntry` does not currently carry.

D4 also changes **already-shipped write-path behavior**, not just adds reads. It
needs its own spec delta against the `reassure-ingest` capability.

### Alias mechanics

Typer is pinned `>=0.12` (`pyproject.toml`), unmodified upstream. `Typer.command()`
and the underlying Click `Command` natively support `hidden: bool` and
`deprecated: bool`. The sub-app's `import` subcommand can re-register the exact
same function object, so there is zero duplication.

---

## 7. Test layers

Strict TDD is active. Runner `./.venv/bin/pytest -q --cov=perf`, coverage
`fail_under = 93`, fakes over mocks (`tests/fakes.py`).

Reassure already has: `tests/integration/test_reassure_jsonl.py` (the parser lives
under integration, not unit, per this repo's convention),
`tests/integration/test_store_reassure.py`,
`tests/contract/test_reassure_import_v1_contract.py`,
`tests/golden/test_reassure_import_pretty_golden.py`.

Recent commits (`d0149c8`, `ebe808f`, `6bc8b3e`) just restyled the pretty-output
layer — history's boxed y-axis chart and compare/budget-check's shared table shape.
New reassure renderers should follow those fresh conventions, not older ones.

---

## 8. Delivery: staged slices

A single PR is estimated at 1800–2200+ changed lines, which is reviewer-hostile and
breaks the project's own committed convention (`openspec/config.yaml:90`: "Keep a PR
under ~400 changed lines; past that, split into chained/stacked PRs").

Recommended order — **the order is load-bearing**:

| Slice | Content | Est. lines |
|---|---|---|
| 0 | D4 duplicate-name detection in the parser (write-path behavior change; needs its own spec delta) | 60–100 |
| 1 | Read-port foundation + `reassure_app` scaffolding + `list`/`entries` + deprecated alias | 350–450 |
| 2 | `reassure show <name>` (needs the "which import" decision + D5 rendering) | 200–250 |
| 3 | `reassure history <name>` — per-import p50/p90/n reusing `statistics` directly | 350–450 |
| 4 | `reassure compare <name>` — baseline window by `created_date`, count-floor decision, `classify()` reuse, never `median_by_commit` | 400–500 |
| 5 | `reassure run` + init-wizard block (D6: last) | 200–300 |

Slice 0 must precede every read model, since all of them assume per-import name
uniqueness. Slice 4 is the riskiest and must resolve the floor gap before shipping.
Docs updates ride along with the slice that introduces each fact.

---

## 9. Open decisions for `sdd-propose`

1. **`render_count` floor unit.** `DEFAULT_FLOORS` has no count key and unknown
   units silently get `0.0`.
2. **`reassure show <name>` semantics.** Which import's entry is shown when a name
   recurs across imports.

---

## 10. Non-goals

- No gating, no `budget-check` integration (D3).
- No reassure data inside `compare`/`history`/`run` — the dimension mismatch is the
  reason.
- No reading of `reassure_import.kind` in any query (0006 forbids it).
- No component/grouping derivation from test names: 0005 records that grouping is
  derived from a project naming convention and never stored.

---

## 11. Risks

| Risk | Severity |
|---|---|
| `median_by_commit` reuse — mechanically works, silently corrupts the series | High |
| `render_count` has no configured floor; defaults to `0.0` | Medium |
| D4 changes shipped write-path behavior, needs its own spec delta | Medium |
| `reassure show <name>` "which import" is underspecified | Medium |
| `README.md` "six commands" table already stale pre-change | Low |
| `docs/baselines-and-history.md` must contrast per-commit vs per-import baselines, or readers assume shared semantics | Medium |
