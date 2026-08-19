# Tasks: Driver-Aware Live Progress for `perfvibe run`

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | ~700-900 total; ~150-230 per slice |
| Session review budget | 800 lines/slice (explicit session override; skill default is 400) |
| 400-line budget risk | High against the 400-line default; Low-Medium against the 800 override |
| Chained PRs recommended | Yes |
| Suggested split | A -> B -> C -> D, each independently mergeable |
| Delivery strategy | auto-forecast |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

Rationale: design.md already commits to "4 chained PR slices, each independently
shippable... under the 800-line budget" — that phrasing (independently shippable,
own verification) matches stacked-to-main, not a coordinated feature-branch chain.
`auto-forecast` resolves the decision now rather than gating on the user.

### Suggested Work Units

| Unit | Goal | PR | Focused test command | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| A | Port + registry wiring + CLI injection + manual STDOUT fix | PR1 | `pytest -q tests/unit/test_ports.py tests/integration/test_driver_manual.py tests/integration/test_registry.py` | N/A — pure wiring, no subprocess behavior change | Revert `ports.py`/`registry.py`/`driver_manual.py`/`run.py` reporter kwarg |
| B | `run_streamed` + Maestro DRIVER_MANAGED live table | PR2 | `pytest -q tests/unit/test_process.py tests/unit/test_progress.py tests/integration/test_driver_maestro.py` | `perfvibe run <flow> --device <serial>` (markers-only config) | Revert `run_streamed`, DRIVER_MANAGED loop change, `cli/output/progress.py` |
| C | TOOL_MANAGED relay + recap + `--no-ansi` | PR3 | `pytest -q tests/integration/test_driver_maestro.py tests/unit/test_progress.py` | Live device run (Maestro+Flashlight config) — MUST confirm `--no-ansi` format | Revert TOOL_MANAGED relay path, `recap()`, `--no-ansi` arg |
| D | `--quiet`/`-q` flag | PR4 | `pytest -q tests/integration/test_cli_run.py` | `perfvibe run <flow> --quiet` | Revert `--quiet` option + `NullProgressReporter` wiring |

## Slice A — Port, Registry Wiring, CLI Injection, Manual STDOUT Fix

_Spec: perf-run/Composable Optional Sources (ManualDriver STDERR fix); run-progress/Manual scenario._

- [x] A.1 RED `tests/unit/test_ports.py`: a minimal stub structurally satisfies the new `ProgressReporter` Protocol.
- [x] A.2 GREEN `domain/ports.py`: add `ProgressReporter` Protocol — `iteration_started`, `iteration_finished(ok=)`, `awaiting_user_input`, `relayed_line` (primitives only, no adapter import).
- [x] A.3 `tests/fakes.py`: add `FakeProgressReporter` recording every emitted event.
- [x] A.4 RED `tests/integration/test_driver_manual.py`: prompt/confirm reach `FakeProgressReporter`, never `print_fn`/stdout (fixes the `--json` STDOUT-corruption bug).
- [x] A.5 GREEN `driver_manual.py`: add `reporter` kwarg; route prompt via `awaiting_user_input`/`iteration_started`/`iteration_finished`.
- [x] A.6 GREEN `driver_maestro.py`, `driver_replay.py`: accept `reporter` kwarg, no-op for now.
- [x] A.7 RED `tests/integration/test_registry.py`: `build_progress_reporter` returns a concrete reporter; `build_driver` passes `reporter` uniformly into every driver builder.
- [x] A.8 GREEN `adapters/registry.py`: add `build_progress_reporter`; add `reporter` to the uniform driver-builder kwargs (mirrors `runner`).
- [x] A.9 RED `tests/integration/test_cli_run.py`: `--json` stdout byte-identical with/without progress active; manual-driver `--json` run has zero stdout progress bytes.
- [x] A.10 GREEN `cli/commands/run.py`: build the concrete reporter via the registry, inject into `build_driver`.
- [x] A.11 Verify: slice tests green; mypy+ruff clean; coverage threshold met.

## Slice B — `run_streamed` + Maestro DRIVER_MANAGED Live Table

_Spec: run-progress/Maestro markers-only, TTY-Aware Rendering, Secret Scrubbing._

- [x] B.1 RED `tests/unit/test_process.py` [threat-matrix]: `run_streamed` relays per-line; secrets scrubbed before relay AND in the bounded accumulator (leak test); argv always a list, `shell` never truthy.
- [x] B.2 GREEN `adapters/process.py`: add `SubprocessRunner.run_streamed(argv, *, env, cwd)` — Popen, `stderr=subprocess.STDOUT`, `encoding="utf-8", errors="replace"`, per-line `scrub_secrets`; same `CommandResult` shape; `.run()` untouched.
- [x] B.3 RED `tests/unit/test_progress.py`: append-only sequential ⏳/✅/❌ lines, identical in TTY and non-TTY (see B.9 — originally written against a TTY in-place ANSI redraw table, later replaced); color-forced-off/on goldens; `NullProgressReporter` emits nothing.
- [x] B.4 GREEN create `cli/output/progress.py`: `StderrProgressReporter` (local ANSI palette; `stderr_is_tty`/`error_color_enabled` only) + `NullProgressReporter` (see B.9 — rendering model corrected post-review).
- [x] B.5 RED `tests/integration/test_driver_maestro.py`: DRIVER_MANAGED loop emits `iteration_started`/`iteration_finished(ok=)` + relayed lines, in order, per iteration.
- [x] B.6 GREEN `driver_maestro.py` `_drive_driver_managed`: call reporter events + `run_streamed` per iteration; relay each line.
- [x] B.7 `tests/integration/test_driver_maestro.py`'s local `_FakeRunner` (the actual "fake runner" tests/fakes.py referred to — see Deviations): grew with a `run_streamed` hook (records calls, feeds fixed lines); `.run()` fake untouched.
- [x] B.8 Verify: slice tests green; mypy+ruff clean; coverage threshold met; `--json` purity re-confirmed with the DRIVER_MANAGED path (new `test_driver_managed_maestro_relay_stays_on_stderr_json_purity_holds`, real registry-built `MaestroDriver`, only `SubprocessRunner` faked).
- [x] B.9 CORRECTNESS FIX (post-review, before merge): the original TTY in-place redraw table tracked `_rendered_rows` but `relayed_line()` printed a line WITHOUT updating that count; in production (`driver_maestro.py` `_drive_driver_managed`) relayed step lines are emitted BETWEEN `iteration_started`/`iteration_finished`, so the next redraw's cursor-up count went stale and clobbered the wrong terminal rows on every driver-managed TTY run that relayed step output (the norm) — same desync on the manual driver's `awaiting_user_input` line. Locked decision: drop in-place redraw entirely; `StderrProgressReporter` is now append-only sequential (TTY and non-TTY render identically except for optional color, no cursor-control byte ever). Removed `_CURSOR_UP_FMT`/`_CLEAR_LINE`/`_rendered_rows`/`_redraw_table`/`_emit`/`_statuses`/`_total`. `relayed_line()` now writes 3-space-indented lines nested under the current iteration. Tests rewritten: removed the TTY-redraw/cursor-up tests, added a regression test for the exact interleave (`iteration_started` → `relayed_line` × 2 → `iteration_finished`, asserted in order with zero cursor bytes, both TTY and non-TTY) plus color on/off goldens for the finished line.

## Slice C — TOOL_MANAGED Relay, Recap, `--no-ansi`

_Spec: run-progress/Maestro+Flashlight scenario; perf-run/Exit-Code Discipline._

- [x] C.1 RED `tests/integration/test_driver_maestro.py` [threat-matrix]: TOOL_MANAGED relays the single subprocess stream via `run_streamed` unparsed (no fake iteration events); composed argv includes `--no-ansi`. (`test_tool_managed_relays_via_run_streamed_unparsed_no_fake_iteration_events`, `test_command_no_ansi_precedes_env_secrets`.)
- [x] C.2 GREEN `driver_maestro.py` `_drive_tool_managed`/`command()`: use `run_streamed`, relay via `reporter.relayed_line`; add `--no-ansi` to the inner maestro argv.
- [x] C.3 RED `tests/unit/test_progress.py`: `recap(result)` renders a ✅/❌ table from `iterations[].status`/`partial_coverage`, including a partial-coverage row. (No fabricated ⏳: recap runs once, post-completion, when every iteration is already resolved — `test_recap_never_emits_pending_glyph` locks this.)
- [x] C.4 GREEN `cli/output/progress.py`: add `recap(result: RunFlowResult)` on `StderrProgressReporter` only — NOT on the Protocol.
- [x] C.5 GREEN `cli/commands/run.py`: call `reporter.recap(result)` after `execute()` on success only; failure (`emit_error`) path unchanged, recap skipped.
- [x] C.6 RED `tests/integration/test_cli_run.py`: TOOL_MANAGED relay never pollutes stdout; `--json` purity holds with recap active. (`test_tool_managed_flashlight_relay_stays_on_stderr_json_purity_holds_with_recap`.)
- [x] C.7 MANDATORY verification (blocks merge) — SATISFIED. Confirmed against a real Maestro binary/device this session (user ran `maestro test <flow>` piped, non-TTY): piped Maestro is ALREADY clean plain text — header `> Flow <name>`, then one line per step `<description>... COMPLETED`/`... FAILED`, no ANSI/cursor codes. `--no-ansi` IS accepted (no "unknown option"); output stays plain text; on failure it also prints static multi-line blocks (assertion detail, `Possible causes:`, a `~/.maestro/tests/<ts>` path, a Unicode box-drawing panel) — all static text, no cursor control. Decision locked: keep `--no-ansi`, relay every line OPAQUELY (no step-structure parsing) — implemented exactly this way in `_drive_tool_managed`.
- [x] C.8 Verify: slice tests green; mypy+ruff clean; coverage threshold met; rendering failure maps to exit 3, never 1. (`./.venv/bin/pytest -q --cov=perf` → 596 passed, 95.00% coverage vs 93% threshold; `./.venv/bin/mypy src/perf` → no issues in 47 files; `./.venv/bin/ruff check .` → all checks passed; `./.venv/bin/ruff format --check .` → 104 files formatted. Exit-3-never-1 unchanged — `recap()` is called inside the SAME guarded try/except as output rendering in `run.py`, already asserted by `test_run_never_exits_1`/`test_render_failure_exits_3_never_1`.)

## Slice C — Post-Review Correctness/Cleanup Fixes (adversarial review, before merge)

- [x] FIX 1 (correctness): `recap()` treated `iteration_statuses is not None` as the
  per-iteration-table gate, but `FlashlightSampler.parse()` returns `[]` (empty list,
  NOT `None`) for a zero-`iterations[]` report — the per-iteration loop ran zero times
  and NO coverage line was emitted (silence, just the header). Fixed: `if statuses:`
  (truthy) drives the table branch; the else branch now handles BOTH `None` and `[]`,
  always emitting an honest coverage line. Test:
  `test_recap_empty_iteration_statuses_falls_back_to_honest_coverage_line`.
- [x] FIX 2 (correctness): the recap header used `result.iterations` (REQUESTED count)
  while the per-iteration rows used `len(statuses)` (ACTUAL reported count) — a
  self-contradictory recap when they disagree. Fixed: when the true table renders,
  BOTH the header and every row denominator are driven from the SAME `len(statuses)`;
  if the requested count differs, the header says "N requested · M reported" instead
  of silently picking one. Tests:
  `test_recap_reports_requested_vs_reported_when_counts_disagree`,
  `test_recap_header_and_rows_share_the_same_count_when_they_agree`.
- [x] FIX 3 (spec cleanliness): removed the `⚠️` glyph (`_PARTIAL`) from the coarse-
  fallback summary — outside the locked ⏳/✅/❌ vocabulary. Now renders `❌ <n>
  iteration(s) · partial coverage (...)` / `✅ <n> iteration(s) · complete` — locked
  glyphs + plain words only. Updated
  `test_recap_honest_coverage_summary_when_no_iteration_statuses_and_partial`.
- [x] FIX 4 (cleanup): removed the `🎯 <flow> · N iterations via Flashlight` header
  emission (and the `_last_flow_name` mutable-state field it required) from
  `driver_maestro.py`'s `_drive_tool_managed` — it was emitted via `relayed_line`,
  3-space-indented so it read like a nested tool line. Added a concrete-only
  `StderrProgressReporter.run_header(flow_name, iterations)` (`cli/output/progress.py`,
  same placement rule as `recap()` — NOT on the `ProgressReporter` Protocol), called
  from `cli/commands/run.py` BEFORE `use_case.execute(...)`, guarded to only the
  Flashlight-sampler (TOOL_MANAGED) path (`config.sampler == "flashlight"`), inside the
  same guarded try/except so a failure still maps to exit 3, never 1, and never
  touches stdout. Tests: `test_run_header_writes_a_non_indented_top_level_line`,
  `test_run_header_is_not_on_the_progress_reporter_protocol`, updated
  `test_tool_managed_relays_via_run_streamed_unparsed_no_fake_iteration_events`
  (driver no longer emits the header), and
  `test_tool_managed_flashlight_relay_stays_on_stderr_json_purity_holds_with_recap`
  (CLI-level: header is a distinct, non-indented top-level stderr line).
- [x] Gates: `./.venv/bin/pytest -q --cov=perf` → 601 passed, 95.01% coverage (threshold
  93.0%); `./.venv/bin/mypy src/perf` → no issues in 47 files; `./.venv/bin/ruff check .`
  → all checks passed; `./.venv/bin/ruff format --check .` → 104 files formatted.

## Slice D — `--quiet`/`-q`

_Spec: perf-run/CLI Options and Configuration Surface, --quiet scenario._

- [x] D.1 RED `tests/integration/test_cli_run.py`: `--quiet`/`-q` yields zero stderr progress bytes (chrome + relay); exit code/final output unaffected; non-TTY auto-degrade (sequential lines) still works WITHOUT the flag.
- [x] D.2 RED — DEVIATION: implemented in `tests/unit/test_progress.py`, not `tests/integration/test_registry.py` — see "Deviations from Design" below. `build_progress_reporter(quiet=True, ...)` returns `NullProgressReporter`.
- [x] D.3 GREEN — DEVIATION: implemented in `cli/output/progress.py`, not `adapters/registry.py` (see below). `quiet=True` path returns `NullProgressReporter` (mirrors `build_sampler` returning `None`).
- [x] D.4 GREEN `cli/commands/run.py`: add `--quiet`/`-q` typer option; no second `--full-quiet` flag.
- [x] D.5 Verify: full suite green (`./.venv/bin/pytest -q --cov=perf` → 609 passed, 95.03% coverage vs 93.0% threshold); mypy (`./.venv/bin/mypy src/perf` → no issues in 47 files) + ruff (`./.venv/bin/ruff check .` → all checks passed; `./.venv/bin/ruff format --check .` → 104 files formatted) clean; coverage threshold met.

### Slice D — Deviations from tasks.md wording (D.2/D.3)

`build_progress_reporter` does not live in `adapters/registry.py` — Slice
A/B already moved it to `cli/output/progress.py` to avoid an adapters->cli
import inversion + circular import (documented in that module's docstring
and in the Slice A/B/C apply-progress notes). Adding the `quiet=True`
branch to `adapters/registry.py` as D.3 literally says would reintroduce
that exact inversion, so the branch (and its RED test) were added to
`cli/output/progress.py`/`tests/unit/test_progress.py` instead, keeping
the established architecture consistent. `cli/commands/run.py` still owns
the `--quiet`/`-q` CLI option (D.4) and threads `quiet=quiet` into the
(unchanged-location) factory.

Also, `NullProgressReporter` now implements `recap(result)` and
`run_header(flow_name, iterations)` as true no-ops (full drop-in for
`StderrProgressReporter`), and `build_progress_reporter`'s return type is
narrowed to a new `CliProgressReporter` Protocol (`cli/output/progress.py`)
— the domain-pure 4 methods plus `recap`/`run_header` — instead of the
concrete `StderrProgressReporter` type it returned after Slice C. This is
the typing seam that lets `run.py` call `.recap()`/`.run_header()` on
whichever reporter the factory returns without an `isinstance` guard or a
`# type: ignore`.

### Slice D — Post-Review Correctness Fixes (adversarial review, before merge)

- [x] FIX 1 (correctness): `NON_TTY_NUDGE` in `cli/commands/run.py` was gated
  ONLY on `output.should_nudge_stderr`, never on `quiet` — so
  `perfvibe run <flow> --quiet > out.txt` (pretty, non-`--json`, non-TTY
  stdout) still printed the nudge to stderr, contradicting `--quiet`'s own
  help text ("only the final result remains"). Fixed: the nudge is now
  gated on `output.should_nudge_stderr and not quiet`. `emit_error` and the
  store-close warning are UNCHANGED — errors/warnings always surface, even
  under `--quiet`; only progress-class output + this nudge are suppressed.
  Also reworded the `--quiet` help text to be honest about what survives:
  "Suppress all progress, relayed tool output, and the end recap on stderr
  (errors are still reported)." Tests:
  `test_quiet_flag_suppresses_non_tty_nudge_on_pretty_path` (RED confirmed
  by temporarily reverting the gate before reapplying it — pretty,
  non-TTY, `--quiet` → zero stderr bytes) and
  `test_non_quiet_non_tty_pretty_run_still_emits_nudge` (regression guard —
  same scenario WITHOUT `--quiet` still emits `NON_TTY_NUDGE`).
- [x] FIX 2 (correctness): `ManualDriver.drive()` surfaces its per-iteration
  prompt ONLY via `reporter.awaiting_user_input(...)`; under `--quiet`,
  `build_progress_reporter(quiet=True)` returns `NullProgressReporter`,
  whose `awaiting_user_input` is a no-op — so `--quiet` + `driver = "manual"`
  blocked on `input()` with NO visible prompt (a silent, undiagnosable
  hang). Fixed: `cli/commands/run.py` now rejects `config.driver == "manual"
  and quiet` as a clean usage error (`emit_error` + `typer.Exit(code=2)`),
  placed right after the existing unknown-flow guard and BEFORE any driver
  is built or interacted with. Test:
  `test_quiet_with_manual_driver_exits_2_before_any_prompt` (RED confirmed
  the same way — reverting the guard reproduced a real `input()` call
  raising `EOFError` inside the CLI, mapped to exit 3, not the intended
  exit 2 — before reapplying the guard).
- [x] Gates: `./.venv/bin/pytest -q --cov=perf` → 612 passed, 95.07% coverage
  (threshold 93.0%); `./.venv/bin/mypy src/perf` → no issues in 47 files;
  `./.venv/bin/ruff check .` → all checks passed; `./.venv/bin/ruff format
  --check .` → 104 files formatted.

## Cross-Cutting Acceptance Checklist (verify at each slice's `Verify` task)

- [x] `--json` stdout stays byte-pure; every progress byte goes to STDERR.
- [x] Exit codes unchanged: `run` never exits `1`; rendering/relay failure -> exit `3`.
- [x] Emoji vocabulary locked to exactly ⏳/✅/❌, uniform across drivers.
- [x] No `rich` dependency introduced; hand-rolled ANSI only.
- [x] mypy + ruff clean; coverage meets project threshold, each slice.
