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

- [ ] C.1 RED `tests/integration/test_driver_maestro.py` [threat-matrix]: TOOL_MANAGED relays the single subprocess stream via `run_streamed` unparsed (no fake iteration events); composed argv includes `--no-ansi`.
- [ ] C.2 GREEN `driver_maestro.py` `_drive_tool_managed`/`command()`: use `run_streamed`, relay via `reporter.relayed_line`; add `--no-ansi` to the inner maestro argv.
- [ ] C.3 RED `tests/unit/test_progress.py`: `recap(result)` renders a ⏳/✅/❌ table from `iterations[].status`/`partial_coverage`, including a partial-coverage row.
- [ ] C.4 GREEN `cli/output/progress.py`: add `recap(result: RunFlowResult)` on `StderrProgressReporter` only — NOT on the Protocol.
- [ ] C.5 GREEN `cli/commands/run.py`: call `reporter.recap(result)` after `execute()` on success only; failure (`emit_error`) path unchanged, recap skipped.
- [ ] C.6 RED `tests/integration/test_cli_run.py`: TOOL_MANAGED relay never pollutes stdout; `--json` purity holds with recap active.
- [ ] C.7 MANDATORY verification (blocks merge): confirm real Maestro `--no-ansi` non-TTY step-line format against a live binary/device — sub-agents could not run one. Until confirmed, treat relayed lines as opaque; NEVER parse them to synthesize iteration events (design.md open item).
- [ ] C.8 Verify: slice tests green; mypy+ruff clean; coverage threshold met; rendering failure maps to exit 3, never 1.

## Slice D — `--quiet`/`-q`

_Spec: perf-run/CLI Options and Configuration Surface, --quiet scenario._

- [ ] D.1 RED `tests/integration/test_cli_run.py`: `--quiet`/`-q` yields zero stderr progress bytes (chrome + relay); exit code/final output unaffected; non-TTY auto-degrade (sequential lines) still works WITHOUT the flag.
- [ ] D.2 RED `tests/integration/test_registry.py`: `build_progress_reporter(quiet=True, ...)` returns `NullProgressReporter`.
- [ ] D.3 GREEN `adapters/registry.py`: `quiet=True` path returns `NullProgressReporter` (mirrors `build_sampler` returning `None`).
- [ ] D.4 GREEN `cli/commands/run.py`: add `--quiet`/`-q` typer option; no second `--full-quiet` flag.
- [ ] D.5 Verify: full suite green (`./.venv/bin/pytest -q --cov=perf`); mypy+ruff clean; coverage threshold met.

## Cross-Cutting Acceptance Checklist (verify at each slice's `Verify` task)

- [ ] `--json` stdout stays byte-pure; every progress byte goes to STDERR.
- [ ] Exit codes unchanged: `run` never exits `1`; rendering/relay failure -> exit `3`.
- [ ] Emoji vocabulary locked to exactly ⏳/✅/❌, uniform across drivers.
- [ ] No `rich` dependency introduced; hand-rolled ANSI only.
- [ ] mypy + ruff clean; coverage meets project threshold, each slice.
