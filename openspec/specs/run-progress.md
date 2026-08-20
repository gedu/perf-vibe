# Specification: `run-progress` capability (SHIPPED)

**Status**: SHIPPED AND ARCHIVED

## Purpose

Driver-aware live progress reporting for `perfvibe run`, so a multi-iteration run never appears hung. A `ProgressReporter` port renders per-driver-appropriate feedback on STDERR only, leaving STDOUT byte-pure for `--json`.

## Non-Goals

`perfvibe flows`; `perfvibe run --all`; interactive `flashlight measure` / manual+Flashlight key-cycle control; PTY passthrough for Maestro's animated table; documenting the `PASSWORD` env-var. Separate future changes.

## Requirements

### Requirement: Driver-Aware Progress Rendering

The system SHALL render progress appropriate to the active driver/sampler combination, exclusively on STDERR, without altering the underlying flow execution.

#### Scenario: Maestro + Flashlight (TOOL_MANAGED)
- GIVEN: `perfvibe run` uses `MaestroDriver` + `FlashlightSampler`
- WHEN: the run executes
- THEN: a framing header is emitted, Flashlight's own stdout/stderr is relayed live to STDERR unparsed, and after completion a ⏳/✅/❌ per-iteration RECAP table renders from the parsed results JSON (`iterations[].status`, `partial_coverage`)
- AND: the system MUST NOT parse Flashlight's human stdout to fabricate a live table

#### Scenario: Maestro markers-only (DRIVER_MANAGED)
- GIVEN: `perfvibe run` uses `MaestroDriver` with markers only (no Flashlight)
- WHEN: the run executes
- THEN: a LIVE per-iteration emoji table (⏳ running → ✅/❌) renders on STDERR as each iteration completes, alongside relayed Maestro step output

#### Scenario: Manual driver
- GIVEN: `perfvibe run` uses `ManualDriver`
- WHEN: the run executes
- THEN: "performance vibing" framing and a per-iteration Enter-to-finish prompt render on STDERR (never STDOUT), followed by the iteration's emoji status

#### Scenario: Replay driver
- GIVEN: `perfvibe run` uses a Replay driver
- WHEN: the run executes
- THEN: minimal or no live progress output is emitted

### Requirement: Locked Emoji Status Vocabulary

The system SHALL use exactly three status glyphs, uniform across all drivers: ⏳ (pending/running), ✅ (passed), ❌ (failed). No driver SHALL substitute an alternate symbol.

#### Scenario: Uniform vocabulary across drivers
- GIVEN: any supported driver renders per-iteration status
- WHEN: an iteration is pending, succeeds, or fails
- THEN: exactly ⏳, ✅, or ❌ is used, respectively

### Requirement: STDOUT Byte-Purity in JSON Mode

The system SHALL write zero progress bytes to STDOUT at any point during execution when `--json` is requested.

#### Scenario: --json output contains zero progress bytes
- GIVEN: `perfvibe run --json` executes with progress enabled (not `--quiet`)
- WHEN: the run completes
- THEN: stdout contains only the schema_version-carrying JSON payload, byte-for-byte identical to the payload without progress enabled

### Requirement: TTY-Aware Rendering Mode

The system SHALL detect whether STDERR is an interactive TTY and adjust rendering accordingly, without ever suppressing relay of the underlying tool's output.

#### Scenario: stderr is a TTY — in-place redraw
- GIVEN: STDERR is attached to an interactive TTY
- WHEN: progress renders
- THEN: the per-iteration table redraws in place using cursor control, while tool output continues relaying live

#### Scenario: stderr is not a TTY (CI/pipe) — plain sequential lines
- GIVEN: STDERR is redirected to a file or pipe (e.g. CI)
- WHEN: progress renders
- THEN: plain sequential emoji lines are emitted with no in-place redraw or cursor-control sequences, while the underlying tool's output is still relayed

### Requirement: Secret Scrubbing in Relayed Output

The system SHALL scrub every relayed output line for secrets before writing it to STDERR, consistent with existing `scrub_secrets` behavior.

#### Scenario: forwarded secret never appears in relay
- GIVEN: a `PASSWORD` value is forwarded to the driver via `--env`
- WHEN: the driver's live output is relayed through the progress reporter
- THEN: each relayed line is scrubbed per-line and the `PASSWORD` value never appears in the relayed stream

## Testing

- **Full suite**: 936 collected tests across unit/integration/contract/golden layers.
- **Core paths verified**: live progress rendering per driver (DRIVER_MANAGED/TOOL_MANAGED/manual), emoji vocabulary locked, STDOUT byte-purity, TTY-aware rendering, secret scrubbing in relay, --quiet flag suppression, exit-code discipline.
- **Corner cases covered**: non-TTY output format, in-place redraw on TTY, missing secrets/no relay, manual+quiet rejection, progress renderer failure → exit 3.

## Files & Configuration

- **ProgressReporter Protocol**: `src/perf/domain/ports.py`
- **Progress Rendering (Stderr/Null)**: `src/perf/cli/output/progress.py`
- **Subprocess Relay**: `src/perf/adapters/process.py` (run_streamed method)
- **Driver Integration**: `src/perf/adapters/driver_maestro.py`, `src/perf/adapters/driver_manual.py`, etc.
- **Registry**: `src/perf/adapters/registry.py` (build_progress_reporter)
- **CLI**: `src/perf/cli/commands/run.py` (--quiet flag, reporter wiring)
- **Tests**: `tests/{unit,integration,contract}` across 20+ test files

## Delivery History

- **PR #34**: ProgressReporter port + registry wiring + manual driver STDERR fix
- **PR #35**: run_streamed + Maestro DRIVER_MANAGED live table
- **PR #36**: TOOL_MANAGED relay + recap + --no-ansi
- **PR #37**: --quiet flag
- **All merged to main** (commits b906ad0, 96d30bb, a524349, 4ee6688)
- **Status**: 46 tasks complete, 936 tests passed, 95.09% coverage (floor 93%)

## Known Limitations & Future Work

None at this time. This capability ships feature-complete and with comprehensive test coverage.

---

**Specification**: 2026-08-19 · new capability archived and consolidated into canonical spec.
