# Proposal: `perf budget-check` capability (Phase 3 — the CI gate)

## Intent

`perf compare` (Phase 2, SHIPPED) computes a per-metric regression verdict but is **show-only**: it exits `0` even when a metric regresses, because reporting is not gating (`openspec/specs/compare.md:11`). There is no pre-merge gate — a developer or CI job cannot make a build FAIL on a performance regression today. Exit code `1` has been deliberately reserved, across `compare`'s spec, `README.md:85`, and `CLAUDE.md`, exclusively for this future `budget-check` gate.

This change spends that reserved code. `perf budget-check <flow>` reuses compare's already-shipped, corner-case-hardened verdict engine and turns its `regression` verdict into a **non-zero exit** so CI can block a merge. It closes the `run → compare → budget-check` loop: `run` persists, `compare` shows, `budget-check` gates.

**Locked decision**: v1 is a **RELATIVE regression gate only** — it fails (`exit 1`) when compare's verdict for any metric is `regression`. Absolute-ceiling budgets and combined relative+absolute policy are DEFERRED (they require a net-new config surface and a precedence policy — real design risk not warranted for the MVP). This mirrors how `compare` itself shipped a conservative first slice and deferred `budget-check`.

**Success looks like**: a CI job runs `perf budget-check <flow>` after `perf run`, gets `exit 1` on a confirmed regression (build red), `exit 0` otherwise (build green), and can machine-read *which* metric(s) tripped the gate out of a versioned `--json` payload — with zero new false-red failures on flows that simply lack enough history yet.

## Scope

| Concern | This capability | Status |
|---|---|---|
| `perf budget-check <flow>` command: gate on compare's regression verdict | budget-check | IN SCOPE ✓ |
| Exit `1` on a CONFIRMED `regression` (the reserved code, finally spent) | budget-check | IN SCOPE ✓ |
| Reuse `Analyzer.compare_latest` / `CompareResult` / `regression.classify` wholesale | budget-check | IN SCOPE ✓ |
| Fail-OPEN (`exit 0`) on insufficient-data / no history / unseen device+mode / no-baseline metric | budget-check | IN SCOPE ✓ |
| All-or-nothing per invocation: ANY metric `regression` fails the whole flow | budget-check | IN SCOPE ✓ |
| Aggregate ALL regressions before exiting (do not stop at the first) for `--json` | budget-check | IN SCOPE ✓ |
| Versioned `--json` gate contract (`budget_check_v1`, OWN `schema_version=1`) with `gate_status` + offending metric(s) | budget-check | IN SCOPE ✓ |
| Exit codes `0`/`1`/`2`/`3` — `1` reserved for confirmed regression only | budget-check | IN SCOPE ✓ |
| Shared `--restart`/`--device` flags + warm/cold mode resolution (same as compare) | budget-check | IN SCOPE ✓ |
| **Absolute-ceiling budgets** (per-metric hard ceiling in `perf.toml`, e.g. "cold start MUST be < 800ms") | budget-check (future slice) | **DEFERRED** — needs a net-new per-metric config surface + read model; out of MVP risk budget |
| **Combined relative + absolute policy** (gate on regression OR ceiling breach, with precedence rules) | budget-check (future slice) | **DEFERRED** — depends on absolute budgets landing first; needs a documented combination/precedence policy |
| **Per-metric warn-vs-block policy** (some metrics gate, others only warn) | budget-check (future slice) | **DEFERRED** — v1 is all-or-nothing per flow; per-metric severity is additive to `BudgetVerdict` later |
| **`perf run` auto-invoking budget-check** (run → gate chaining) | future CLI-layer seam | **DEFERRED** — same deferral `compare` carries for run→compare auto-chaining |
| **Fail-CLOSED mode for insufficient-data** (`exit 1` when history can't prove safety) | budget-check (future opt-in) | **DEFERRED** — v1 is fail-open only; a `--strict`/`--fail-closed` opt-in is a clean future flag |
| Change to `run`'s or `compare`'s tables / write path | N/A by design | **OUT OF SCOPE** — purely additive, no schema migration |
| Retrofit of `compare_v1`'s frozen contract | N/A by design | **OUT OF SCOPE** — `compare_v1` is shipped/frozen; budget-check gets its OWN contract module |

## Capabilities

### New Capabilities
- `budget-check`: the CI gate — consumes compare's `CompareResult`, applies a pure gate rule, and maps the outcome to an exit code (`1` on regression, `0` fail-open otherwise) plus a versioned `budget_check_v1` `--json` payload.

### Modified Capabilities
- None. `compare` and `run` are untouched. `compare` remains show-only and NEVER exits `1`; budget-check reuses its `Analyzer` seam read-only. The reserved-exit-1 promise in `compare`'s spec is *fulfilled by a sibling capability*, not altered.

## Approach

**Relative-gate design.** budget-check does NOT re-derive any statistics. It calls the EXISTING `Analyzer.compare_latest(flow, device_key, mode) -> CompareResult` (the same seam `compare` uses), then applies ONE pure gate rule over the returned per-metric verdicts:

- If any metric's verdict is `regression` → gate FAIL → `exit 1`.
- If no metric regresses (all `stable`/`improvement`) → gate PASS → `exit 0`.
- If the flow has no usable baseline (`insufficient-data`, no history, unseen device+mode, no-baseline metric) → gate SKIPPED → **fail-open** `exit 0`.

**Fail-open rationale**: a CI gate that flakes red on flows that simply lack history is worse than useless — it trains engineers to ignore the gate. New flows must never block on their first runs; they start gating only once enough baseline exists. Fail-closed (`--strict`) is a clean future opt-in, deferred.

**All-or-nothing rationale**: CI needs a single pass/fail per invocation. Any one metric regressing fails the whole flow. But the evaluator AGGREGATES every regression into `--json` before exiting — it does not stop at the first — so the payload reports the full blast radius, not just the first offender. Per-metric warn-vs-block severity is a documented v1 limitation.

**Files touched, by hexagonal layer** (all additive; nothing shipped changes behavior):

| Area | Impact | Description |
|------|--------|-------------|
| `src/perf/domain/budget.py` | New | Pure gate rule: `CompareResult -> BudgetVerdict` (`gate_status ∈ {pass, fail, skipped}`, offending metrics). No I/O, no adapter imports — the ONE place the gate decision lives (locality of behavior). |
| `src/perf/application/budget_check_flow.py` | New | `BudgetCheckUseCase` — orchestrates the `Analyzer` port + the pure gate rule; mirrors `run_flow.py`'s exception→exit-code mapping (`UsageError`→2, runtime failure→3, gate-fail signaled to CLI→1). |
| `src/perf/cli/commands/budget_check.py` | New | `perfvibe budget-check <flow>` typer command; mirrors `cli/commands/compare.py` wiring (build context/store/analyzer), shares `--restart`/`--device` + warm/cold resolution; owns the `0/1/2/3` exit mapping and pretty vs `--json` rendering. |
| `src/perf/cli/main.py` | Modified | Register the new `budget-check` subcommand (only wiring change). |
| `src/perf/contracts/budget_check_v1.py` | New | OWN `schema_version=1` contract. Wraps/embeds compare's verdict shape and ADDS gate fields (`gate_status`, offending metric(s)). NOT a retrofit of `compare_v1` — its contract test pins its OWN shape independently. |

**Reuse of the Analyzer seam**: no new port, no new adapter, no new store read-model, no new config keys. budget-check reuses `threshold_pct` / floors / `baseline_n` / `min_baseline_commits` exactly as compare resolves them — the gate is a thin, pure decision layer on top of an already-tested verdict engine. A new port/config surface is introduced ONLY when absolute-ceiling budgets land (rule-of-three; no speculative extension points now).

## Non-Goals / Explicit Deferrals

- **NOT** absolute-ceiling budgets, and **NOT** combined relative+absolute — those need a per-metric config surface and precedence policy; deferred as a clearly-scoped future slice (the same additive pattern by which `compare` itself grew from `run`).
- **NOT** per-metric warn-vs-block — v1 gates the whole flow or nothing.
- **NOT** fail-closed on insufficient-data — v1 is fail-open only; `--strict` is a future opt-in.
- **NOT** `perf run` auto-invoking budget-check — future CLI-layer seam.
- **NOT** any modification to `compare_v1`, `compare`'s behavior, or `run`'s schema/write path.

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Scope creep toward absolute budgets mid-implementation | Med | Absolute + combined explicitly DEFERRED in the scope table; v1 gate rule reads ONLY compare's existing verdicts — no new config key is even loaded. Intentional, documented boundary. |
| Contract drift vs `compare_v1` (accidental coupling or retrofit) | Low | `budget_check_v1` is a SEPARATE module with its OWN `schema_version=1` and its OWN contract test; `compare_v1` stays frozen. Embedding compare's verdict shape is by-value, not by mutation. |
| All-or-nothing coarseness annoys teams with one noisy metric | Med (accepted) | Documented v1 limitation; `--json` reports EVERY offending metric so teams can triage; per-metric severity is a scoped future slice. Intentional for the MVP. |
| Fail-open means brand-new flows never block initially | Med (accepted, by design) | This is the DESIRED behavior — a flaky-red gate is worse than no gate. A flow gates only once it has baseline. Fail-closed `--strict` is a deferred opt-in. Documented, not a defect. |
| Users assume `exit 1` means "any non-green verdict" | Low | `1` fires ONLY on a confirmed `regression`; `insufficient-data`/`stable`/`improvement` all exit `0`. Pinned by the corner-case matrix (B1–B10 from exploration) and contract test. |

## Corner Cases (to be formalized in spec)

The exploration defines a budget-check corner-case matrix (B1–B10) that RE-CLASSIFIES compare's C1–C9 into gate outcomes — e.g. C1 no-history → `exit 0` fail-open; C2 unknown flow → `exit 2` (same usage error as compare); C3 insufficient baseline commits → `exit 0` fail-open; mixed metrics (some `regression`, some `stable`) → `exit 1` (all-or-nothing). These are carried into the spec phase as formal scenarios; the invariant is **budget-check never crashes and never exits `1` except on a confirmed `regression`**.

## Rollback Plan

budget-check is purely additive: one new pure domain module, one new use-case, one new CLI command + registration, one new contract module. No schema migration, no change to `run`'s or `compare`'s code or write path. Rollback = revert the change branch; `run` and `compare` behavior are untouched. If only the renderer misbehaves, `--json` remains the stable machine contract.

## Dependencies

- Phase 2 `compare` (SHIPPED) — the `Analyzer` port, `CompareResult`, and `regression.classify` verdict engine budget-check reuses wholesale.
- Phase 1 `run` (SHIPPED) — the persisted data compare reads.
- No new runtime libraries (stdlib + sanctioned `typer`/`rich`; existing test stack).

## Open Questions for Spec / Design

1. **Exact `budget_check_v1` payload shape**: confirm the wrapper form — does it embed the full compare verdict block verbatim under a key (e.g. `compare`) plus top-level `gate_status`/`offending_metrics`, or a flattened per-metric list with an added `gated: bool`? (Design decision; contract test pins whichever is chosen.)
2. **Pretty output for the gate**: does budget-check render its own gate-focused pretty view (PASS/FAIL banner + offending metrics), or reuse compare's sparkline renderer plus a gate footer? (UX decision — `--json` is unaffected either way.)
3. **Failure-type modeling**: what domain-level signal does `BudgetCheckUseCase` raise/return to let the CLI distinguish `1` (gate fail) from `3` (runtime error) cleanly, mirroring `run_flow.py`'s exception→exit mapping without conflating a normal gate-fail with an error?
4. **`--strict`/fail-closed flag surface**: reserve the flag name now (spec-visible, unimplemented) or leave it entirely to the future slice?

## Success Criteria

- [ ] `perf budget-check <flow>` exits `1` on a confirmed `regression`, `0` otherwise (including all fail-open cases), `2` on usage error, `3` on runtime error.
- [ ] Fail-open verified: insufficient-data / no history / unseen device+mode / no-baseline metric all exit `0`.
- [ ] All-or-nothing verified: any one metric regressing fails the flow; `--json` aggregates ALL offending metrics, not just the first.
- [ ] `budget_check_v1` `--json` carries its OWN `schema_version=1` with `gate_status` + offending metric(s); contract test pins its shape independently of `compare_v1`.
- [ ] `compare_v1`, `compare` behavior, `run` schema/write path all unchanged (all Phase 1 + Phase 2 tests still pass).
- [ ] The gate decision lives in ONE pure `domain/budget.py` module with no adapter imports / no I/O.
- [ ] Corner-case matrix B1–B10 covered, each RED-then-GREEN; budget-check never crashes and never exits `1` except on a confirmed regression.

---

## Addendum (Rev 2) — Locked interactive design decisions

These SUPERSEDE any conflicting item above. Decided with the user in an interactive SDD session (2026-07-23). Spec and design MUST honor these.

### D1 — `--json` shape: FLATTENED (not embedded)
`budget_check_v1` is a single flat per-metric list, each entry carrying compare's verdict fields PLUS a `gated: bool`, with a top-level `gate_status` (`"pass" | "fail" | "skipped"`). It does NOT nest `compare_v1`'s payload under a key. Rationale: self-contained, gate-first, and its contract test pins its OWN shape independently of `compare_v1` (avoids the contract-drift coupling). Example:
```json
{ "schema_version": 1, "gate_status": "fail",
  "verdicts": [ { "metric": "checkout", "status": "regression", "gated": true, "delta_pct": 20.0, ... },
                { "metric": "fps_avg", "status": "stable", "gated": false, ... } ] }
```

### D2 — Pretty output: budget-check's OWN renderer (compare stays frozen)
budget-check does NOT reuse `compare_pretty.py` (that would force changes to compare's frozen golden files). It gets its own renderer showing the SAME full data (all metrics, sparklines, calibration footer) plus a gate banner. Visual spec:
- **Open-right layout**: top rule + bottom rule + a left rail (`│`) only. NO right border (wide sparkline glyphs desync monospace alignment — leaving the right open is both nicer and correct).
- **Spaced rows**: a blank line between metric rows so sparklines never overlap vertically.
- **Color by status**: regression rows red; gate footer red (fail) / green (pass) / dim (skipped). Emphasis never depends on color alone (a `✗`/`✓`/`·` glyph + the STATUS word carry it too), mirroring compare's existing rule.
- **Header** carries `HEAD <short-sha> (<branch>)`.
- **Deterministic for golden tests**: rendered at a fixed width with color forced off, exactly like compare's goldens.

### D3 — Exit `1` vs `3` mechanism (implementation-locked)
A gate FAIL is a normal RETURN VALUE, not an exception: the use-case returns a `BudgetVerdict` (`gate_status ∈ {pass, fail, skipped}`); the CLI maps `fail → exit 1`. Exit `3` comes ONLY from a caught runtime exception (device/store/tooling failure), exit `2` from a usage error — mirroring `run_flow.py`'s exception→exit mapping. The CLI ALWAYS prints the verdict (pretty or `--json`) before exiting — never silent, in any state.

### D4 — `--strict` (fail-closed): IN SCOPE for v1, implemented (moved from DEFERRED)
`--strict` flips the fail-open default: on `insufficient-data` / no-history / unseen-device+mode / no-baseline metric, `--strict` makes the gate FAIL (`exit 1`, "guilty until proven safe"). Default (no flag) stays fail-open (`exit 0`). Implemented for real in v1 — NOT merely reserved — because a flag that is documented but inert breeds confusion and false expectations.

### D5 — Per-metric detail view: `--metric <name>` (new)
A drill-down view for one metric with a LARGER chart (multi-line, y-axis with value ticks, x-axis with per-commit labels, HEAD marked) and, on a regression, git context. `--verbose` on the summary view auto-expands regressed metrics inline. Both are additive; the default summary view stays compact.

### D6 — Git context on regression (new)
- **Regressing commit**: HEAD `git_commit` + `git_branch` — ALREADY in `RunContext`, no new data.
- **Commit subject/message**: fetched AT RENDER TIME via a small git adapter behind a new port (e.g. `CommitLog.subject(sha) -> str | None` running `git log -1 --format=%s <sha>` as an argv list, never `shell=True`). Fails GRACEFULLY to SHA-only when the repo/commit is unavailable. Message is NOT persisted (no schema migration).

### D7 — Baseline commit labels: additive `series_points` on `Verdict` (v1)
The chart labels each baseline point with its commit. The raw per-commit rows already carry `git_commit` (the pre-collapse baseline class in `domain/model.py`), but `Verdict.series` is bare floats. Add an ADDITIVE `series_points: Sequence[SeriesPoint]` (commit + value) threaded from the `Analyzer` (`analyzer_sql.py`) through `CompareResult` into `Verdict`. Additive with a safe default — `compare_v1` stays frozen (it simply does not emit the new field) and `compare_pretty` keeps using `series`. The domain change is shared but backward-compatible.

### Scope-table deltas from Rev 2
- `--strict` / fail-closed: **DEFERRED → IN SCOPE ✓**
- ADD IN SCOPE: own rich-based renderer (open-right, spaced, colored, gate banner); `--metric <name>` detail view + `--verbose` auto-expand; git commit-subject lookup adapter (render-time, fail-graceful); additive `Verdict.series_points`.
- Everything else in Rev 1 stands. `compare`/`run` behavior + `compare_v1` remain untouched and frozen.
