# Tasks: `budget-check` capability (Phase 3 — the CI gate)

Grounded in spec `openspec/changes/budget-check/spec.md`, design `openspec/changes/budget-check/design.md`,
proposal `openspec/changes/budget-check/proposal.md` (Addendum Rev 2, decisions D1–D7, locked 2026-07-23).
Scope: `perf budget-check <flow>` only — a relative regression gate reusing `compare`'s `Analyzer.compare_latest`
seam wholesale. `compare`, `compare_v1`, `compare_pretty`, and `run`'s schema/write path stay FROZEN.

**STRICT TDD MODE ACTIVE.** Test runner: `./.venv/bin/pytest` (no bare `pytest`/`python3.11` on PATH — always
the venv binary). Baseline: 328 tests passing. CI gates: `ruff check`, `ruff format --check`, `mypy src/perf`,
93% coverage floor. Every `GREEN` task is preceded by its `RED` test task — never a `GREEN` without a prior `RED`.
`INFRA` tasks add shared test doubles (`tests/fakes.py`) with no production-code pairing of their own; they sit
immediately before the first `RED` task that consumes them. `CHECKPOINT` tasks are verification gates, not new
production code.

**Newly resolved (fold into Phase 3):** `--metric <name>` splits by cause — a name that is NOT a known metric
for the flow is a **usage error, exit 2**, with the error message listing valid metric names; a name that IS
valid but has no data in the latest run (corner case B7) **renders normally** ("no data for this metric in this
run") and keeps normal gate-status exit semantics (never exit 2). These are two distinct RED/GREEN pairs.

**Design risks made explicit here:**
1. `series_points` ordering must match `series` (the sparkline) exactly — Task 1.5 pins this with a dedicated
   length + value-equality test before any consumer (the detail chart) is built.
2. `Verdict.series_points` must be the LAST defaulted field — Task 1.7 is a full-suite checkpoint confirming all
   328 prior tests stay green with zero modification to their assertions.
3. The detail-chart y/x-axis renderer (Task 3.6) is deliberately a plain deterministic string assembler — no
   charting abstraction (rule-of-three), same normalization guard as `compare_pretty`'s sparkline.

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | ~2150–2450 (prod ~860–950, tests ~1150–1350, docs/demo ~50–70) across 3 chained PRs |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR-A → PR-B → PR-C |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending (orchestrator/user decision required before apply) |

Decision needed before apply: **Yes**
Chained PRs recommended: **Yes**
400-line budget risk: **High**

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Rollback boundary |
|---|---|---|---|---|
| 1 | Shared domain plumbing: additive `series_points` threading (model → regression → analyzer_sql), pure gate rule (`domain/budget.py`), `CommitLog` port + `GitCommitLog` adapter + registry factory | PR-A (~750–850 ln: prod ~350, tests ~450) | `./.venv/bin/pytest tests/unit/test_model.py tests/unit/test_regression.py tests/unit/test_budget.py tests/unit/test_domain_boundary.py tests/unit/test_commit_log_git.py tests/integration/test_analyzer_sql.py tests/integration/test_registry.py -q` | Revert `domain/model.py` (`SeriesPoint`, `GatedVerdict`, `BudgetVerdict`, `GATE_*`, `Verdict.series_points`), `domain/regression.py` kwarg, `adapters/analyzer_sql.py` `_series_points`, `domain/budget.py`, `domain/ports.py` `CommitLog`, `adapters/commit_log_git.py`, `adapters/registry.py::build_commit_log`, `tests/fakes.py` additions. Nothing consumes any of this yet — `compare`/`run` untouched, no CLI wiring exists |
| 2 | Application use-case (`BudgetCheckUseCase`) + own `budget_check_v1` `--json` contract | PR-B (~350–450 ln: prod ~150, tests ~250) | `./.venv/bin/pytest tests/unit/test_budget_check_flow.py tests/contract/test_budget_check_v1.py -q` | Revert `application/budget_check_flow.py`, `contracts/budget_check_v1.py`; PR-A remains usable standalone (pure domain + adapters, no orchestration wired to a CLI) |
| 3 | Own hand-rolled renderer (summary + detail chart) + CLI command + exit-code/corner-case matrix (B1–B10) + `--metric` typo-vs-no-data split + docs + demo | PR-C (~950–1150 ln: prod ~450, tests ~650, docs/demo ~50–70) | `./.venv/bin/pytest tests/golden/test_budget_check_pretty_golden.py tests/integration/test_cli_budget_check.py -q` | Revert `cli/output/budget_check_pretty.py`, `cli/commands/budget_check.py`, `cli/main.py` registration, `tests/golden/test_budget_check_pretty_golden.py`, `tests/integration/test_cli_budget_check.py`, doc edits, `examples/demo-budget-check/`; PR-A/PR-B remain usable standalone (importable, tested, just not exposed as a subcommand) |

> Rationale for the split: PR-A is the highest-risk shared surface (`Verdict.series_points` touches the domain
> object `compare`/`run` both already consume) — landing it alone, full-suite-green-gated, isolates that risk
> from the net-new CLI surface. PR-B is small and low-risk (pure orchestration + a contract module with no I/O
> of its own). PR-C is the largest because the hand-rolled detail chart (design risk #3) and the 10-row B1–B10
> corner-case matrix (mirroring `compare`'s C1–C9 precedent) are both inherently test-heavy. If PR-C still runs
> too large in review, `--metric` detail-view tasks (3.5–3.8, 4.3–4.4) are a clean PR-C2 follow-up split point —
> the summary view + exit-code matrix (3.1–3.4, 4.1–4.2, 4.5–4.6) can land and gate CI on its own first.

## Phase 1: Shared domain plumbing — `series_points` + gate rule + `CommitLog` (PR-A)

- [x] 1.0 CHECKPOINT: run `./.venv/bin/pytest -q` and confirm the pre-existing 328 tests pass before any change (baseline snapshot for Task 1.7's diff).
- [x] 1.1 RED: `tests/unit/test_model.py` (extend) — `SeriesPoint(commit, value)` frozen dataclass constructs and compares by value; `Verdict.series_points` defaults to `()`; a `Verdict(...)` constructed with ONLY its pre-existing positional/keyword args (no `series_points`) still succeeds and reads `series_points == ()` (backward-compat pin, design risk #2).
- [x] 1.2 GREEN: `domain/model.py` — add `SeriesPoint` (frozen); append `series_points: Sequence[SeriesPoint] = ()` as the LAST field on `Verdict`, after `floor`.
- [x] 1.3 RED: `tests/unit/test_regression.py` (extend) — `classify(..., series_points=(SeriesPoint("a", 1.0),))` echoes the tuple onto the returned `Verdict` in BOTH the `insufficient-data` early-return path and the normal-classification path; omitting the kwarg defaults to `()`.
- [x] 1.4 GREEN: `domain/regression.py` — `classify(...)` gains `series_points: Sequence[SeriesPoint] = ()`, threaded into both `Verdict(...)` constructions.
- [x] 1.5 RED (highest blast radius — design risk #1): `tests/integration/test_analyzer_sql.py` (extend) — for both the `measure` and `system_sample` families, across a multi-commit seeded history: `len(verdict.series) == len(verdict.series_points)`, `verdict.series_points[i].value == verdict.series[i]` for every `i`, points are chronological, and the LAST point's `.commit == latest.git_commit`.
- [x] 1.6 GREEN: `adapters/analyzer_sql.py` — add `_series_points(points, latest_value, latest_commit) -> tuple[SeriesPoint, ...]` that consumes the SAME sorted input `_sparkline_series` does (factor the shared ordering once so drift is structurally impossible, not just tested-against); wire `series_points=self._series_points(...)` into both `_compare_measure_family` and `_compare_system_sample_family`.
- [x] 1.7 CHECKPOINT (design risk #2): run the FULL suite (`./.venv/bin/pytest -q`) — confirm all 328 pre-existing tests pass UNMODIFIED (no assertion edits) plus the new Tasks 1.1–1.6 tests, proving the additive `Verdict.series_points` field and `classify` kwarg default are truly backward-compatible.
- [x] 1.8 RED: `tests/unit/test_budget.py` (new) — the fail-open/fail-closed matrix: regression present → `fail`, offending_metrics lists it, that metric `gated=True`; multiple regressions → `fail`, ALL aggregated into `offending_metrics` (not first-only); mixed regression+stable → `fail` (all-or-nothing), only the regression gated; all stable/improvement → `pass`, `offending_metrics == ()`; all insufficient-data, non-strict → `skipped`, no offenders; all insufficient-data, `strict=True` → `fail`, every metric gated; mixed stable+insufficient, non-strict → `pass`; mixed stable+insufficient, `strict=True` → `fail`; `improvement` never gates; `calibration` passed through unchanged and never alters `gate_status`.
- [x] 1.8a RED: `tests/unit/test_domain_boundary.py` (extend) — `domain/budget.py` imports no `adapters/` module (same static-import guard already applied to `regression.py`/`statistics.py`/`calibration.py`).
- [x] 1.9 GREEN: `domain/model.py` — add `GatedVerdict(verdict, gated)` and `BudgetVerdict(gate_status, gated_verdicts, offending_metrics, strict, calibration)` (both frozen), plus `GATE_PASS = "pass"`, `GATE_FAIL = "fail"`, `GATE_SKIPPED = "skipped"` constants.
- [x] 1.10 GREEN: `domain/budget.py` (new) — `evaluate(result: CompareResult, *, strict: bool = False) -> BudgetVerdict`, pure, no I/O, no adapter imports (satisfies 1.8 and 1.8a).
- [x] 1.11 RED: `tests/unit/test_commit_log_git.py` (new) — `GitCommitLog.subject(sha)` invokes a fake `SubprocessRunner.run(["git", "log", "-1", "--format=%s", sha], cwd=repo_path)` — argv-list, never `shell=True`; returns the stripped stdout on `returncode == 0`; returns `None` (never raises) on non-zero `returncode`, on the runner raising, and on empty/whitespace-only stdout.
- [x] 1.12 GREEN: `domain/ports.py` — add `CommitLog(Protocol)` with `subject(self, sha: str) -> str | None`. `adapters/commit_log_git.py` (new) — `GitCommitLog` implementing it via `SubprocessRunner` (mirrors `driver_maestro.py`'s argv-list-only discipline).
- [x] 1.13 RED: `tests/integration/test_registry.py` (extend) — `build_commit_log(repo_path=None, runner=None) -> CommitLog` returns a `GitCommitLog`.
- [x] 1.14 GREEN: `adapters/registry.py` — add `build_commit_log(*, repo_path=None, runner=None) -> CommitLog` (plain factory, one implementation — mirrors `build_context_provider`/`build_store`).
- [x] 1.15 INFRA: `tests/fakes.py` — add `FakeCommitLog(subject: str | None = "fixed subject")` implementing `CommitLog`, and `FakeAnalyzer(result: CompareResult | None = None, raises: Exception | None = None)` implementing `Analyzer.compare_latest(...)` (needed by PR-B's use-case tests and PR-C's renderer/CLI tests — compare-era tests never needed one since they always exercise the real `SqlAnalyzer`).
- [x] 1.16 CHECKPOINT: `./.venv/bin/pytest -q` full suite green; `ruff check`; `ruff format --check`; `mypy src/perf` clean on the PR-A diff.

## Phase 2: Application use-case + `budget_check_v1` contract (PR-B)

- [x] 2.1 RED: `tests/unit/test_budget_check_flow.py` (new) — `BudgetCheckUseCase.execute(...)`, using `FakeAnalyzer`: `compare_latest` raising → `BudgetCheckFailedError`; `compare_latest` returning `None` → `UsageError`; `compare_latest` returning a `CompareResult` → delegates to `budget.evaluate(result, strict=request.strict)` and returns its `BudgetVerdict` unchanged (assert `evaluate` is genuinely called, not re-implemented inline).
- [x] 2.2 GREEN: `application/budget_check_flow.py` (new) — `BudgetCheckRequest` (frozen), `UsageError`, `BudgetCheckFailedError`, `BudgetCheckUseCase` (depends only on the `Analyzer` port + `domain.budget`, no adapter imports).
- [x] 2.3 RED: `tests/contract/test_budget_check_v1.py` (new) — exact top-level keys `{schema_version, gate_status, strict, offending_metrics, verdicts}`; `schema_version == 1`; each `verdicts[]` entry carries `gated: bool` PLUS compare's per-metric fields (`metric`, `unit`, `direction`, `latest_value`, `baseline_value`, `delta_pct`, `threshold_pct`, `floor`, `status`, `sample_n`, `baseline_commit_n`); `series_points` and `calibration` are ABSENT from the payload (pinned exclusions per D1/§8); the payload is NOT nested under a `compare` key; a shape change without a `SCHEMA_VERSION` bump fails this test.
- [x] 2.4 GREEN: `contracts/budget_check_v1.py` (new) — `SCHEMA_VERSION = 1`, `build_payload(bv: BudgetVerdict) -> dict[str, Any]`, flattened per §8, independent of `contracts/compare_v1.py`.
- [x] 2.5 CHECKPOINT: `./.venv/bin/pytest -q` full suite green; `ruff check`; `ruff format --check`; `mypy src/perf` clean on the PR-B diff.

## Phase 3: Renderers + CLI command + corner cases + `--metric` split (PR-C)

- [x] 3.1 RED: `tests/golden/test_budget_check_pretty_golden.py` (new) — color forced off, fixed width, byte-identical on repeat render, for summary PASS (all metrics shown, `GATE: PASS` banner), summary FAIL (offending banner + `✗` rows), summary SKIPPED; open-right frame (no right border, top+bottom rule + left rail only) and a blank line between metric rows asserted structurally across all three; every regression/fail marker legible via glyph (`✗`/`✓`/`·`) + STATUS word alone.
- [x] 3.2 GREEN: `cli/output/budget_check_pretty.py` (new) — `render_summary(bv, rc, commit_log, *, flow_name, verbose, color, width) -> str`. Hand-rolled string assembly (per design §9 — NOT `rich`; mirrors `compare_pretty.py`'s color-flag + fixed-width discipline). Header `HEAD <short-sha> (<branch>)`; per-metric row (name, latest vs baseline, arrow + signed %, status word, sparkline, glyph); calibration footer; gate banner.
- [x] 3.3 RED: `tests/golden/test_budget_check_pretty_golden.py` (extend) — `--verbose` auto-expands EACH regressed metric inline (compact detail block: baseline vs latest, delta, HEAD commit subject via `FakeCommitLog`); non-regressed metrics stay compact; with MULTIPLE regressed metrics in one run, `FakeCommitLog.subject` is called EXACTLY ONCE total (all expanded rows reference the same `rc.git_commit` — reuse, not one call per row) — pins the design's "exactly one `git log` call per invocation" invariant.
- [x] 3.4 GREEN: `cli/output/budget_check_pretty.py` — `--verbose` branch; fetch `commit_log.subject(rc.git_commit)` ONCE and reuse across every auto-expanded row.
- [x] 3.5 RED: `tests/golden/test_budget_check_pretty_golden.py` (extend) — `render_metric_detail(...)` for a present metric: y-axis value ticks (min/mid/max), x-axis per-commit short-sha labels from `series_points`, HEAD marked; on a `regression`, git-context line (sha + branch + subject via `FakeCommitLog`, exactly one call) with fail-graceful SHA-only fallback when `commit_log.subject(...)` returns `None`; empty/single-point/zero-variance series render without a divide-by-zero (same normalization guard as `compare_pretty`'s sparkline).
- [x] 3.6 GREEN: `cli/output/budget_check_pretty.py` — `render_metric_detail(bv, metric_name, rc, commit_log, *, flow_name, mode, color, width) -> str` (design risk #3: plain deterministic string assembler, no charting abstraction).
- [x] 3.7 RED: `tests/integration/test_cli_budget_check.py` (new) — via real `build_analyzer`/`SqliteStore` seeded history where end-to-end wiring matters (only `RunContextProvider` faked): confirmed regression → exit `1`, `--json` `gate_status == "fail"`, offenders listed, stdout non-empty (verdict always printed before exit); all-stable → exit `0`, `gate_status == "pass"`; insufficient-data with no flag → exit `0` `skipped`, the SAME input with `--strict` → exit `1` `fail`; unknown flow → exit `2` BEFORE any store/analyzer construction (mirrors compare's C2 usage-before-work guard); analyzer returns `None` (no history) → exit `2`; analyzer raises mid-execute → exit `3`; a render failure → exit `3`; `store.close()` failure never overrides the computed exit code.
- [x] 3.8 GREEN: `cli/commands/budget_check.py` (new) — typer command: `flow` argument, `--strict`, `--metric`, `--verbose`, `--restart`, `--device`; usage guard before work; composition (`build_context_provider`/`build_store`/`build_analyzer`/`build_commit_log`); `BudgetCheckUseCase` construction/execution; exception→exit mapping (`UsageError`→2, `BudgetCheckFailedError`→3, any other exception→3, never Python's default 1); ALWAYS renders (pretty or `--json`) before `raise typer.Exit(1 if gate_status == GATE_FAIL else 0)`.
- [x] 3.9 GREEN: `cli/main.py` — register the `budget-check` subcommand (mirrors `compare`'s registration, only wiring change).
- [x] 3.10 RED: `tests/integration/test_cli_budget_check.py` (extend) — corner-case matrix B1/B2/B3/B5/B9/B10 end-to-end via real `SqlAnalyzer`/`SqliteStore` (never monkeypatched for the analyzer's business logic, mirrors compare's C1–C9 precedent): B1 no-history → default `skipped`/exit 0, `--strict` `fail`/exit 1; B2 unknown flow → exit 2 both modes; B3 insufficient baseline commits → default `skipped`/exit 0, `--strict` `fail`/exit 1; B5 one regression, rest stable → `fail`/exit 1, ALL offenders aggregated in `--json`; B9 dev-bundle-only history → default `skipped`/exit 0 (fail-open); B10 a store raise mid-`compare_latest`, a composition-time raise, and a render-time raise → exit 3 in every case, NEVER silently 0 or 1, and a `store.close()` failure never overrides the already-computed exit code. B4/B6/B7/B8 are exercised at the domain layer by `tests/unit/test_budget.py` (PR-A) — `domain/budget.evaluate` is the ONE place the gate rule lives, so those cases do not need a second, redundant end-to-end proof; B10's "no truly-dropped-metric detection" limit (analyzer_sql "silently skipped (C6)") is documented in the apply-progress artifact.
- [x] 3.11 GREEN: no gap surfaced by 3.10 — every case was satisfied by the reused `Analyzer` + `domain/budget.evaluate` + the CLI's existing exception mapping; no production-code change was needed for this task.
- [x] 3.12 RED: `tests/integration/test_cli_budget_check.py` (extend) — `--metric <typo>` (a name that is NOT a known metric for this flow's verdicts) → exit `2`, stderr lists the valid metric names for the flow.
- [x] 3.13 GREEN: `cli/commands/budget_check.py` — validate `--metric` against the set of metric names present in the evaluated `BudgetVerdict.gated_verdicts` BEFORE rendering; on a mismatch, echo an error listing the valid names, `raise typer.Exit(2)`.
- [x] 3.14 RED: `tests/integration/test_cli_budget_check.py` (extend) — `--metric <valid-name>` never exits `2` regardless of this run's data shape (a false CI red is unacceptable) — implemented against `verdict.latest_value is None` as the concrete "no data for this metric in this run" signal (see clarification below).
- [x] 3.15 GREEN: `cli/output/budget_check_pretty.py::render_metric_detail` — a selected `GatedVerdict` whose `verdict.latest_value is None` renders a "no data for metric ... in this run" message and returns normally; routes through the CLI's normal `gate_status`-based exit mapping, never a usage error. **Clarification vs. the original B7 framing**: `adapters/analyzer_sql.py` documents (unchanged, PR-A) that a metric present ONLY in the baseline and fully absent from the latest run is "silently skipped (C6)" — it never produces a `Verdict`/`GatedVerdict` at all, so the CLI has no way to distinguish that case from a genuine typo without a NEW store read-model (explicitly out of scope, design §4 "NO new port for the gate"). "Valid" is therefore defined as "present in `bv.gated_verdicts`" (i.e. this run computed SOME verdict for it, even `insufficient-data`); "no data for this metric" is `latest_value is None` within that set (the warm-up-fully-dropped shape `adapters/analyzer_sql._collapse_latest_system_sample` already documents, which still emits `(None, 0)` rather than omitting the metric). This is implementable with zero new ports, consistent with the shipped analyzer, and never produces a false CI red — noted here rather than silently deviating.
- [x] 3.16 RED: `tests/integration/test_cli_budget_check.py` (extend) — `--json` output is NEVER affected by `--metric`/`--verbose`/`--no-color`; the full flat payload is emitted regardless of any pretty-only flag.
- [x] 3.17 GREEN: `cli/commands/budget_check.py` — dispatch order already short-circuits `--json` before any `--metric`/`--verbose` branching (satisfied by 3.8's structure — no change needed for this task).
- [x] 3.18 CHECKPOINT: `./.venv/bin/pytest -q` full suite green (369 PR-B baseline + 46 new: 25 golden + 21 CLI integration = 415 total); `ruff check` clean; `ruff format --check` clean; `mypy src/perf` clean; coverage 95.39% (floor 93%).
- [x] 3.19 DOCS: `README.md` — added `perfvibe budget-check <flow> [--strict] [--metric <name>] [--verbose] [--restart] [--device <serial>]` to the Usage section; corrected the exit-code line — `1` is now SPENT by `budget-check` (confirmed regression, or `--strict` insufficient-data); `run`/`compare` still never exit `1`.
- [x] 3.20 DOCS: `CLAUDE.md` — replaced the stale "Exit 1 is reserved for a future budget-check CI gate" sentence with the shipped behavior: `budget-check` exits `1` on gate fail; `run`/`compare` remain exit-1-free.
- [x] 3.21 DOCS: `AGENTS.md` — applied the same correction (the "reserved" wording was stale project-wide, not README-only).
- [x] 3.22 DEMO (mirrors `examples/demo-compare/`): `examples/demo-budget-check/` — reuses `examples/demo-compare/`'s seeded fixtures/pattern (imports its `seed_into`), seeds the same regressing `checkout` metric so `perfvibe budget-check demo` visibly exits `1`, README showing default/`--strict`/`--json` invocations.
- [x] 3.23 SPEC CLOSE: `openspec/specs/` — marked every budget-check requirement's Status PLANNED → IMPLEMENTED; confirmed via `git diff main --stat` that every FROZEN file from design §10 (`contracts/compare_v1.py`, `cli/output/compare_pretty.py`, `cli/commands/compare.py`, `application/run_flow.py`, `run`'s schema/write path) is byte-unchanged across the whole change.

## Phase 4: Final verification

- [x] 4.1 Ran the FULL `./.venv/bin/pytest -q` suite (369 PR-B baseline + all budget-check tests = 415); confirmed the `domain/` boundary test still passes for `regression.py`/`statistics.py`/`calibration.py`/`budget.py`.
- [x] 4.2 `ruff check` and `ruff format --check` clean across the whole diff.
- [x] 4.3 `mypy src/perf` clean (full type annotations, `disallow_untyped_defs`).
- [x] 4.4 Coverage floor (93%) confirmed on the full suite: 95.39%, not just the new files.
- [x] 4.5 Confirmed `compare`'s and `run`'s pre-existing tests pass UNMODIFIED (no assertion edits anywhere outside the files PR-A/PR-B already additively touched; PR-C touched zero pre-existing test files, only added new ones).
- [x] 4.6 Confirmed `perf compare <flow>` still exits `0` on a metric that budget-check's gate would fail (Non-Mutation Invariant scenario) — `tests/integration/test_cli_compare.py::test_compare_never_exits_1` (unmodified, still passing) plus a manual spot-check against the seeded demo history (see apply-progress).
