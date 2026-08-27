# Design: Read, Chart and Compare Persisted reassure Data (`reassure-read`)

> Size note: the `sdd-design` skill's 800-word budget is deliberately exceeded, matching
> the house template `docs/specs/reassure-ingest/design.md`. Nine slices, three store
> methods, five renderers and two load-bearing invariants do not compress into 800 words
> without becoming a summary. Skill rule "use the project's ACTUAL patterns" wins.

## Load-Bearing Invariants, Enforced Structurally

Both invariants are enforced by **type shape**, not by a comment asking people not to.

### I1 — `durations` and `counts` are never zipped

`0005_add_reassure_tables.sql:131-141` and `domain/model.py:529-546` already state it. This
change's enforcement: **no read model carries a raw sample array at all.** Every read model
exposes the two series only as two separately-named, already-reduced `HistoryMetric` values
with different `metric_name` and different `unit`. Two scalars cannot be zipped. Where the
adapter does touch raw rows, it runs **two independent batched queries** — never a join of
`reassure_duration_sample` with `reassure_count_sample`, which the ingest design forbids by
name.

### I2 — `commit_hash` is a label; `median_by_commit` never touches reassure

`domain/statistics.median_by_commit` (`statistics.py:83`) takes exactly one input shape:
`Iterable[tuple[str, float]]`. **No reassure read model or pure function ever produces or
accepts that shape.** `ReassureSeriesPoint.commit_hash` is a display field on a record, not
half of a pair. Calling `median_by_commit` would require first *constructing* the pair list
— a visible, deliberate act, not a slip. Backed by two RED tests in slice 4a (below).

Corollary of I2: `runs` stays DECLARED and is never reconciled against stored sample counts
(`model.py:540-546`). `show` surfaces the mismatch; nothing repairs it.

## Technical Approach

Three new `Store` read methods, three new frozen read models reusing `HistoryMetric` as the
leaf, one new pure module `domain/reassure_compare.py`, one `reassure_app` sub-app, five
`_v1` contracts, five renderers. **No new Protocol, no `application/` use-case, no
migration, no analyzer.** The CLI calls `Store` then a pure function then a renderer —
`cli/commands/history.py:81`'s exact shape.

    perfvibe reassure compare <name>        (cli/commands/reassure.py)
      ├─ build_store(config.db_path)
      │    store.reassure_series(name, config.baseline_n + 1)
      │      ① import window for this name  (ORDER BY COALESCE(created_date, imported_at) DESC LIMIT ?)
      │      ② batched duration samples ─┐  TWO queries, never a join,
      │      ③ batched count samples ────┘  each reduced via domain/statistics
      │      └→ Sequence[ReassureSeriesPoint]      OLDEST→NEWEST
      ├─ reassure_compare.compare_series(points, threshold_pct=…, floors=…)
      │      points[-1] = latest · points[:-1] = baseline window
      │      baseline = statistics.median([p.duration.p90 …])   ← plain median, NO grouping
      │      regression.classify(...) per series  ·  UpdateCountChange (D5, not a Verdict)
      └─ build_reassure_compare_payload(…) → render_json | render_reassure_compare
                                                                          → always exit 0 (D3)

## Architecture Decisions

| # | Decision | Choice | Rejected | Rationale |
|---|---|---|---|---|
| A1 | Comparison home | New pure module `domain/reassure_compare.py`, **exactly one public function** | (a) add to `domain/regression.py`; (b) private helper in `cli/commands/reassure.py` | Proposal's placement **validated**. `python-architecture` rule 3 forbids speculative *indirection* (Protocol/factory/base class) — a pure module is not indirection, and `calibration.py` is the sibling precedent. (a) pollutes the dimension-agnostic rule that `analyzer_sql` + `budget_check` share, and invites reuse across families D3 keeps apart. (b) puts decision logic behind Typer, unit-testable only through `CliRunner` — violates "pure core, effects at the edges". The one-public-function cap is what stops it becoming a grab bag. |
| A2 | Read-method count | **THREE**, not the proposal's five | Separate `entry detail` and `baseline window` methods | "Entry detail" is `reassure_entries(import_id, name=…)` — one WHERE clause. "Baseline window" and "series" are the SAME query at a different `limit`; two methods for one query shape is the abstraction rule 3 rejects. The latest/baseline split moves into `compare_series`, which is exactly where D2 belongs (pure and testable). **Slice 4a loses its store method as a result** — see Slice Map. |
| A3 | Series leaf type | Reuse `HistoryMetric` (`model.py:327`) | New `ReassureSeriesStat` | Exploration verified it as dimension-free (`{metric_name, p50, p90, n, unit}`). `metric_name` is `'duration_ms'` / `'render_count'`; `unit` is `'ms'` / `'count'`. `HistoryRun` is NOT reused (bakes in `run_id`/`git_commit`-as-identity). |
| A4 | Where p50/p90/n is computed | **Adapter groups rows, calls pure `statistics.median`/`percentile`** | (a) SQL; (b) a new pure reducer module | `_history_system_summaries` (`store_sqlite.py:822-855`) is the precedent and the only one that applies: `_history_measure_summaries` can use SQL only because `run_metric_summary` is a **view**, and reassure has none. Adding one needs a migration this change explicitly does not ship. SQLite has no `MEDIAN` (`statistics.py:1-4`). (b) is redundant — the pure part already exists. Reuses the existing `_HISTORY_P90` module constant. |
| A5 | `reassure_import.kind` | **Never SELECTed.** Absent from every read model | Show it in `reassure list` | `0006`'s own comment plus the proposal's Out of Scope ("no read of `kind` in any query"). Displaying it would be useful; the decision is locked, and a future slice wanting it needs a scope change, not a quiet SELECT. |
| A6 | `higher_is_better` | Pass literal `False` for both series | `default_higher_is_better()` (`model.py:47`) | That lookup is a global un-namespaced string map; the exploration flagged a latent collision if a user marker is literally named `duration_ms`. A literal **removes** the risk instead of documenting it. Both series are lower-is-better definitionally. |
| A7 | `min_n` for `classify` | Module constant `MIN_BASELINE_IMPORTS = 3` in `reassure_compare.py` | `config.min_baseline_commits` | The config key's NAME binds it to commit semantics; borrowing it lets a user tuning the flow-world **gate** silently retune reassure's show-only report, across the D3 boundary. Rule 7: no config surface for a need we do not have. |
| A8 | Baseline window N | Reuse `config.baseline_n` (default 10, clamped ≥1 at `loader.py:337`) | A new `reassure_baseline_n` key | "Last N observations" is the same knob; D2 makes an import the observation. Zero new config surface. The store is asked for `baseline_n + 1` (window + latest). |
| A9 | D4 line-number carrier | **Parallel local `list[tuple[int, ReassureEntry]]` inside `parse()`** | Add `line_number: int` to `ReassureEntry` | `ReassureEntry` is what `Store.save_reassure_import` consumes — widening it widens the *persistence* contract for a purely diagnostic fact the store never writes, and forces a meaningless default into every test and fake. (b) keeps the frozen-dataclass contract, `store_sqlite`, `tests/fakes.py` and every existing caller **completely untouched**. **Scope correction**: A9's untouched-surfaces guarantee covers `ReassureEntry`, `store_sqlite`, and the fakes only — it does NOT reach `ReassureParseResult`, which already exists precisely to carry parser-side diagnostics (`skipped`, `diagnostic`) the store never persists, and is the correct home for `duplicate_names_dropped` too. An earlier revision of this row read A9 as covering `ReassureParseResult` as well; that broader reading conflicted with `spec.md:356-357`'s "MUST emit ONE stderr warning naming the duplicated name" and has been corrected. |
| A10 | Deprecation notice | **Native `deprecated="use `perfvibe reassure import` instead"`** on the flat registration — no shim, no wrapper | (a) a 3-line `reassure_import_alias` shim; (b) branch on `ctx.info_name` inside the shared function | Typer's vendored Click emits the notice **at invocation**, not just in help text: `typer/_click/core.py:738-743` runs inside `Command.invoke()` and `echo(style(message, fg="red"), err=True)`. The parameter is typed `deprecated: bool | str` (`_click/core.py:533`), so a string appends a custom message after `DeprecationWarning: The command 'reassure-import' is deprecated.` It lands on **stderr**, which independently satisfies `perf-cli-output` rule 6 and keeps stdout byte-pure under `--json` — a hand-rolled shim would have had to get that right by hand. (a) is therefore dead weight; (b) makes shared behavior depend on how it was mounted, via a Click internal. The **sub-app's `import` still gets the exact same function object** and stays silent, because only the flat registration carries the argument. The custom `str` form needs click `>= 8.2`; below that the generic notice still fires, so the alias works either way — see "Typer version dependency" below. |
| A11 | `reassure run` payload | **Reuse `reassure_import_v1` verbatim** | A sixth `reassure_run_v1` contract | If reassure succeeded, the only machine-relevant fact is what got imported. If it failed, there is no import: `emit_error` + exit `3`, no payload. A sixth contract would restate ten keys to add two, which is the second-source-of-truth problem the ingest contract already rejects. Tradeoff accepted: a consumer cannot distinguish `run` from `import` output by payload alone — it invoked one of them, and `path`/`content_hash` identify the file. |
| A12 | `reassure run` process seam | `adapters/process.SubprocessRunner.run_streamed`, constructed by the command; logic in a testable `run_reassure(runner, argv, *, on_line)` helper | (a) new `ReassureRunner` Protocol + adapter; (b) `registry.build_reassure_runner()` | (a) is a Protocol for one implementation — rule 3. (b) is a factory for a class needing zero configuration. The runner is already the house seam (argv-list only, `scrub_secrets` built in, faked in `tests/fakes.py`). Helper-above/callback-below is `markers.py`'s stated shape. `run_streamed`'s `on_line` relays reassure's progress to **stderr** so `--json` stdout stays byte-pure. |
| A13 | D5 carrier | `UpdateCountChange` — a frozen dataclass with **no numeric delta field**, not a `Verdict` | An extra `Verdict` with `delta_pct` | A type that has no delta cannot render one. It is also not a `Verdict`, so `arrow_and_pct(verdict)` (`primitives.py:173`) type-errors on it and it can never land in a verdict table row. Enforcement by type, not by review. |
| A14 | D8 "name absent from latest import" | Report it and point at `--import <id>` / `reassure history <name>`; exit `2` | Silently walk back to the newest import containing that name | D8 says "the most recent import", literally. Walking back invents an ordering rule nobody asked for and would make two invocations a day apart silently read different imports. |

## Read Models (`src/perf/domain/model.py`)

```python
@dataclass(frozen=True)
class ReassureImportRow:
    """One row of `reassure list`. `ordering_key` names WHICH D2 key ordered
    this row ('created_date' | 'imported_at') so the pretty view can be honest
    about a header-less file. `commit_hash`/`branch` are LABELS: nothing keys,
    groups, joins or filters on them. `kind` is deliberately absent (A5)."""

    import_id: int
    ordered_at: str                 # COALESCE(created_date, imported_at), resolved in SQL
    ordering_key: str               # 'created_date' | 'imported_at'
    imported_at: str
    created_date: str | None = None
    branch: str | None = None
    commit_hash: str | None = None  # LABEL ONLY
    source_path: str = ""
    entry_count: int = 0


@dataclass(frozen=True)
class ReassureEntryRow:
    """One measurement line, ALREADY REDUCED. Carries NO raw sample array:
    `duration` and `count` are two separately-named `HistoryMetric`s with
    different units, so there is nothing to zip (invariant I1). `None` on
    either means that series had zero stored samples — 'no series', NEVER 0.

    `runs` is what the file DECLARED and is never reconciled against
    `duration.n`/`count.n` (`model.py:540-546`); `show` surfaces a mismatch.
    `initial_update_count` keeps NULL (never measured) distinct from 0
    (measured clean) — D5."""

    entry_id: int
    name: str
    entry_type: str
    runs: int
    duration: HistoryMetric | None = None   # metric_name='duration_ms', unit='ms'
    count: HistoryMetric | None = None      # metric_name='render_count', unit='count'
    initial_update_count: int | None = None


@dataclass(frozen=True)
class ReassureSeriesPoint:
    """One IMPORT's slot in one test name's series over time (D2: one import =
    one point, always). Ordered OLDEST→NEWEST by the store, matching
    `history_runs`. `commit_hash` is carried for the chart LABEL only and is
    never a key — two points may legitimately share it (`0006`)."""

    import_id: int
    ordered_at: str
    ordering_key: str
    entry: ReassureEntryRow
    commit_hash: str | None = None  # LABEL ONLY
    branch: str | None = None
```

## Ports (`src/perf/domain/ports.py`) — three additive methods, no new Protocol

```python
class Store(Protocol):
    def reassure_imports(self, limit: int) -> Sequence[ReassureImportRow]: ...
    def reassure_entries(
        self, import_id: int, name: str | None = None
    ) -> Sequence[ReassureEntryRow]: ...
    def reassure_series(self, name: str, limit: int) -> Sequence[ReassureSeriesPoint]: ...
```

### Query budget — CONSTANT per command, never per row

| Command | Store calls | Queries | Shape |
|---|---|---|---|
| `reassure list` | `reassure_imports` | **2** | window (`ORDER BY COALESCE(created_date, imported_at) DESC, import_id DESC LIMIT ?`) → one batched `COUNT(*) … GROUP BY import_id … WHERE import_id IN (?,?,…)` |
| `reassure entries` | `reassure_entries` | **3** | entry window → batched duration reduction → batched count reduction |
| `reassure show` | `reassure_imports(1)` + `reassure_entries(id, name)` | **2 + 3 = 5** | D8 default; `--import <id>` skips the first, so **3** |
| `reassure history` | `reassure_series` | **3** | name-joined import window → 2 batched reductions |
| `reassure compare` | `reassure_series` | **3** | identical, `limit = baseline_n + 1` |

The `IN (…)` placeholder text is built with `",".join("?" for …)` — **placeholder text, never a
bound value**, exactly as `_history_measure_summaries:804` documents (SKILL rule 4). Every
value is `?`-bound; `name` is always a bound VALUE, never an identifier.

The two reductions are two `SELECT`s over two tables, mirroring
`_history_system_summaries:830-855`: group rows by `entry_id`, then build one `HistoryMetric`
per (entry, series) via `statistics.median` / `statistics.percentile(values, _HISTORY_P90)`.
An entry with zero rows in a table yields `None`, not a zero-valued metric.

## The Verdict Function (`src/perf/domain/reassure_compare.py`)

```python
SERIES_DURATION = "duration_ms"      # unit 'ms'
SERIES_RENDER_COUNT = "render_count" # unit 'count'  → floor 0.0 via D7
MIN_BASELINE_IMPORTS = 3             # A7: NOT config.min_baseline_commits

@dataclass(frozen=True)
class UpdateCountChange:
    """D5. A STATE TRANSITION. Deliberately carries NO delta field and is NOT
    a `Verdict`, so no renderer or payload builder can turn it into a
    percentage. `None` on either side means that import never measured
    `issues` — which yields 'unknown', never 'unchanged'."""

    baseline: int | None
    latest: int | None
    state: str  # 'introduced'|'resolved'|'changed'|'unchanged'|'unknown'

@dataclass(frozen=True)
class ReassureComparison:
    name: str
    latest: ReassureSeriesPoint
    baseline_import_n: int          # IMPORTS, never commits
    verdicts: Sequence[Verdict]     # fixed order: duration_ms, render_count
    update_count: UpdateCountChange

def compare_series(
    points: Sequence[ReassureSeriesPoint],   # OLDEST→NEWEST, one per IMPORT
    *,
    threshold_pct: float,
    floors: Mapping[str, float],
) -> ReassureComparison | None:                # None when `points` is empty
```

**How it reuses `classify()` without `median_by_commit`:** `points[-1]` is the latest,
`points[:-1]` the baseline window. Per series, `baseline = statistics.median([p90 for each
baseline point that has one])` — a **plain median over per-import p90s, with no grouping step
of any kind**. D2 is expressed as the *absence* of a `by_commit` pass, and `median_by_commit`'s
`(commit, value)` input shape is never constructed (I2).

`classify(...)` is then called per series with `higher_is_better=False` (A6),
`floor=floors.get(unit, 0.0)` (D7 supplies `count: 0.0` explicitly),
`min_n=MIN_BASELINE_IMPORTS`, `sample_n=<latest series n>`, and
`series=<the per-import p90 list>` for the sparkline.

**Naming friction, documented not renamed:** `classify`'s parameter is `baseline_commit_n`
(`regression.py:32`). Reassure passes a count of baseline **imports**. Renaming it would touch
`analyzer_sql`, `calibration` and `budget_check`; the parameter's real meaning is "independent
baseline observations", and for reassure that unit is the import. A comment at the call site
says so, and `ReassureComparison.baseline_import_n` carries the honest name outward.

`UpdateCountChange` uses **latest-vs-previous** (`points[-2]`), not the median window: a
median of `int | None` state values is meaningless. Two baselines in one command is a real
wart; it is the honest one, and both are labelled in the pretty view and the payload.

## D4 — Parser Changes (`adapters/reassure_jsonl.py`, slice 0)

`parse()` appends unconditionally at `:128` today. Three edits, all local to `parse()`:

1. New constant `REASON_DUPLICATE_NAME = "duplicate_name"` beside the existing `REASON_*`
   block (`:45-50`).
2. The loop accumulates `pending: list[tuple[int, ReassureEntry]]` instead of
   `entries: list[ReassureEntry]` — a one-token change at `:128`.
3. A post-loop pass groups `pending` by `entry.name` into a `dict[str, list[tuple[int,
   ReassureEntry]]]`. Every name with ≥2 occurrences contributes `(line_number,
   REASON_DUPLICATE_NAME)` **for each copy's line** to `skipped`, and **all** copies are
   dropped. Survivors keep first-seen order (dict insertion order), so a clean file's
   persisted order is byte-identical to today — no existing golden or store assertion churns.

`ReassureEntry`, `store_sqlite`, and `tests/fakes.py` are all **unchanged** (A9 — scoped to
the persistence contract only, see the corrected A9 row above). `ReassureParseResult` gains
one additive field, `duplicate_names_dropped: Sequence[tuple[str, int]] = ()` (name, copies
dropped per duplicated name, first-seen order) — it is the diagnostic carrier `skipped`/
`diagnostic` already are, and the store never reads it. The CLI's per-line
`for line_number, reason in result.skipped` loop (`reassure_import.py`) now SKIPS
`REASON_DUPLICATE_NAME` entries and instead emits exactly ONE `emit_warning` per entry in
`duplicate_names_dropped`, naming the duplicated test — a bare line number in a generated
`.perf` file is not actionable, and `spec.md:356-357` requires ONE warning naming the name.

`_build_diagnostic` (`:293-301`) currently says "skipped as malformed" — a duplicate is not
malformed, so that message would lie. Slice 0 widens it to count the two causes separately:
`"{m} line(s) skipped as malformed; {d} dropped as duplicate name(s); {k} entries imported."`

**Contract impact (`reassure_import_v1`, 2 → 3).** A duplicate line genuinely was not
imported, so it counts into `entries_skipped` — a widening of that key's meaning, which
`openspec/specs/reassure-ingest.md:289-294` says MUST bump. A new
`entries_dropped_duplicate_name` key is added alongside, computed in the CLI by counting
`skipped` reasons equal to `REASON_DUPLICATE_NAME`. It is **not** derivable from any payload
key (`skipped` itself never enters the payload — only its count does), which is what earns it
a place under the no-second-source-of-truth rule. `sdd-spec` owns the final accounting call;
the distinct reason token in `skipped` supports either outcome with no design change.

Also in slice 0: correct `openspec/specs/reassure-ingest.md:16-17`'s inaccurate "enters
through `Analyzer`" sentence — that spec is being touched anyway for the D4 delta.

## Sub-App Wiring (`cli/commands/reassure.py` + `cli/main.py`, slice 1b)

```python
# cli/commands/reassure.py — mirrors markers.py:324-328 and :440-448 exactly
reassure_app = typer.Typer(
    add_completion=False,
    context_settings={"help_option_names": ["--help", "-h"]},
    help="Read, chart and compare persisted @callstack/reassure measurements.",
)

from perf.cli.commands.reassure_import import reassure_import

# The EXACT same function object — no wrapper, no partial, no copy.
reassure_app.command(name="import", context_settings=_CTX)(reassure_import)
reassure_app.command(name="list", context_settings=_CTX)(reassure_list)
reassure_app.command(name="entries", context_settings=_CTX)(reassure_entries_command)
```

`Typer.command()` returns the function untouched and stores a reference in a `CommandInfo`, so
registering one object on two apps yields two commands over one implementation — the same
mechanism `main.py:113-141` already uses for five module-level functions.

The `name=` kwarg form is not merely preferred here, it is **required**: `import` is a Python
keyword and can never be a `def` name, and `list` would shadow a builtin. The house form
already separates CLI name from Python name, so nothing new is needed.

```python
# cli/main.py
app.command(
    name="reassure-import",
    context_settings={"help_option_names": ["--help", "-h"]},
    hidden=True,   # drops it from root --help
    # Annotates --help AND emits at invocation, red, on stderr
    # (typer/_click/core.py:641,666 and :738-743). The string is appended
    # after "DeprecationWarning: The command 'reassure-import' is deprecated."
    deprecated="use `perfvibe reassure import` instead",
)(reassure_import_command)   # the EXACT same function object — no shim (A10)

app.add_typer(reassure_app, name="reassure")
```

`Context.obj` propagates through `add_typer` with no plumbing (`markers.py:6-10`).

### Typer version dependency — tighten the pin in slice 1b

`pyproject.toml:12` pins `typer>=0.12` with **no upper bound**, and this repo has no lockfile of
any kind (no `uv.lock`, `poetry.lock`, `requirements.txt`, `constraints.txt`, `Pipfile.lock` or
`pdm.lock` — verified absent). Two separate facts decide what the alias actually prints:

**1. The runtime echo is old; only the custom string is new.** Click's
`commands-and-groups.md` documents `deprecated=True` as issuing a warning "when the command is
invoked", and that command-level stderr echo is long-standing Click 8.x behavior — it is *not*
the thing at risk. What is new is the `str` form: Click `CHANGES.md` 8.2.0 records that
"`deprecated: bool | str` can now be used on options and arguments… The message can now also be
customised by using a `str` instead of a `bool`."

**Degradation is graceful, not breaking.** On a pre-8.2 Click a `str` value is still truthy, so
the generic `The command 'reassure-import' is deprecated.` notice still fires on stderr. Only
the custom suffix is lost. No crash, no silence. **So the pin is load-bearing for the golden
test and for message quality — never for the alias working.** Nobody should read it as a
correctness gate.

**2. Typer 0.27.0 declares no `click` dependency at all.**
`typer-0.27.0.dist-info/METADATA` requires exactly `shellingham>=1.3.0`, `rich>=13.8.0`,
`annotated-doc>=0.0.2` and Windows-only `colorama`. Click is fully vendored as `typer._click`,
and no `click*` package exists in the venv. This is what changes the shape of the argument: at
the current unbounded `typer>=0.12`, an older typer resolves an **external** click whose version
floats independently, so **two** unpinned axes decide one behavior. On a vendored typer the
bundled click ships with it, so **one** pin determines everything.

Tightening the typer pin therefore does not merely guarantee the message — it **collapses an
entire uncontrolled dependency axis**. That is the ruff lesson at `pyproject.toml:20-26` one
layer deeper: there an unbounded pin let CI and a developer venv disagree (`ruff>=0.6`, CI on
0.16, venv on 0.15, "the exact same commands passed locally and failed in CI"); here it lets
them disagree about a transitive dependency that `pyproject.toml` does not even name.

**Slice 1b tightens `typer` to a floor-and-ceiling pin on the same minor line**, following that
precedent and citing the ruff incident in the comment. Requirement: **the floor must be a typer
version that vendors click, or one whose click resolves to `>= 8.2`.** Confirming the exact
number is now a single lookup in typer's changelog, not open-ended research. **The notice's test
is only meaningful once that floor is pinned**, so the pin and the test land in the same PR —
asserting version-dependent behavior against an unbounded range is what turns a passing test
into a CI coin flip.

## Renderers (`cli/output/`) — five new modules

All five follow the open-right box established by `render_compare` (`compare_pretty.py:250`)
and `render_history` (`history_pretty.py:269`): `┌─ perfvibe reassure <verb> · …`, a `│` rail
on every line including wrapped ones, `└─` to close. Every one takes `color: bool = False` and
emits zero escapes when false.

| Module | Slice | Shape | Primitives from `f317ece` |
|---|---|---|---|
| `reassure_list_pretty.py` | 1b | Box + table, one row per import; `ordering_key` shown dim when it fell back to `imported_at` | `header_line`, `table_line`, `ColumnSpec`, `style`, `DIM` |
| `reassure_entries_pretty.py` | 1b | Box + table; `duration p50/p90` and `count p50/p90` are **separate columns with separate units** — a reader never sees a row pairing one duration with one count (I1 in the UI) | `table_line`, `header_line`, `format_value`, `Cell` |
| `reassure_show_pretty.py` | 2 | Box + labelled key/value block (`render_reassure_import`'s confirmation shape, not a table) + the D5 sentence + a `declared runs N ≠ stored n M` line when they disagree | `style`, `format_value`, `BOLD`, `DIM`, `GLYPH_*` |
| `reassure_history_pretty.py` | 3b | Box + **two sections**, one per series, each a `chart_lines` chart over that series' per-import p90 plus its own `sparkline` window line and table — `history_pretty._metric_section:233-254` copied section-for-section | `chart_lines`, `sparkline`, `table_line`, `style`, `BOLD`, `DIM` |
| `reassure_compare_pretty.py` | 4b | Box + verdict table, one row per series, then the D5 sentence on its own line below the table | `table_line`, `header_line`, `Cell`, `arrow_and_pct`, `format_value`, `sparkline`, `GLYPH_OK/OFFENDER/NEUTRAL` |

Two per-series sections instead of one combined table is invariant I1 surfacing visually.

`device_label` (`primitives.py:324`) is the one primitive that must **not** be reached for —
reassure has no device dimension. Chart x-labels use the short `commit_hash` when present, else
the date part of `ordered_at`, else `#<import_id>` (never the import id alone when a label
exists — that is what `_chart_label` already does for runs).

`_row_glyph`/`_status_code`/`_status_word` stay **private to `reassure_compare_pretty.py`**.
`compare_pretty.py:94-113` has the `Verdict`-keyed trio and `budget_check_pretty.py:104` has a
`GatedVerdict`-keyed one — different input types, so reassure is only the *second*
`Verdict`-keyed caller. Rule of three is not met; the shared `GLYPH_*` constants already are
shared. A third `Verdict`-keyed caller earns the promotion into `primitives.py`, not this one.

## D5 — State Transition in Both Views

State derivation (pure, in `reassure_compare.py`): either side `None` → `'unknown'`;
`0 → >0` → `'introduced'`; `>0 → 0` → `'resolved'`; different non-zero → `'changed'`; else
`'unchanged'`.

**Pretty** — one sentence line, never a table row, never an arrow-and-percentage:

| state | line |
|---|---|
| `introduced` | `✗ extra mount render introduced (0 → 1)` |
| `resolved` | `✓ extra mount render resolved (1 → 0)` |
| `changed` | `✗ mount render count changed (1 → 2)` |
| `unchanged`, both 0 | omitted entirely |
| `unchanged`, both non-zero | `· mount render count unchanged (1)` — dim |
| `unknown` | `· mount render diagnostics unavailable (not measured in one of the two imports)` — dim |

**JSON** — three FLAT keys, no nesting, no percentage anywhere:
`initial_update_count: int|null`, `baseline_initial_update_count: int|null`,
`initial_update_state: str`.

NULL survives serialization because Python `None` maps to JSON `null` natively and
`json_reporter`'s sanitizer only rewrites non-finite floats — `None` passes through untouched.
The contract test asserts the unmeasured case with `is None` (not a falsy check, which `0`
would also pass) and carries an explicit negative: no key matching `*_delta_pct` or `*_pct`
exists for the update count.

## `reassure run` (slice 5)

New config key `reassure_command: Sequence[str] = ("npx", "reassure")` on `PerfConfig`,
overridable as a TOML **array** (`reassure_command = ["yarn", "reassure"]`) — never a string
that gets split, so no shell-quoting surface exists. The init wizard (D6) prompts for it with a
detected default from a new pure `detect_package_manager(root: Path) -> str` (presence of
`yarn.lock` / `pnpm-lock.yaml` / `package-lock.json`), reusing `_prompt_bundle_id`'s dim
pre-filled-default idiom (`init.py:435-453`).

Flow: `run_reassure(runner, argv, on_line=…)` → non-zero returncode ⇒ `emit_error` with
`bounded_diagnostics`-trimmed output + exit `3`, **no import attempted**; zero ⇒ delegate to
the same parse-then-store path `reassure_import` already owns and emit
`reassure_import_v1` (A11). Exit codes: `2` unset/invalid `reassure_command`, `3` subprocess or
store failure, `0` otherwise. Never `1`.

## File Changes

| File | Action | Slice |
|---|---|---|
| `src/perf/adapters/reassure_jsonl.py` | Modify — `REASON_DUPLICATE_NAME`, post-loop grouping, widened diagnostic | 0 |
| `src/perf/cli/commands/reassure_import.py` | Modify — `entries_dropped_duplicate_name` only (the alias needs no shim, A10) | 0 |
| `pyproject.toml` | Modify — tighten `typer>=0.12` to a floor-and-ceiling pin (see "Typer version dependency") | 1b |
| `src/perf/contracts/reassure_import_v1.py` | Modify — `SCHEMA_VERSION` 2→3, new key | 0 |
| `openspec/specs/reassure-ingest.md` | Modify — D4 delta + correct the `Analyzer` sentence | 0 |
| `src/perf/domain/model.py` | Modify — 3 read models | 1a / 3a |
| `src/perf/domain/ports.py` | Modify — 3 `Store` methods | 1a / 3a |
| `src/perf/adapters/store_sqlite.py` | Modify — 3 read methods + batched reduction helpers | 1a / 3a |
| `tests/fakes.py` | Modify — `FakeStore` gains the 3 methods | 1a / 3a |
| `src/perf/cli/commands/reassure.py` | Create — `reassure_app` + 6 subcommands | 1b→5 |
| `src/perf/cli/main.py` | Modify — `hidden`/`deprecated` alias + `add_typer` | 1b |
| `src/perf/contracts/reassure_{list,entries,show,history,compare}_v1.py` | Create | 1b/2/3b/4b |
| `src/perf/cli/output/reassure_{list,entries,show,history,compare}_pretty.py` | Create | 1b/2/3b/4b |
| `src/perf/domain/reassure_compare.py` | Create — one public function + 2 value objects | 4a |
| `src/perf/config/loader.py` | Modify — `DEFAULT_FLOORS["count"] = 0.0` (4a); `reassure_command` (5) | 4a / 5 |
| `src/perf/cli/commands/init.py` | Modify — `detect_package_manager` + wizard block | 5 |
| `README.md`, `docs/commands.md`, `docs/configuring-flows.md`, `AGENTS.md`, `CLAUDE.md` | Modify | 1b (+ per-slice appends) |
| `docs/baselines-and-history.md` | Modify — per-import vs per-commit contrast | 3b / 4b |

## Slice Map — every decision assigned to exactly one slice

| # | Owns |
|---|---|
| **0** | A9, `REASON_DUPLICATE_NAME`, diagnostic rewording, `entries_dropped_duplicate_name`, `SCHEMA_VERSION` 2→3 + widened exact-set contract test, reassure-ingest spec correction |
| **1a** | A2 (`reassure_imports`, `reassure_entries`), A3, A4, A5, `ReassureImportRow`, `ReassureEntryRow`, `FakeStore` |
| **1b** | Sub-app wiring, A10 native `deprecated="…"` alias, **the `typer` floor-and-ceiling pin**, `list`/`entries`, 2 contracts + tests, 2 renderers + goldens, README/`commands.md`/`configuring-flows.md`/`AGENTS.md`/`CLAUDE.md` |
| **2** | D8 + A14, `show`, the `name` filter param on `reassure_entries`, contract, renderer, golden |
| **3a** | `ReassureSeriesPoint`, `reassure_series`, the 2 batched per-series reductions |
| **3b** | `history`, contract, two-section chart renderer, golden, `baselines-and-history.md` per-import note |
| **4a** | D7 floor, A1, A6, A7, A8, A13, `compare_series`, `UpdateCountChange`, both I2 guard tests |
| **4b** | `compare`, contract, table renderer + local status trio, golden, per-import-vs-per-commit contrast |
| **5** | A11, A12, `reassure_command`, `detect_package_manager`, wizard block, `reassure run` |

**Two deviations from the proposal's slice table, both flagged rather than absorbed:**

1. **4a loses its store method.** A2 folds the baseline window into `reassure_series` (3a), so
   4a is pure-domain + config only. It gets *smaller*, which helps the riskiest slice.
2. **Slice 2 contains one store-layer line.** The `name: str | None = None` filter on
   `reassure_entries` is one WHERE clause plus one Protocol default. Splitting it into a
   2a/2b pair would cost more review than it saves. This is the only place a CLI slice touches
   the store, and it is deliberate.

Nothing else is unassignable — including the reassure-ingest spec correction, which rides
slice 0 because that slice already touches that capability.

## Testing Strategy

| Layer | What | How | Slice |
|---|---|---|---|
| Integration (`tests/integration/test_reassure_jsonl.py` — this repo files the parser here, not `unit/`) | Duplicate name: 3 copies of one name across lines 4/7/9 ⇒ ALL absent from `entries`, three `(line, 'duplicate_name')` pairs in `skipped`; a clean file's entry ORDER byte-identical to today | Real fixture in `tmp_path` | 0 |
| Contract | `reassure_import_v1` exact key set + exact key COUNT at 11, `SCHEMA_VERSION == 3`, version-bump guard | Direct builder call | 0 |
| Integration (`test_store_reassure_read.py`) | `reassure_imports` orders by `created_date` then falls back per-row to `imported_at`; `entry_count` correct; **query count asserted** via a counting `sqlite3` trace hook | Real `SqliteStore` on `tmp_path` | 1a |
| Integration | **I1 at rest**: an 8-count/6-duration entry yields `duration.n == 6` and `count.n == 8` from **two** queries; no query text joins the two sample tables | Trace-hook assertion on the executed SQL | 1a/3a |
| Integration | Entry with zero duration rows ⇒ `duration is None`, not a zero-valued `HistoryMetric` | Fixture with `durations: []` | 1a |
| Unit (`test_reassure_compare.py`) | **I2 guard 1** — two imports sharing `commit_hash` AND `branch` (0006's recorded real case, baseline `created_date` newer than current) stay TWO points: assert `baseline_import_n == 3`, and pick p90s so the collapsed median (17.5) differs from the true one (20.0) | Hand-built `ReassureSeriesPoint`s, no I/O | 4a |
| Unit | **I2 guard 2** — `"median_by_commit" not in Path(reassure_compare.__file__).read_text()` | Source read; precedent `tests/unit/test_domain_boundary.py:41` | 4a |
| Unit | `compare_series` insufficient-data below `MIN_BASELINE_IMPORTS`; `count` floor is exactly `0.0`; `higher_is_better` is `False` on both verdicts | Direct call | 4a |
| Unit | D5 state table: all five states, `None` ⇒ `'unknown'` (asserted `is None`, never falsy) | Direct call | 4a |
| Contract | Each of the 5 payloads: exact key set, exact key COUNT, flat (no nested dict/list), JSON round-trip, version-bump guard; D5 negatives (`no *_delta_pct`, `is None` vs `== 0`) | `tests/contract/test_reassure_*_v1_contract.py` | 1b/2/3b/4b |
| Golden | 5 renderers, color forced OFF; empty-series, single-point and zero-variance edges for the chart | `tests/golden/test_reassure_*_pretty_golden.py` | 1b/2/3b/4b |
| Integration (CLI) | Exit `0` on every success incl. a `compare` regression (D3); `2` unknown name / name absent from latest import / bad `--import`; `3` store failure. Never `1`. `--json` stdout byte-pure, warnings stderr-only | `CliRunner`, separated streams, `--db tmp_path` | per slice |
| Integration (CLI) | `perfvibe reassure-import` still works, is absent from root `--help`, and prints the deprecation notice **on stderr** with stdout still byte-pure under `--json`; `perfvibe reassure import` prints NO notice and produces an identical payload. **Only meaningful once the `typer` floor is pinned in the same PR** — the notice is version-sensitive (see "Typer version dependency"), so asserting it against an unbounded range is a CI coin flip | `CliRunner`, separated streams | 1b |
| Unit | `run_reassure` with a fake runner: non-zero returncode ⇒ no import attempted; argv is a LIST | `tests/fakes.py` runner | 5 |
| Unit | `detect_package_manager` for each lockfile and for none | `tmp_path` | 5 |

RED before GREEN for every row. `./.venv/bin/pytest -q --cov=perf`, `fail_under = 93`, branch
coverage on. Fakes over mocks; never patch the thing under test.

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED test |
|---|---|---|---|
| SQL injection in the new read queries | **Applicable** | Every value `?`-bound; `name` and `import_id` are bound VALUES, never identifiers; all table/column names static literals; the `IN (…)` string is `?`-placeholder TEXT only | `reassure show "x'); DROP TABLE run;--"` ⇒ clean not-found, `run` table intact |
| Subprocess (`reassure run`) | **Applicable** (slice 5) | `SubprocessRunner.run_streamed` with an argv LIST; never `shell=True`; `reassure_command` is a TOML array, never a split string; `scrub_secrets` already applied per line by `run_streamed`; failure output `bounded_diagnostics`-trimmed before it reaches the CLI | Config `reassure_command = ["echo", "; rm -rf /"]` ⇒ the metacharacters are one inert argv element; failing command ⇒ exit `3`, no import |
| Untrusted `.perf` parsing (D4 path) | **Applicable** | Post-loop grouping touches only already-validated entries; `json.loads` only; line length still bounded — unchanged from ingest | Duplicate name whose value is a 1 MiB line ⇒ oversized skip fires first |
| Live subprocess output reaching stdout | **Applicable** | `on_line` relays to **stderr**, so `--json` stdout stays byte-pure | `reassure run --json` with a noisy fake command ⇒ stdout parses as exactly one JSON object |
| `--db` path handling | **Applicable** | Opened as a local SQLite file only, never executed/imported; no new migration surface | Covered by existing store tests |
| Routing / VCS-PR automation / executable-file classification | N/A | This change adds no routing, no VCS automation, and classifies no file as executable. | — |

## Migration / Rollout

**No migration.** The schema is at `0007` and this change adds none. Every slice is code-only
plus two config defaults, independently revertable by reverting its PR.

Slice 0 is the one non-data-neutral revert: data imported while D4 is live has duplicates
dropped, and `content_hash` idempotency (`ON CONFLICT DO NOTHING`) makes re-importing the same
bytes a no-op. Recovery requires deleting the `reassure_import` row before re-import. This must
appear in the D4 spec delta and in the slice-0 PR description.

Reverting 1b restores the flat `reassure-import` to visible and non-deprecated — it is only
hidden, never removed.

## Open Questions

- [ ] **D4 accounting (`sdd-spec` owns it).** The design supports both outcomes with no change,
      because the reason token in `skipped` is distinct either way. Design's position:
      `entries_skipped` widens AND `entries_dropped_duplicate_name` is added; both bump 2→3.
- [ ] **`reassure list --limit` default.** Design assumes `50` (matching `history`), per the
      proposal's assumption. Not blocking.
- [ ] **`reassure_command` default.** `("npx", "reassure")` is the zero-config choice; a
      pnpm/yarn-berry project may need an override, which the init wizard's detected default
      covers. Not blocking — slice 5 is last.
- [x] ~~**Exact `typer` floor for the deprecation notice.**~~ **CLOSED.** Researched: the
      command-level stderr echo is long-standing Click 8.x behavior, and only the custom `str`
      message is new in Click 8.2.0 (`CHANGES.md`). Typer 0.27.0 vendors click entirely
      (`typer-0.27.0.dist-info/METADATA` names no `click`). Requirement is now stated in "Typer
      version dependency": floor must vendor click, or resolve click `>= 8.2`. A pre-8.2 click
      still fires the generic notice, so nothing breaks — only the custom suffix is lost. Slice
      1b confirms the exact version number from typer's changelog; single lookup, not research.

None of these block slices 0 through 4b.
