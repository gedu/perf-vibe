# Delta for perf-run

## ADDED Requirements

### Requirement: Backward-Compatible Streaming Introduction

The tool SHALL introduce iteration-progress streaming via a separate execution path alongside the
existing buffered subprocess execution used today, and SHALL NOT modify the behavior, signature,
or return value of that existing buffered path.

#### Scenario: Existing buffered callers unaffected
- GIVEN other perfvibe subsystems already call the buffered subprocess execution path
- WHEN the live-progress streaming path is introduced
- THEN those existing callers' behavior, inputs, and outputs remain unchanged

## MODIFIED Requirements

### Requirement: Composable Optional Sources with Minimum-Measurement Guarantee

**Status**: SHIPPED ✓

`FlowDriver`, `SystemSampler`, and `MarkerSource` SHALL each be independently optional, selected by
config/flags via the adapter registry. At least one MEASUREMENT source (`SystemSampler` OR
`MarkerSource`) MUST be active; otherwise the tool SHALL exit `2` before any device interaction.
Phase 1 ships (a) `MaestroDriver` + `FlashlightSampler` + `AdbLogcatMarkerSource`, and (b) a
`ManualDriver` — no automation, instructs the user to perform the flow, waits for confirmation —
so the no-Maestro path is built and tested. Flashlight-only-manual and iOS are documented
structural seams in the registry, NOT built in Phase 1. `ManualDriver`'s instructions and
per-iteration confirmation prompt MUST be written to STDERR (never STDOUT), framed with the
locked ⏳/✅/❌ emoji status vocabulary, keeping STDOUT free for `--json`.
(Previously: ManualDriver printed instructions/confirmation via raw `print`/`input` to STDOUT,
corrupting the `--json` contract.)

#### Scenario: No measurement source configured
- GIVEN: config selects a FlowDriver but neither SystemSampler nor MarkerSource
- WHEN: `perf run <flow>` executes
- THEN: the tool SHALL exit `2`, and no device interaction SHALL occur

#### Scenario: ManualDriver flow
- GIVEN: config selects ManualDriver + FlashlightSampler
- WHEN: `perf run <flow>` executes
- THEN: the tool prints "performance vibing" framing and the per-iteration Enter-to-finish prompt
  to STDERR, waits for user confirmation, then sample capture proceeds identically to the Maestro
  path
- AND: no prompt or confirmation text SHALL appear on STDOUT

### Requirement: CLI Options and Configuration Surface

**Status**: SHIPPED ✓

The tool SHALL accept: flow name, iterations `n` (default 10), `--restart` (forces cold; warm is
default), device pinning (`--device` or `MAESTRO_DEVICE`), secret forwarding to the driver's env
mechanism (e.g. `PASSWORD`), `--db <path>`, `--config <path>`, `--json`, `--no-color`. The bundle
identifier SHALL come from config, never hardcoded. The tool SHALL additionally accept a single
`--quiet`/`-q` flag that fully suppresses ALL STDERR progress output — both perfvibe's own chrome
and relayed tool output; only the final result (or `--json` payload) remains. There SHALL be no
second `--full-quiet` (or equivalent) flag.
(Previously: no `--quiet` flag existed.)

#### Scenario: Bundle id from config, secret not logged
- GIVEN: `--config` supplies a bundle identifier and `PASSWORD` is set in the environment
- WHEN: `perf run` executes
- THEN: the configured bundle id is used with none hardcoded, and `PASSWORD` is forwarded to the
  driver without appearing in stdout/stderr

#### Scenario: --quiet suppresses all stderr progress
- GIVEN: `perf run <flow> --quiet` is invoked (with or without `--json`)
- WHEN: the run executes
- THEN: zero progress bytes are written to STDERR — no perfvibe chrome, no relayed tool output
- AND: the run's exit code and final result output are unaffected

### Requirement: Exit-Code Discipline

**Status**: SHIPPED ✓

The tool SHALL exit `0` on success, `2` on usage error (bad arguments, no measurement source
configured), `3` on runtime/tooling failure (device offline, driver failure, zero markers AND zero
samples captured, transaction failure). `run` SHALL NEVER exit `1`. A failure in the
progress-rendering/relay path (e.g. a streaming or terminal-control error) SHALL be treated as a
runtime/tooling failure (`exit 3`) and SHALL NEVER produce `exit 1`; rendering failures SHALL
NEVER mask or override the underlying flow's own exit-code determination.
(Previously: no clause existed for progress-renderer failures, since no renderer existed.)

#### Scenario: No measurement source is a usage error
- GIVEN: config selects no SystemSampler and no MarkerSource
- WHEN: `perf run` is invoked
- THEN: the tool exits `2`, not `3`

#### Scenario: No data captured
- GIVEN: the flow completes but both active sources yield zero data
- WHEN: the run finishes
- THEN: the tool exits `3` and no run row is written

#### Scenario: Progress rendering failure maps to exit 3
- GIVEN: the flow completes and data is captured successfully
- WHEN: the progress reporter raises an error while rendering the live table or relay
- THEN: the tool exits `3`, never `1`, reported like any other runtime/tooling failure
