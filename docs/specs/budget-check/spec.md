# Specification: `budget-check` capability (Current — Phase 3 SHIPPED, CI gate slice)

**Consolidated from**: SDD artifacts `openspec/changes/budget-check/{proposal,spec,design,tasks}.md`
(Addendum Rev 2, decisions D1–D7), implemented across three chained PRs — `budget-check/pr-a-domain`
(shared domain plumbing), `budget-check/pr-b-app-contract` (application use-case + `budget_check_v1`
contract), `budget-check/pr-c-cli-renderer` (renderer + CLI command + corner cases + docs/demo).
Exact merge commit hashes are recorded here once every PR lands on `main` (mirrors
`openspec/specs/compare.md`'s "Consolidated from" convention).

**Status**: Phase 3 (CI gate slice) COMPLETE. Capability ships with the following behavior as the
authoritative current spec.

## Purpose

`perf compare <flow>` (SHIPPED) computes a per-metric regression verdict but is show-only — it never
exits `1`, by design (`openspec/specs/compare.md`). `perfvibe budget-check <flow>` spends that
reserved exit code: it reuses compare's already-shipped, corner-case-hardened `Analyzer.compare_latest`
verdict engine and applies ONE pure gate rule over the returned verdicts, turning a confirmed
`regression` into a non-zero exit so CI can block a merge.

**Critical invariant**: budget-check NEVER crashes and NEVER exits `1` except on a confirmed
`regression` (default mode) or an unprovable-safety case under `--strict`. `compare`'s behavior,
`compare_v1`, and `run`'s schema/write path are unchanged — budget-check is purely additive and reads
the `Analyzer` seam read-only.

**v1 scope**: a **RELATIVE regression gate only**. Absolute-ceiling budgets, combined relative+absolute
policy, and per-metric warn-vs-block severity are explicitly DEFERRED (see Scope table).

## Scope

| Concern | This capability | Status |
|---|---|---|
| `perf budget-check <flow>` command, gating on compare's `regression` verdict | budget-check | SHIPPED ✓ |
| Reuse of `Analyzer.compare_latest` / `CompareResult` / `regression.classify` wholesale (no re-derivation) | budget-check | SHIPPED ✓ |
| Exit `1` on a CONFIRMED `regression` only (the reserved code) | budget-check | SHIPPED ✓ |
| Fail-open (default): insufficient-data / no history / unseen device+mode / no-baseline metric → gate SKIPPED, exit `0` | budget-check | SHIPPED ✓ |
| `--strict` (fail-closed): the SAME insufficient-data cases → gate FAIL, exit `1` | budget-check | SHIPPED ✓ |
| All-or-nothing per invocation + full aggregation of every offending metric into `--json` | budget-check | SHIPPED ✓ |
| `budget_check_v1` `--json` contract: FLATTENED, own `schema_version=1`, independent of `compare_v1` | budget-check | SHIPPED ✓ |
| Exit codes `0`/`1`/`2`/`3` (mirrors `compare`'s `2`/`3` usage-error/runtime-error semantics) | budget-check | SHIPPED ✓ |
| Own pretty renderer: all metrics + sparklines + calibration footer + gate banner, open-right layout | budget-check | SHIPPED ✓ |
| `--metric <name>` detail view (larger chart, git context on regression) | budget-check | SHIPPED ✓ |
| `--verbose` auto-expand of regressed metrics on the summary view | budget-check | SHIPPED ✓ |
| Git commit-subject lookup (render-time, fail-graceful to sha-only) | budget-check | SHIPPED ✓ |
| Additive `Verdict.series_points` (baseline chart point → commit labels) | budget-check (shared domain change) | SHIPPED ✓ — backward-compatible; `compare_v1`/`compare_pretty` unaffected |
| Shared `--restart`/`--device` flags + warm/cold mode resolution (same as compare) | budget-check | SHIPPED ✓ |
| Absolute-ceiling budgets (per-metric hard ceiling in `perf.toml`) | budget-check (future slice) | DEFERRED |
| Combined relative + absolute policy (regression OR ceiling breach, with precedence) | budget-check (future slice) | DEFERRED |
| Per-metric warn-vs-block severity | budget-check (future slice) | DEFERRED — v1 is all-or-nothing per flow |
| `perf run` auto-invoking budget-check (run → gate chaining) | future CLI-layer seam | DEFERRED |
| Any change to `compare`'s or `run`'s behavior, `compare_v1`, or `run`'s schema/write path | N/A by design | OUT OF SCOPE |

## Requirements

### Requirement: Gate Command & Relative Rule

**Status**: SHIPPED ✓

`perf budget-check <flow>` SHALL compute compare's verdict by calling the SAME `Analyzer.compare_latest(flow, device_key, mode) -> CompareResult` seam `compare` uses — no statistic is re-derived. The system SHALL then apply ONE pure gate rule over the returned per-metric verdicts: if ANY metric's verdict is `regression`, the gate SHALL FAIL; if no metric regresses (all `stable`/`improvement`), the gate SHALL PASS.

#### Scenario: Any-metric-regression fails the gate
- GIVEN a flow whose verdicts include at least one metric classified `regression`
- WHEN `perf budget-check <flow>` runs
- THEN the gate status is `fail`

#### Scenario: All-stable-or-improvement passes the gate
- GIVEN a flow whose verdicts are all `stable` or `improvement` (no `regression`, no `insufficient-data`)
- WHEN `perf budget-check <flow>` runs
- THEN the gate status is `pass`

### Requirement: Exit-Code Contract

**Status**: SHIPPED ✓

The tool SHALL exit `1` ONLY when the gate status is `fail` from a CONFIRMED `regression` in default mode (or from an unprovable-safety case under `--strict` — see the `--strict` requirement). The tool SHALL exit `0` when the gate status is `pass`, AND on every fail-open `skipped` case in default mode. The tool SHALL exit `2` on a usage error (e.g. unknown flow). The tool SHALL exit `3` on a runtime or tooling failure (device/store/git-adapter error).

#### Scenario: Confirmed regression exits 1 (default mode)
- GIVEN a flow with at least one metric classified `regression`
- WHEN `perf budget-check <flow>` runs (no `--strict`)
- THEN the exit code is `1`

#### Scenario: Gate pass exits 0
- GIVEN a flow whose verdicts are all `stable`/`improvement`
- WHEN `perf budget-check <flow>` runs
- THEN the exit code is `0`

#### Scenario: Unknown flow exits 2
- GIVEN a flow name with no history at all
- WHEN `perf budget-check <flow>` runs
- THEN the exit code is `2`

#### Scenario: Runtime/tooling failure exits 3
- GIVEN a device, store, or git-adapter failure occurs during evaluation
- WHEN `perf budget-check <flow>` runs
- THEN the exit code is `3`

#### Scenario: insufficient-data, stable, and improvement never exit 1 in default mode
- GIVEN a flow whose verdicts are any mix of `insufficient-data`, `stable`, and `improvement`, with NO `regression` present
- WHEN `perf budget-check <flow>` runs without `--strict`
- THEN the exit code is `0`, never `1`

### Requirement: Fail-Open Default Behavior

**Status**: SHIPPED ✓

By default (no `--strict`), when the flow has no usable baseline to prove safety, the gate SHALL be SKIPPED and the tool SHALL exit `0`. This applies to: no history (first-ever run), unseen device+mode combination, insufficient baseline commits, and a metric present in the latest run with no baseline (no-baseline metric).

#### Scenario: No history fails open
- GIVEN a known flow whose only run is the one being evaluated (no prior baseline)
- WHEN `perf budget-check <flow>` runs (default mode)
- THEN the gate status is `skipped` and the exit code is `0`

#### Scenario: Unseen device+mode fails open
- GIVEN the evaluated device+mode combination has no prior history
- WHEN `perf budget-check <flow>` runs (default mode)
- THEN the gate status is `skipped` and the exit code is `0`

#### Scenario: Insufficient baseline commits fails open
- GIVEN fewer than `min_baseline_commits` qualifying baseline commits exist
- WHEN `perf budget-check <flow>` runs (default mode)
- THEN that metric's verdict is `insufficient-data`, the gate does not fail on it, and the exit code is `0`

#### Scenario: No-baseline metric fails open
- GIVEN a metric present in the latest run but absent from all baseline commits
- WHEN `perf budget-check <flow>` runs (default mode)
- THEN that metric does not gate the flow, and (absent any other regression) the exit code is `0`

### Requirement: `--strict` Fail-Closed Mode

**Status**: SHIPPED ✓

The `--strict` flag SHALL flip the fail-open default: on the SAME insufficient-data-class cases (no history, unseen device+mode, insufficient baseline commits, no-baseline metric), the gate SHALL FAIL and the tool SHALL exit `1` — "guilty until proven safe." Without `--strict`, the default fail-open behavior SHALL apply unchanged. `--strict` SHALL NOT alter the outcome for any metric that already classifies `regression`, `stable`, or `improvement`.

#### Scenario: No history — default passes open, --strict fails closed
- GIVEN a known flow whose only run is the one being evaluated (no prior baseline)
- WHEN `perf budget-check <flow>` runs once without `--strict` and once with `--strict`, on the identical input
- THEN the default run has gate status `skipped` and exit `0`; the `--strict` run has gate status `fail` and exit `1`

#### Scenario: No-baseline metric — default passes open, --strict fails closed
- GIVEN a metric present in the latest run but absent from all baseline commits, with no other metric regressing
- WHEN `perf budget-check <flow>` runs once without `--strict` and once with `--strict`, on the identical input
- THEN the default run exits `0`; the `--strict` run exits `1`

#### Scenario: --strict does not change an already-confirmed regression or a clean pass
- GIVEN a flow with a confirmed `regression` on one metric, and separately a flow with all `stable`/`improvement` verdicts and full baseline coverage
- WHEN `perf budget-check <flow> --strict` runs against each
- THEN the regression case still exits `1` and the clean-pass case still exits `0` — `--strict` only changes the insufficient-data-class outcome

### Requirement: All-or-Nothing Gating with Full Aggregation

**Status**: SHIPPED ✓

Any ONE metric classified `regression` SHALL fail the whole flow (a single pass/fail per invocation). The evaluator SHALL aggregate EVERY regressing metric before producing output — it SHALL NOT stop at the first offender — so the `--json` payload and pretty output report the full blast radius.

#### Scenario: Mixed metrics — two regress, one stable, gate fails and both are reported
- GIVEN a flow with three metrics where two classify `regression` and one classifies `stable`
- WHEN `perf budget-check <flow>` runs
- THEN the gate status is `fail`, the exit code is `1`, and BOTH regressing metrics are marked `gated: true` in the `--json` payload — not just the first one encountered

### Requirement: `budget_check_v1` `--json` Contract (Flattened)

**Status**: SHIPPED ✓

`--json` output SHALL be a FLATTENED payload, its OWN `schema_version=1`, independent of `compare_v1`: a top-level `gate_status` (`"pass" | "fail" | "skipped"`) and a flat `verdicts` array where EACH entry carries compare's per-metric verdict fields PLUS an added `gated: bool`. The payload SHALL NOT nest `compare_v1`'s shape under a key. The pretty-output gate banner SHALL NEVER appear in `--json`. The contract SHALL be pinned by a dedicated contract test, independent of `compare_v1`'s contract test — any shape change requires a `schema_version` bump.

#### Scenario: JSON payload is flat, not nested
- GIVEN a completed budget-check evaluation
- WHEN `--json` is requested
- THEN the payload has top-level `schema_version` and `gate_status`, and a flat `verdicts` array (no nested `compare` key wrapping a sub-payload)

#### Scenario: Every verdict entry carries a gated flag
- GIVEN a payload with multiple metrics, some regressing and some not
- WHEN `--json` is inspected
- THEN every entry in `verdicts` has a `gated: bool`, `true` only for metrics that caused the gate to fail

#### Scenario: Contract test fails on shape change without a schema_version bump
- GIVEN a code change alters the shape of the `budget_check_v1` payload (field added/removed/retyped)
- WHEN the `budget_check_v1` contract test runs without a corresponding `schema_version` bump
- THEN the contract test fails

#### Scenario: Gate banner never appears in --json
- GIVEN any gate status (`pass`, `fail`, or `skipped`)
- WHEN `--json` is requested
- THEN no pretty-output banner text or ANSI/formatting artifact appears in the payload

#### Scenario: budget_check_v1 shape is pinned independently of compare_v1
- GIVEN `compare_v1`'s contract is unchanged
- WHEN `budget_check_v1`'s contract test runs
- THEN it validates against its OWN schema, with no dependency on `compare_v1`'s contract test passing or failing

### Requirement: Pretty Output (Own Renderer)

**Status**: SHIPPED ✓

budget-check SHALL render its OWN pretty view — it SHALL NOT reuse `compare_pretty.py` (which stays frozen). The view SHALL show ALL metrics (not just offenders), sparklines, the calibration/config-sanity footer, and a gate banner (`PASS`/`FAIL`/`SKIPPED`). Layout SHALL be open-right (top rule, bottom rule, left rail only — no right border). Rows SHALL be spaced (a blank line between metric rows) so sparklines never overlap vertically. `regression` rows and a `fail` gate banner SHALL be visually emphasized WITHOUT relying on color alone — a glyph (`✗`/`✓`/`·`) plus the STATUS word SHALL always be present. The header SHALL show `HEAD <short-sha> (<branch>)`. Rendering SHALL be deterministic (fixed width, color forced off) for golden tests, mirroring compare's existing convention.

#### Scenario: Pretty view shows all metrics with a gate banner
- GIVEN a completed budget-check evaluation with a mix of verdicts
- WHEN pretty output renders (no `--json`)
- THEN every metric appears (not only offenders), each with a sparkline, and a gate banner shows the overall `PASS`/`FAIL`/`SKIPPED` status

#### Scenario: Golden output is deterministic with color off
- GIVEN a fixed input dataset and a fixed terminal width
- WHEN pretty output renders with color forced off, twice in a row
- THEN the two renders are byte-identical, and every regression/fail marker is legible via glyph + status word alone (no color-only signal)

### Requirement: `--metric` Detail View and `--verbose` Auto-Expand

**Status**: SHIPPED ✓

`--metric <name>` SHALL render a single-metric drill-down: a larger chart with y-axis value ticks, x-axis per-commit labels, and HEAD marked. On a `regression`, the detail view SHALL include git context (see the Git Context requirement). `--verbose` on the default summary view SHALL auto-expand each regressed metric inline (showing its detail-view content within the summary), without requiring `--metric`. Both flags are additive; the default summary view SHALL remain compact when neither is passed.

`--metric` splits by cause: a name that is NOT a known metric for the flow (a typo) is a **usage error, exit 2**, with the error message listing valid metric names; a name that IS valid but has no data in the latest run renders normally ("no data for this metric in this run") and keeps normal gate-status exit semantics — it never exits `2`.

#### Scenario: --metric renders a single-metric detail chart
- GIVEN a flow with multiple metrics
- WHEN `perf budget-check <flow> --metric <name>` runs
- THEN only the named metric is shown, with a larger chart carrying y-axis ticks, per-commit x-axis labels, and HEAD marked

#### Scenario: --verbose auto-expands regressed metrics in the summary
- GIVEN a flow with one regressing metric among several
- WHEN `perf budget-check <flow> --verbose` runs
- THEN the summary view shows all metrics compactly EXCEPT the regressed one, which is auto-expanded inline with its detail content

#### Scenario: --metric with an unknown name is a usage error
- GIVEN a flow whose verdicts do not include a metric named `<typo>`
- WHEN `perf budget-check <flow> --metric <typo>` runs
- THEN the exit code is `2` and stderr lists the flow's valid metric names

#### Scenario: --metric with a valid name and no data this run never exits 2
- GIVEN a flow whose verdicts include a metric with no usable data in this run (e.g. `latest_value is None`)
- WHEN `perf budget-check <flow> --metric <that-metric>` runs
- THEN the detail view renders a "no data for this metric in this run" message and the exit code follows the OVERALL `gate_status`, never `2`

### Requirement: Git Context on Regression

**Status**: SHIPPED ✓

When the `--metric` detail view shows a `regression`, it SHALL display the regressing HEAD commit's sha and branch (sourced from `RunContext`, no new data) and its commit subject, fetched AT RENDER TIME via a git adapter behind a port. If the subject cannot be fetched (repo/commit unavailable), the view SHALL fail gracefully to sha-only display — it SHALL NOT crash and SHALL NOT abort the whole command. Baseline chart points SHALL be labeled with their originating commit via the additive `series_points` field.

#### Scenario: Commit subject is shown when available
- GIVEN a regressing metric whose HEAD commit exists in the local git repository
- WHEN `perf budget-check <flow> --metric <name>` runs
- THEN the detail view shows the HEAD sha, branch, and commit subject

#### Scenario: Commit subject unavailable falls back to sha-only, no crash
- GIVEN a regressing metric whose HEAD commit is unavailable to the git adapter (e.g. shallow clone, detached history, missing repo)
- WHEN `perf budget-check <flow> --metric <name>` runs
- THEN the detail view shows the sha (and branch, if known) without a subject, and the command completes without crashing or raising an uncaught exception

#### Scenario: Baseline chart points are labeled by commit
- GIVEN a baseline series with multiple points
- WHEN the `--metric` detail chart renders
- THEN each baseline point is labeled with its originating commit, sourced from the additive `series_points` field

### Requirement: Non-Mutation Invariant

**Status**: SHIPPED ✓

budget-check SHALL be purely additive. `compare`'s behavior, `compare_v1`'s frozen contract, and `run`'s schema/write path SHALL be unchanged. budget-check SHALL access the `Analyzer` seam READ-ONLY — it introduces no new adapter behavior on the write path, no schema migration, and no retrofit of `compare_v1`. The gate decision SHALL live in a pure domain rule with no I/O; any git lookup SHALL be accessed only behind a port, invoked via an argv list (never `shell=True`).

#### Scenario: Existing compare and run tests still pass
- GIVEN the full pre-existing test suite for `compare` (Phase 2) and `run` (Phase 1)
- WHEN budget-check is added to the codebase
- THEN every pre-existing `compare` and `run` test still passes unmodified

#### Scenario: compare never exits 1 after budget-check ships
- GIVEN a metric that would classify `regression` under `perf compare <flow>`
- WHEN `perf compare <flow>` runs (not `budget-check`)
- THEN the exit code is still `0` — `compare` remains show-only and is unaffected by budget-check's existence

## Corner-Case Matrix

`perf budget-check <flow>` SHALL handle every degenerate-history and tooling corner case gracefully: it SHALL NEVER crash, and it SHALL NEVER exit `1` except on a confirmed `regression` (default mode) or an unprovable-safety case under `--strict`. This matrix RE-CLASSIFIES compare's C1–C9 corner cases (`openspec/specs/compare.md`) into budget-check gate outcomes and adds tooling-failure case B10.

| # | Corner case | Default mode | `--strict` mode |
|---|---|---|---|
| B1 | No history / first-ever run of a known flow | gate `skipped`, exit `0` | gate `fail`, exit `1` |
| B2 | Unknown flow (no rows at all) | usage error, exit `2` (unaffected by `--strict`) | usage error, exit `2` |
| B3 | Insufficient baseline commits (below `min_baseline_commits`) | gate `skipped` for that metric, exit `0` (absent other regressions) | gate `fail`, exit `1` |
| B4 | All metrics `stable` | gate `pass`, exit `0` | gate `pass`, exit `0` |
| B5 | One metric `regression`, rest `stable` | gate `fail`, exit `1` | gate `fail`, exit `1` |
| B6 | New metric present in latest run, absent from baseline (no-baseline metric) | does not gate the flow by itself, exit `0` (absent other regressions) | gates the flow, exit `1` |
| B7 | Dropped metric (in baseline, absent from latest run) | skipped, non-fatal — no effect on gate status | skipped, non-fatal — no effect on gate status |
| B8 | Unseen device+mode combination | gate `skipped`, exit `0` | gate `fail`, exit `1` |
| B9 | Dev-bundle-only history (baseline entirely excluded) | gate `skipped` (fail-open), exit `0` | gate `fail`, exit `1` |
| B10 | Render/tooling failure (e.g. store or git-adapter error during evaluation or rendering) | exit `3` — never silently `0` or `1` | exit `3` — never silently `0` or `1` |

**Invariant**: budget-check NEVER crashes and NEVER exits `1` except on a confirmed `regression` (default mode) or a `--strict`-mode unprovable-safety case (B1, B3, B6, B8, B9 under `--strict`).

**Implementation note (B7)**: `adapters/analyzer_sql.py` silently omits a metric that exists ONLY in the baseline and never appears in the latest run — no `Verdict`/`GatedVerdict` is produced for it at all (documented, unchanged behavior from `compare`'s Phase 2 shipped code). `--metric`'s "valid but no data in this run" case (above) is therefore defined over metrics that DO have a `GatedVerdict` this run but `latest_value is None` (e.g. a fully warm-up-dropped `system_sample` metric) — a superset that includes B7's spirit without requiring a new store read-model (design §4: no new port for the gate).

#### Scenario: B1 — no history, default fail-open vs strict fail-closed
- GIVEN a known flow whose only run is the one being evaluated
- WHEN `perf budget-check <flow>` runs once default and once with `--strict`
- THEN default exits `0` (gate `skipped`); `--strict` exits `1` (gate `fail`)

#### Scenario: B2 — unknown flow is always a usage error
- GIVEN a flow name with no rows at all in the store
- WHEN `perf budget-check <flow>` runs, with or without `--strict`
- THEN the exit code is `2` in both cases

#### Scenario: B7 — dropped metric is skipped, not fatal, and does not affect the gate
- GIVEN a metric present in the baseline but absent from the latest run, with all other metrics `stable`
- WHEN `perf budget-check <flow>` runs
- THEN the dropped metric is skipped/noted, the command does not crash, and the gate status is `pass`

#### Scenario: B9 — dev-bundle-only history fails open by default
- GIVEN every prior run for the flow is a dev-bundle run (all excluded from the baseline)
- WHEN `perf budget-check <flow>` runs without `--strict`
- THEN the gate status is `skipped` and the exit code is `0`

#### Scenario: B10 — render/tooling failure exits 3, never silently 0 or 1
- GIVEN the git-adapter or store raises an unexpected runtime error during evaluation or rendering
- WHEN `perf budget-check <flow>` runs
- THEN the exit code is `3`, and the failure is surfaced (never silently mapped to `0` or `1`)
