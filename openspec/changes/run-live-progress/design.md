# Design: Driver-Aware Live Progress for `perfvibe run`

## Technical Approach

Add a 7th outbound port `ProgressReporter` (pure `Protocol`, `domain/ports.py`)
injected INTO the driver adapters (constructor kwarg, like `runner`), NOT into
`RunFlowUseCase` — iteration granularity exists only inside `driver.drive()`,
which the use-case calls exactly once (explore fact). Drivers emit semantic
events; a concrete `StderrProgressReporter` (`cli/output/`) owns all ANSI/TTY
rendering on STDERR. Subprocess relay uses a NEW `SubprocessRunner.run_streamed()`
(explore Approach 2) so the 10 buffered `.run()` callers are untouched. Delivered
as the proposal's 4 chained PR slices.

## Architecture Decisions

| Decision | Choice | Rejected | Rationale |
|---|---|---|---|
| Where the port is injected | Into each driver adapter | Into `RunFlowUseCase` | Per-iteration loop lives inside drivers; use-case sees only before/after `drive()`. |
| Relay mechanism | New `run_streamed()` | Change `.run()` to Popen | `.run()` has 10 buffered callers who must not stream (git/bash/tests). |
| End recap layering | CLI calls concrete `reporter.recap(result)` after `execute()` returns | Use-case or driver renders recap | Keeps use-case adapter-free (SKILL rule 1); `RunFlowResult` (application type) must not enter the domain Protocol. |
| `recap()` placement | Concrete reporter method, NOT on the `Protocol` | Add `recap` to Protocol | Protocol is domain-pure; `recap` needs `RunFlowResult`. CLI holds the concrete reporter and calls it. |
| `--quiet` | Build a `NullProgressReporter` (no-op) instead | Flag checks inside renderer | Mirrors `build_sampler` returning `None`; one flag = fully silent. CI decoration is TTY auto-detection, not a flag. |
| Stream merge | `stderr=subprocess.STDOUT` (one stream) | Separate stdout/stderr pumps | Preserves real-time interleaving the user would see; matches `start_capture`. |
| No `rich` | Hand-rolled ANSI palette (local consts) | `rich` | First-use unjustified (arch rule 3); prior art is `budget_check_pretty.py`. |

## Data Flow

    run.py: build_progress_reporter(quiet, output)  ──┐ (concrete, retained)
       └─ build_driver(..., reporter=<Protocol>) ──→ driver.drive(plan)
                                                        │ emits events
                                        run_streamed(argv) ─ per line ─→ scrub_secrets ─→ reporter.relayed_line
       use_case.execute()  →  RunFlowResult ───────────┘
    run.py (success): reporter.recap(result)  →  STDERR summary
    run.py (failure): existing emit_error path (recap skipped)

## File Changes

| File | Action | Description |
|---|---|---|
| `domain/ports.py` | Modify | Add `ProgressReporter` Protocol (4 pure live methods; primitives only). |
| `adapters/process.py` | Modify | Add `run_streamed()`; per-line `scrub_secrets` at new call site; leave `.run()`/`start_capture`/`stop_capture` untouched. |
| `adapters/driver_maestro.py` | Modify | Add `reporter` kwarg; DRIVER_MANAGED loop emits `iteration_started/finished`+`relayed_line` via `run_streamed`; TOOL_MANAGED relays stream (no fake iteration events); pass `--no-ansi` to maestro argv. |
| `adapters/driver_manual.py` | Modify | Add `reporter` kwarg; route prompt via `reporter.awaiting_user_input` (STDERR) — fixes `print`/`input` STDOUT `--json` corruption; keep injectable read. |
| `adapters/driver_replay.py` | Modify | Accept `reporter` kwarg (minimal/no-op emit). |
| `adapters/registry.py` | Modify | Add `build_progress_reporter`; add `reporter` to the uniform driver-builder kwargs. |
| `cli/output/progress.py` | Create | `StderrProgressReporter` (TTY redraw / non-TTY sequential) + `NullProgressReporter`; local ANSI palette; `recap(result)`. |
| `cli/commands/run.py` | Modify | Add `--quiet/-q`; build+retain reporter; inject into `build_driver`; call `reporter.recap(result)` on success. |
| `tests/fakes.py` | Modify | Add `FakeProgressReporter` (records events); grow `_FakeRunner` with a `run_streamed` streaming hook. |

## Interfaces / Contracts

```python
# domain/ports.py — PURE, no adapter imports, primitives only
class ProgressReporter(Protocol):
    def iteration_started(self, index: int, total: int) -> None: ...
    def iteration_finished(self, index: int, total: int, *, ok: bool) -> None: ...
    def awaiting_user_input(self, prompt: str) -> None: ...
    def relayed_line(self, text: str) -> None: ...
# recap(result: RunFlowResult) lives ONLY on the concrete cli/output reporter.
```

`run_streamed(argv, *, env, cwd)` returns the same `CommandResult`; merged
output goes into `stderr`+`stdout` (bounded) so the driver's existing
`bounded_diagnostics(result.stderr)` failure path is unchanged. Popen uses
`encoding="utf-8", errors="replace"`. Emoji vocab LOCKED: ⏳/✅/❌. Colors use
`error_color_enabled`/`stderr_is_tty` ONLY — never stdout's pair. Progress is
STDERR-only, so it is orthogonal to `--json` stdout purity; `--json` consumers
who want silence pass `--quiet`.

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit | streaming secret leak | `run_streamed` with `--env PASSWORD=…` in argv → assert `***` in every relayed line + accumulated buffer (new call site). |
| Unit | renderer | TTY redraw vs non-TTY sequential; color-forced-off golden; `NullProgressReporter` emits nothing. |
| Integration | drivers | `FakeProgressReporter` asserts event order per driver; manual prompt goes to STDERR not STDOUT. |
| Contract | `--json` purity | STDOUT byte-identical with progress active on STDERR; exit codes unchanged (run never 1). |

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED test |
|---|---|---|---|
| Subprocess spawn (`run_streamed`) | Applicable | argv-LIST only, `shell` never truthy (SKILL rule 5); mirrors `.run()`. | Assert no shell string composition. |
| Secret in streamed output | Applicable | Per-line `scrub_secrets(line, argv)` BEFORE relay AND in accumulator. | Leak test above. |
| Maestro `--no-ansi` argv | Applicable | Pass `--no-ansi` explicitly; treat each line as opaque relayed text (do NOT parse step structure). | Assert `--no-ansi` present in composed argv. |
| Git/PR/commit/push automation | N/A | No VCS automation in this change. | — |

## Migration / Rollout

No migration. `NullProgressReporter` default (and `--quiet`) restores today's
silent behavior; `.run()` unchanged keeps buffered callers unaffected. Revert
per slice.

## Open Questions

- [ ] **Maestro real non-TTY output format (UNVERIFIED here).** No pinned
  Maestro binary/version exists in the repo (pyproject/README/examples only
  reference it); context7 (docs.maestro.dev) confirms `maestro test` exposes
  `--[no-]ansi`/`--[no-]color` and `--format=<NOOP|JUNIT|HTML>` but not whether
  ANSI auto-disables on a pipe. **apply/verify MUST confirm** the exact plain
  `--no-ansi` step-line format against a live device. **Conservative default
  (ship this regardless):** pass `--no-ansi`; relay each line as opaque text;
  never parse step boundaries to synthesize iteration events in TOOL_MANAGED.
- [ ] Slice sizing: each of the 4 slices (a: port+registry+CLI+manual fix;
  b: `run_streamed`+Maestro DRIVER_MANAGED table; c: TOOL_MANAGED relay+recap+
  `--no-ansi`; d: `--quiet`) is independently shippable, reviewable, and under
  the 800-line budget — confirmed at task-forecast time by `sdd-tasks`.
