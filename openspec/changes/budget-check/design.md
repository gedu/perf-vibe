# Design: `perf budget-check` (Phase 3 — the CI gate)

**Scope**: HOW to build the relative-gate CI command locked by the proposal Rev 2 (decisions D1–D7). No new statistics — budget-check is a thin, pure decision layer on top of `compare`'s already-shipped `Analyzer.compare_latest → CompareResult` engine, plus its own presentation. Every shipped module (`compare`, `run`, `compare_v1`, `compare_pretty`) stays byte-frozen; everything here is additive.

---

## 1. Architecture Overview — data + control flow

```
CLI  cli/commands/budget_check.py  (typer, presentation + composition root)
  │  1. usage guard: flow ∉ config.flows  ───────────────────────────► exit 2
  │  2. resolve device_key + HEAD sha/branch via build_context_provider().context()
  │  3. build_store → build_analyzer  (SAME wiring as compare.py)
  │  4. build_commit_log()            (NEW adapter, render-time only)
  │  5. construct BudgetCheckUseCase(analyzer=analyzer)
  ▼
Application  application/budget_check_flow.py
  │  BudgetCheckUseCase.execute(BudgetCheckRequest)
  │    analyzer.compare_latest(flow, device_key, mode) ─► CompareResult | None
  │      None  ──raise UsageError──────────────────────────────────► exit 2
  │      raise (store/tooling) ──BudgetCheckFailedError────────────► exit 3
  ▼
Domain (pure)  domain/budget.py
  │  budget.evaluate(CompareResult, *, strict) ─► BudgetVerdict
  │    gate_status ∈ {pass, fail, skipped}     (fail is a RETURN, never a raise)
  ▼
CLI  maps + ALWAYS prints the verdict first, THEN exits
  │  --json  → contracts/budget_check_v1.build_payload(BudgetVerdict)  (schema_version=1)
  │  pretty  → cli/output/budget_check_pretty.render_summary / render_metric_detail
  │  exit:  gate_status == "fail" → 1     pass|skipped → 0
```

The gate decision lives in EXACTLY ONE pure function (`domain/budget.evaluate`). The CLI owns exit-code mapping and rendering. The use-case owns orchestration + the exception→exit contract. This mirrors `run`'s `RunFlowUseCase` exception mapping and `compare`'s CLI-owned exit discipline.

**Reuse, not re-derivation**: budget-check loads no new config key. It reuses `threshold_pct` / `floors` / `min_baseline_commits` / `warmup_k` / `baseline_n` exactly as `compare` resolves them through `build_analyzer`. No new port for the gate, no new store read-model, no schema migration.

---

## 2. New domain types (`domain/model.py`, additive, all `@dataclass(frozen=True)`)

### `SeriesPoint` (D7)
```python
@dataclass(frozen=True)
class SeriesPoint:
    commit: str      # git sha for this baseline point (or HEAD for the latest point)
    value: float     # the per-commit median (or the latest run's own value)
```
Purpose: labels each chart point with the commit it came from. `Verdict.series` (bare floats) is kept for `compare_pretty`; `series_points` is the labeled parallel carrier the budget-check detail chart consumes.

### `Verdict.series_points` (additive field on the EXISTING `Verdict`)
Append ONE field, safe default — no positional breakage, `compare_v1`/`compare_pretty` untouched:
```python
    series_points: Sequence[SeriesPoint] = ()
```
`series` and `series_points` describe the same points; `series_points[i].value == series[i]` by construction. Additive-only: existing keyword/positional `Verdict(...)` construction in `run`-era tests keeps working.

### `GatedVerdict` (NEW — per-metric gate annotation)
```python
@dataclass(frozen=True)
class GatedVerdict:
    verdict: Verdict     # the untouched compare Verdict, by value
    gated: bool          # True ⇒ this metric counts as an offender in this invocation
```

### `BudgetVerdict` (NEW — the gate result the use-case returns)
```python
GATE_PASS = "pass"
GATE_FAIL = "fail"
GATE_SKIPPED = "skipped"

@dataclass(frozen=True)
class BudgetVerdict:
    gate_status: str                       # 'pass' | 'fail' | 'skipped'
    gated_verdicts: Sequence[GatedVerdict]  # every metric, in CompareResult order
    offending_metrics: Sequence[str]        # names where gated is True (aggregate blast radius)
    strict: bool                           # which policy produced this verdict
    calibration: CalibrationReport         # carried through for the pretty footer (informational)
```
`GATE_*` constants live beside the type. `BudgetVerdict` is frozen, pure, imports nothing from adapters. It carries `calibration` so the renderer shows the same sanity footer `compare` does, WITHOUT the domain gate rule depending on it (the label never changes `gate_status`).

---

## 3. Pure gate rule — `domain/budget.py`

One pure function, no I/O, no adapter imports. `strict` is a PARAMETER, not scattered branching — fail-open vs fail-closed differ only in whether `insufficient-data` counts as an offender.

```python
def evaluate(result: CompareResult, *, strict: bool = False) -> BudgetVerdict:
    gated: list[GatedVerdict] = []
    offending: list[str] = []
    saw_real_verdict = False        # any metric that was actually gradeable
    for v in result.verdicts:
        if v.status == regression.STATUS_REGRESSION:
            is_offender = True
            saw_real_verdict = True
        elif v.status == regression.STATUS_INSUFFICIENT_DATA:
            is_offender = strict     # fail-closed only under --strict
        else:                        # stable | improvement
            is_offender = False
            saw_real_verdict = True
        gated.append(GatedVerdict(verdict=v, gated=is_offender))
        if is_offender:
            offending.append(v.metric_name)

    if offending:
        status = GATE_FAIL
    elif not saw_real_verdict:       # everything was insufficient-data, nothing gradeable
        status = GATE_SKIPPED        # fail-OPEN (exit 0); under strict this branch is unreachable
    else:
        status = GATE_PASS
    return BudgetVerdict(status, tuple(gated), tuple(offending), strict, result.calibration)
```

**Rule summary**
- `regression` → always an offender → contributes to `fail`.
- `insufficient-data` → offender ONLY under `--strict` (guilty until proven safe).
- `stable`/`improvement` → never an offender.
- `fail` if any offender; else `skipped` if NOTHING was gradeable (all insufficient-data, non-strict); else `pass`.
- All-or-nothing: any single offender fails the whole flow, but the loop AGGREGATES every offender into `offending_metrics` (never stops at the first) — the full blast radius for `--json`.

No config surface, no per-metric severity, no absolute-budget hook. Rule-of-three: the deferred absolute-budget future gets its own additive slice; we add zero speculative extension points now.

---

## 4. Ports

### NEW port — `CommitLog` (`domain/ports.py`, `typing.Protocol`)
```python
class CommitLog(Protocol):
    """Git commit-subject lookup for render-time context (D6). PURE seam;
    the concrete adapter runs `git log` behind it. Fail-graceful: returns
    None when the repo/commit/binary is unavailable — NEVER raises."""
    def subject(self, sha: str) -> str | None: ...
```
Contract: `subject(sha)` returns the one-line commit subject, or `None` on any failure (missing repo, unknown sha, `git` absent from PATH, empty output). This is the ONLY new port. The git lookup is a side effect, so it MUST sit behind a port with the adapter in `adapters/` — the renderer/CLI depend on the Protocol, never on `git`.

### NO new port for the gate
budget-check consumes the EXISTING `Analyzer` port read-only. No `Budget`/`Gate` port, no new store read-model. A new port is introduced only when absolute-ceiling budgets land (rule-of-three).

---

## 5. `series_points` threading (D7) — exact changes

The SQL already returns per-commit baseline rows carrying `git_commit` (`RunPoint`, `BaselineSystemSamplePoint`), and `analyzer_sql._sparkline_series` already orders commits chronologically and appends the latest value. We add a PARALLEL builder that keeps the commit label.

**`adapters/analyzer_sql.py`**
- Add `_series_points(points, latest_value, latest_commit) -> tuple[SeriesPoint, ...]`: same ordering logic as `_sparkline_series`, but each element is `SeriesPoint(commit=commit, value=median)`; append `SeriesPoint(commit=latest_commit, value=latest_value)` when `latest_value is not None`. `latest_commit` is `latest.git_commit` (already in scope for both families).
- In `_compare_measure_family` and `_compare_system_sample_family`, pass `series_points=self._series_points(non_null_points, point.p90_ms, latest.git_commit)` (measure) / `(points, latest_value, latest.git_commit)` (system_sample) into `regression.classify`.

**`domain/regression.py`**
- `classify(...)` gains a keyword `series_points: Sequence[SeriesPoint] = ()`, threaded straight into the `Verdict(...)` it builds (both the insufficient-data early return and the normal return). Default `()` keeps every existing caller/test green.

**`domain/model.py`**
- `Verdict.series_points: Sequence[SeriesPoint] = ()` (§2). `CompareResult` is unchanged structurally — `series_points` rides inside each `Verdict` it already holds.

**Backward-compat guarantees (verified against shipped code)**
- `compare_v1.build_compare_payload` reads named fields only and never emits `series` — it will NOT emit `series_points` either. `compare_v1` stays frozen; its contract test is unaffected.
- `compare_pretty` keeps calling `verdict.series` (bare floats). It never reads `series_points`. Its goldens are unaffected.
- Because the field is additive with a safe default, the domain change is shared but backward-compatible.

---

## 6. Application use-case — `application/budget_check_flow.py`

Mirrors `RunFlowUseCase`'s exception→exit contract. Note: `compare` wires directly in its CLI with no use-case; budget-check gets a real use-case because the proposal wants the gate-fail-vs-runtime-error distinction modeled in the application layer.

```python
class UsageError(Exception): ...              # CLI → exit 2 (mirrors run_flow.UsageError)
class BudgetCheckFailedError(Exception):      # CLI → exit 3 (mirrors run_flow.RunFailedError)
    def __init__(self, message, *, diagnostics=None): ...

@dataclass(frozen=True)
class BudgetCheckRequest:
    flow_name: str
    device_key: str
    mode: str            # 'warm' | 'cold'
    strict: bool = False

class BudgetCheckUseCase:
    def __init__(self, *, analyzer: Analyzer) -> None:
        self._analyzer = analyzer
    def execute(self, request: BudgetCheckRequest) -> BudgetVerdict:
        try:
            result = self._analyzer.compare_latest(
                request.flow_name, request.device_key, request.mode)
        except Exception as exc:                 # store/tooling failure
            raise BudgetCheckFailedError(
                f"Failed to evaluate budget for {request.flow_name!r}: {exc}",
                diagnostics=str(exc)) from exc
        if result is None:                       # no runs at all (C2/C7)
            raise UsageError(
                f"no history for flow {request.flow_name!r} "
                f"(device={request.device_key!r}, mode={request.mode!r})")
        return budget.evaluate(result, strict=request.strict)
```

- Depends ONLY on the `Analyzer` port + the pure `budget` module. No adapter imports (boundary-clean).
- Return: `BudgetVerdict`. **Gate FAIL is in the returned value (`gate_status == "fail"`), never a raise** (D3). The CLI maps it to exit 1.
- Exception contract: `UsageError` → 2, `BudgetCheckFailedError` → 3. Deliberately NOT importing `run_flow`'s exception classes — the two use-cases stay decoupled; the CLI catches budget-check's own types.

---

## 7. CLI command — `cli/commands/budget_check.py`

Mirrors `compare.py`'s composition + exit discipline; adds gate mapping and budget-check's own flags.

**Flags**
- `flow: str` (argument) — config-known flow.
- `--strict` (bool, default False) — fail-closed on insufficient-data/no-baseline (D4).
- `--metric <name>` (str | None) — render the single-metric DETAIL view (D5).
- `--verbose` (bool, default False) — summary view auto-expands regressed metrics inline (D5).
- `--restart` / `--device` — shared with compare (cold series / device pin).
- Globals from `ctx.obj`: `--json`, `--no-color`, `--db`, `--config` (resolved by `main_callback` into `OutputContext` + `PerfConfig`, same as compare).

**Flow**
```python
1. if flow not in config.flows:  echo err; raise typer.Exit(2)     # usage-before-work
2. resolved_device = device or config.device;  mode = "cold" if restart else "warm"
3. try (composition + execute):
       ctxp = build_context_provider(build_variant, tool_version, device=resolved_device)
       rc   = ctxp.context()                 # HEAD sha/branch + device_key, one call
       store = build_store(config.db_path)
       analyzer = build_analyzer(store, threshold_pct=..., floors=..., min_baseline_commits=...,
                                 warmup_k=..., baseline_n=...)
       use_case = BudgetCheckUseCase(analyzer=analyzer)
       verdict = use_case.execute(BudgetCheckRequest(flow, rc.device_key, mode, strict))
   except UsageError:            echo err; raise typer.Exit(2)
   except BudgetCheckFailedError: echo err; raise typer.Exit(3)
   except Exception:             echo err; raise typer.Exit(3)     # never Python default 1
   finally: store.close() guarded (never override exit code)
4. ALWAYS render the verdict first:
       if output.json_mode:  echo(render_json(build_payload(verdict)))
       elif metric:          commit_log = build_commit_log(repo_path=...)
                             echo(render_metric_detail(verdict, metric, rc, commit_log, color=...))
       else:                 commit_log = build_commit_log(...)  # only queried on regression
                             echo(render_summary(verdict, rc, commit_log, verbose=verbose, color=...))
5. exit mapping (AFTER printing — never silent):
       raise typer.Exit(1 if verdict.gate_status == GATE_FAIL else 0)
```

- Exit codes: `0` pass/skipped, `1` gate fail (confirmed regression, or strict insufficient-data), `2` usage (unknown flow / no history), `3` runtime. `1` fires ONLY from `gate_status == "fail"`.
- Non-TTY nudge toward `--json` reused from `OutputContext.should_nudge_stderr` (same as compare).
- `--metric`/`--verbose` affect PRETTY only; `--json` always emits the full flat payload regardless.

**`cli/main.py`** (only wiring change)
```python
from perf.cli.commands.budget_check import budget_check as budget_check_command
app.command(name="budget-check",
            context_settings={"help_option_names": ["--help", "-h"]})(budget_check_command)
```

---

## 8. Contract — `contracts/budget_check_v1.py` (D1)

Independent module, OWN `SCHEMA_VERSION = 1`, FLATTENED shape. Does NOT nest `compare_v1`. Reuses compare's per-metric field layout by VALUE and ADDS `gated`.

```python
SCHEMA_VERSION = 1

def build_payload(bv: BudgetVerdict) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "gate_status": bv.gate_status,          # "pass" | "fail" | "skipped"
        "strict": bv.strict,
        "offending_metrics": list(bv.offending_metrics),
        "verdicts": [
            {
                "metric": gv.verdict.metric_name,
                "unit": gv.verdict.unit,
                "direction": _direction(gv.verdict.metric_name),
                "latest_value": gv.verdict.latest_value,
                "baseline_value": gv.verdict.baseline_value,
                "delta_pct": gv.verdict.delta_pct,
                "threshold_pct": gv.verdict.threshold_pct,
                "floor": gv.verdict.floor,
                "status": gv.verdict.status,
                "gated": gv.gated,               # ← the gate-specific addition
                "sample_n": gv.verdict.sample_n,
                "baseline_commit_n": gv.verdict.baseline_commit_n,
            }
            for gv in bv.gated_verdicts
        ],
    }
```

**Decisions**
- Flat, gate-first, self-contained (D1). Top-level `gate_status` + `offending_metrics` + `strict`; per-metric `gated: bool`.
- `series_points` is NOT emitted — it is a render-time chart concern, not part of the machine gate contract (keeps the payload lean and stable, same discipline by which `compare_v1` omits `series`).
- `calibration` is NOT emitted — the gate contract is about the gate; the sanity label stays a `compare` concern and is shown only in the pretty footer from the domain object. (Deliberate exclusion; if CI triage ever needs it, it is an additive field behind a `schema_version` bump.)
- Its contract test pins THIS shape independently of `compare_v1`; `compare_v1` stays frozen. No coupling, no retrofit.

---

## 9. Renderers — `cli/output/budget_check_pretty.py`

### rich vs hand-rolled — DECISION: hand-rolled box-drawing
budget-check would be the FIRST `rich` consumer. I reject rich here and hand-roll, matching `compare_pretty.py`'s established pattern.

Rationale (senior call, rule-of-three):
1. **Determinism is free hand-rolled, fiddly with rich.** The whole codebase's golden discipline is "pass an explicit `color: bool`, emit zero ANSI when false, render at a fixed width." A hand-assembled string satisfies this by construction. rich needs a carefully pinned `Console(width=…, no_color=True, force_terminal=…, legacy_windows=False)` and still routes through its own styling engine — a NEW determinism surface to babysit for ONE renderer.
2. **The layout is trivial box-drawing.** Open-right = a top rule, a bottom rule, and a left rail `│` — no table engine required. Reaching for rich to draw two horizontal rules and a vertical bar is over-engineering.
3. **Consistency.** Two renderers (`compare_pretty`, `budget_check_pretty`) sharing the exact same color-flag + golden approach is easier to reason about than one hand-rolled + one rich.

rich stays sanctioned; adopt it when a SECOND, genuinely table/tree-shaped consumer justifies the determinism cost. Not now.

### Summary view — `render_summary(bv, rc, commit_log, *, verbose, color, width=DEFAULT)`
- **Header**: `HEAD <short-sha> (<branch>)` from `rc.git_commit`/`rc.git_branch` (short = first 7 chars; `None` → `unknown`).
- **Open-right layout**: a top rule (`┌────…`), a bottom rule, and a left rail `│ ` on every content line. NO right border (wide sparkline glyphs desync monospace alignment — leaving the right open is correct AND nicer, per D2).
- **Spaced rows**: a blank rail line between metric rows so sparklines never overlap vertically.
- **Per-metric row**: reuses compare's content (name, latest vs baseline, arrow + signed %, status word, sparkline) plus a status glyph `✗`/`✓`/`·` (regression/ok/insufficient) so emphasis never depends on color alone.
- **Color by status**: regression rows red (`color` path only); color-off keeps the `✗` glyph + `REGRESSION` word.
- **`--verbose`**: after each regressed metric row, inline-expand a compact detail block (baseline vs latest, delta, and — via `commit_log.subject(rc.git_commit)`, one git call — the HEAD commit subject; `None` → SHA-only).
- **Calibration footer**: one sanity-label line (from `bv.calibration`), never interleaved mid-metric — same rule as compare.
- **Gate footer banner**: a final rule + `GATE: FAIL — checkout, fps_avg regressed` (red) / `GATE: PASS` (green) / `GATE: SKIPPED — insufficient history to gate` (dim). Glyph + word carry it with color off.

### Detail view — `render_metric_detail(bv, metric_name, rc, commit_log, *, color, width)`
- Selects the one `GatedVerdict` whose `metric == metric_name`; if absent → a clear "metric not in this run" message (still exit-mapped by gate_status, still printed).
- **Bigger multi-line chart** from `verdict.series_points`:
  - **Y-axis**: value ticks — min/mid/max of the series values, left-labelled (e.g. `820 ┤`, `610 ┤`, `400 ┤`), rows rendered with block/braille levels normalized to the series min/max (same normalization guard as compare's `_sparkline`: empty/single/zero-variance handled without divide-by-zero).
  - **X-axis**: one label per commit — short shas from `series_points[i].commit`, HEAD marked (e.g. a `^HEAD` caret / `*` under the last column).
  - On a regression: the HEAD commit subject via `commit_log.subject(rc.git_commit)` (fail-graceful to SHA-only).
- Same open-right frame, same color rules, same gate banner.

### Determinism for goldens
Every renderer takes `color: bool` (forced off in goldens) and a fixed `width`. Charts normalize only to their own series' min/max. `commit_log` is a `FakeCommitLog` returning a fixed subject in tests. No wall-clock, no TTY probing inside the renderer (the CLI resolves TTY/color and passes the flag). → byte-stable output, exactly like `compare_pretty`'s goldens.

---

## 10. File map (by hexagonal layer)

**NEW**
| File | Layer | Responsibility |
|---|---|---|
| `src/perf/domain/budget.py` | domain (pure) | `evaluate(CompareResult, *, strict) -> BudgetVerdict`; `GATE_*` constants. The ONE place the gate decision lives. |
| `src/perf/application/budget_check_flow.py` | application | `BudgetCheckUseCase`, `BudgetCheckRequest`, `UsageError`, `BudgetCheckFailedError`. Orchestrates `Analyzer` + pure gate; exception→exit contract. |
| `src/perf/adapters/commit_log_git.py` | adapters | `GitCommitLog.subject(sha)` — `git log -1 --format=%s <sha>` via argv-list `SubprocessRunner`, fail-graceful to `None`. |
| `src/perf/cli/commands/budget_check.py` | cli | typer command; composition + exit `0/1/2/3` mapping + render dispatch. |
| `src/perf/cli/output/budget_check_pretty.py` | cli/presentation | `render_summary` + `render_metric_detail` (hand-rolled, open-right, deterministic). |
| `src/perf/contracts/budget_check_v1.py` | contracts | `SCHEMA_VERSION=1`, `build_payload(BudgetVerdict)` — flattened D1 shape. |

**MODIFIED (additive only)**
| File | Change |
|---|---|
| `src/perf/domain/model.py` | Add `SeriesPoint`; add `GatedVerdict`; add `BudgetVerdict` + `GATE_*`; add `Verdict.series_points: Sequence[SeriesPoint] = ()`. |
| `src/perf/domain/ports.py` | Add `CommitLog` Protocol. |
| `src/perf/domain/regression.py` | `classify(...)` gains `series_points: Sequence[SeriesPoint] = ()`, threaded into both `Verdict(...)` returns. |
| `src/perf/adapters/analyzer_sql.py` | Add `_series_points(...)`; pass `series_points=` in both families. |
| `src/perf/adapters/registry.py` | Add `build_commit_log(repo_path=None, runner=None) -> CommitLog`. |
| `src/perf/cli/main.py` | Register the `budget-check` subcommand. |
| `tests/fakes.py` | Add `FakeCommitLog` (+ `FakeAnalyzer` if not already present — see §11). |

**FROZEN — MUST NOT change**: `contracts/compare_v1.py`, `cli/output/compare_pretty.py`, `cli/commands/compare.py`, `application/run_flow.py`, `run`'s store write path / schema. (`compare_v1`/`compare_pretty` verified to read only `series`/named fields, so §5's additive field cannot touch them.)

---

## 11. Test seams (RED-first; strict_tdd active, runner `./.venv/bin/pytest`)

Write each test RED before the implementing code.

**Unit — pure `domain/budget.evaluate` (`tests/unit/test_budget.py`)** — the fail-open/fail-closed matrix:
- regression present → `fail`, `offending_metrics` lists it, that metric `gated=True`.
- multiple regressions → `fail`, ALL aggregated into `offending_metrics` (not first-only).
- mixed regression + stable → `fail` (all-or-nothing), only the regression gated.
- all `stable`/`improvement` → `pass`, `offending_metrics == ()`.
- all `insufficient-data`, non-strict → `skipped` (fail-open), no offenders.
- all `insufficient-data`, `strict=True` → `fail`, every metric gated.
- mixed stable + insufficient, non-strict → `pass` (insufficient not gated).
- mixed stable + insufficient, `strict=True` → `fail` (insufficient gated).
- `improvement` never gates (regression-direction sanity).
- `calibration` passed through unchanged; never alters `gate_status`.

**Unit — `series_points` threading (`tests/unit/test_regression.py` additions)**:
- `classify(..., series_points=(SeriesPoint("a",1.0),))` echoes them onto the returned `Verdict`.
- default `()` when omitted (backward-compat).

**Contract — `tests/contract/test_budget_check_v1.py`** (independent of compare_v1):
- exact top-level keys `{schema_version, gate_status, strict, offending_metrics, verdicts}`; `schema_version == 1`.
- each verdict entry carries `gated` plus compare's per-metric fields; shape change without a version bump FAILS the test.
- `series_points`/`calibration` are ABSENT (pinned exclusions).

**Golden — `tests/golden/` (color forced off, fixed width, `FakeCommitLog`)**:
- summary PASS, summary FAIL (offending banner + `✗` rows), summary SKIPPED.
- `--verbose` summary auto-expanding a regression (with fixed commit subject).
- `--metric <name>` detail view: y-axis ticks + x-axis commit labels + HEAD marker + regression git context.
- open-right frame (no right border), blank line between rows.

**Integration — real CLI wiring (`tests/integration/test_cli_budget_check.py`)** via `FakeAnalyzer`/`FakeCommitLog` (and `FakeStore`/`FakeRunContextProvider`), asserting exit codes:
- confirmed regression → exit 1 (+ `--json` `gate_status == "fail"`, offenders listed).
- all stable → exit 0, `gate_status == "pass"`.
- insufficient-data, no flag → exit 0 `skipped`; SAME case `--strict` → exit 1 `fail`.
- unknown flow → exit 2 (before any analyzer construction).
- analyzer returns `None` (no history) → exit 2.
- analyzer raises → exit 3; verdict-render failure path → exit 3.
- verdict is ALWAYS printed before exit (assert stdout non-empty in every exit path).
- `--json` never affected by `--metric`/`--verbose`/color.

**Fakes**: reuse `tests/fakes.py`. Add `FakeCommitLog(subject: str | None = ...)`. Add `FakeAnalyzer(result: CompareResult | None, raises: Exception | None)` exposing `compare_latest(...)` if a compare-era one is not already present.

**Regression guard**: full suite (328 shipped tests) MUST stay green — the additive `Verdict.series_points` and `classify` kwarg default protect `compare`/`run`/`compare_v1`/`compare_pretty`.

---

## 12. Corner-case → behavior table (B1–B10)

Re-classifies `compare`'s C1–C9 into gate outcomes. Invariant: **never crashes; never exits 1 except a confirmed regression (or, under `--strict`, insufficient-data).**

| # | Corner case | compare verdict | Default (fail-open) | `--strict` (fail-closed) |
|---|---|---|---|---|
| B1 | No history / first-ever run of a KNOWN flow | all `insufficient-data` | `skipped`, **exit 0** | `fail`, **exit 1** ✱ |
| B2 | Unknown flow (config-unknown, or no rows) | — / `None` | usage error, **exit 2** | **exit 2** (same) |
| B3 | Single baseline commit (< `min_baseline_commits`) | `insufficient-data` | `skipped`, **exit 0** | `fail`, **exit 1** ✱ |
| B4 | All-equal values (zero variance, baseline==0) | `stable` | `pass`, **exit 0** | `pass`, **exit 0** |
| B5 | New metric absent from baseline | `insufficient-data` (that metric) | `pass` if other metrics gradeable, else `skipped`; **exit 0** | that metric gated → `fail`, **exit 1** |
| B6 | Device or mode never seen before | empty baseline ⇒ `insufficient-data` | `skipped`, **exit 0** | `fail`, **exit 1** ✱ |
| B7 | Mixed: some `regression`, some `stable` | mixed | `fail` (all-or-nothing), **exit 1**, all offenders aggregated | `fail`, **exit 1** |
| B8 | Warm-only vs cold-only (mode split) | `insufficient-data` | `skipped`, **exit 0** | `fail`, **exit 1** ✱ |
| B9 | Dev-bundle-only history | `insufficient-data` | `skipped`, **exit 0** | `fail`, **exit 1** |
| B10 | Confirmed single regression | `regression` | `fail`, **exit 1** | `fail`, **exit 1** |

✱ = the four cases the prompt highlights where `--strict` flips fail-open→fail-closed (B1/B3/B6/B8); B9 (and B5's insufficient metric) flip too. B2/B4/B7/B10 are strict-invariant.

---

## 13. Risks & open questions for the tasks phase

**Risks**
- **`series_points` double-write drift**: `_series_points` must order commits identically to `_sparkline_series` or the chart labels desync from `series`. Mitigation: factor the shared ordering once and have both consume it; a unit test asserts `len(series) == len(series_points)` and value equality. (Tasks: decide whether to refactor `_sparkline_series` to derive from `series_points` to guarantee a single ordering source.)
- **Additive `Verdict` field ordering**: `series_points` must be the LAST field with a default, after `floor`, or positional construction in shipped tests breaks. Low risk, pinned by the full-suite green gate.
- **`git log` cost in detail/verbose**: keep it to ONE call (HEAD subject only); x-axis labels use short shas from `series_points`, never per-point git calls. Fail-graceful to SHA-only.
- **Renderer scope creep**: the detail chart (y/x axes) is the most novel code; keep it a plain string assembler with the same normalization guard as compare's sparkline — do not grow a charting abstraction.
- **`skipped` vs `pass` semantics**: both exit 0; the distinction is informational (banner + `gate_status`). Confirm CI consumers key off exit code, not the word — documented in spec.

**Open questions (resolve in tasks/spec, not blocking design)**
1. Detail-chart glyph set: reuse compare's 8-level block chars for the multi-row chart, or braille for finer vertical resolution? (Braille is denser but harder to golden across fonts — lean block chars for determinism.)
2. `--metric` on a non-existent metric name: exit code — treat as usage (2) or render "not present" and exit per gate_status (0/1)? (Design leans: render + gate_status exit, since the flow itself is valid; confirm in spec.)
3. Whether `build_commit_log` should thread `repo_path` from config or default to CWD (compare/run derive git from CWD today — match that).

---

## 14. Traceability to locked decisions

- **D1** → §8 (flattened `budget_check_v1`, `gated` + top-level `gate_status`, independent contract test).
- **D2** → §9 (own renderer, open-right, spaced, colored, gate banner, HEAD header, deterministic; hand-rolled decision justified).
- **D3** → §3/§6/§7 (gate-fail is a RETURN; CLI maps fail→1; 3 only from caught runtime; always print before exit).
- **D4** → §3/§7/§12 (`--strict` implemented; single param flips fail-open→fail-closed).
- **D5** → §7/§9 (`--metric` detail view + `--verbose` auto-expand; bigger multi-line chart).
- **D6** → §4/§10 (`CommitLog` port + `commit_log_git` adapter, argv-list, fail-graceful, registered).
- **D7** → §2/§5 (additive `Verdict.series_points`/`SeriesPoint`; safe default; compare_v1/compare_pretty frozen).
