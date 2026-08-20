# Proposal: Driver-Aware Live Progress for `perfvibe run`

## Intent

`perfvibe run` is silent for the whole N-iteration duration, so users believe it hung. Output is swallowed (`capture_output=True`), the iteration loop lives inside each driver (not the use-case), the CLI prints only at the end, and `ManualDriver` prints via raw `print`/`input` to STDOUT — corrupting the `--json` contract. Give users driver-appropriate live feedback while keeping `--json` stdout byte-pure and exit codes unchanged.

## Scope

### In Scope
- New 7th outbound port `ProgressReporter` (Protocol) injected **into the drivers**, wired via the registry `build_*` + CLI pattern; no-op fake in `tests/fakes.py`.
- Concrete `StderrProgressReporter` (STDERR-only) using `stderr_is_tty`/`error_color_enabled`; TTY in-place redraw, non-TTY sequential emoji lines. Hand-rolled ANSI (no `rich`).
- `SubprocessRunner.run_streamed()` (new method; `.run()` untouched) relaying child output live with per-line `scrub_secrets` + bounded diagnostics preserved.
- Maestro DRIVER_MANAGED: full live per-iteration table (⏳/✅/❌) + relayed step output (`--no-ansi`).
- Maestro+Flashlight TOOL_MANAGED: framing header, live relay of Flashlight stdout/stderr, end-of-run RECAP table from already-parsed results JSON.
- Manual driver: "performance vibing" framing + per-iteration Enter prompt routed through the reporter to STDERR (fixes the `--json` corruption bug).
- `--quiet` per-command flag on `run` to suppress progress.

### Out of Scope
- `perfvibe flows` command; `perfvibe run --all`; interactive `flashlight measure`/manual+Flashlight key-cycle seam; PTY passthrough for Maestro's animated table; `PASSWORD` env-var doc gap — all separate future changes.

## Capabilities

### New Capabilities
- `run-progress`: driver-aware live progress reporting — the `ProgressReporter` port, semantic driver events, STDERR-only dual (TTY/non-TTY) rendering, streamed subprocess relay, and per-driver behavior (live table / raw relay + recap / manual prompt).

### Modified Capabilities
- `perf-run`: add `--quiet`; ManualDriver prompt/input routed to STDERR (byte-pure `--json`); reaffirm exit-code discipline (`run` never emits `1`).

## Approach

Exploration Approach 2: separate `run_streamed()` leaves the 10 buffered `.run()` callers untouched. Drivers emit semantic events; the reporter owns all ANSI/TTY rendering. Iteration granularity exists only inside drivers, so the port is driver-injected. Deliver as 4 chained PR slices: (a) port + registry + CLI wiring + ManualDriver bug fix; (b) `run_streamed` + Maestro DRIVER_MANAGED live table; (c) TOOL_MANAGED relay + recap + `--no-ansi`; (d) `--quiet`. Sized against the 800-line review budget.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/perf/domain/ports.py` | Modified | Add `ProgressReporter` Protocol |
| `src/perf/adapters/process.py` | Modified | New `run_streamed()`, per-line scrub |
| `src/perf/adapters/driver_maestro.py` | Modified | Emit events both loop modes |
| `src/perf/adapters/driver_manual.py` | Modified | Route prompt/input via reporter |
| `src/perf/adapters/sampler_flashlight.py` | Modified | Recap data from results JSON |
| `src/perf/adapters/registry.py` | Modified | `build_progress_reporter` + driver kwarg |
| `src/perf/cli/output/` | New | `StderrProgressReporter` |
| `src/perf/cli/run.py` | Modified | Wire reporter, `--quiet` flag |
| `tests/fakes.py`, integration tests | Modified | No-op reporter, streaming hook |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Maestro real non-TTY output format unverified | High | Design-phase live check; pass `--no-ansi` explicitly |
| Missed per-line `scrub_secrets` leaks a secret | Med | Dedicated streaming secret-leak test |
| `--json` stdout contaminated | Med | All progress on STDERR; contract test on stdout purity |
| First in-place-redraw code (100% new) | Med | Non-TTY fallback; golden with color forced off |

## Rollback Plan

Revert per slice — each PR is self-contained. The `ProgressReporter` no-op default restores today's silent behavior; `.run()` is unchanged so buffered callers are unaffected.

## Dependencies

- No new packages (`rich` explicitly declined). Design phase must verify Maestro `--no-ansi` output against a real device.

## Success Criteria

- [ ] Live per-iteration feedback visible for all drivers per their loop model.
- [ ] `--json` stdout stays byte-identical to today (contract test passes).
- [ ] `--quiet` fully suppresses progress; exit codes unchanged (`run` never `1`).
- [ ] ManualDriver prompt no longer corrupts `--json`; no secret leaks in streamed output.
