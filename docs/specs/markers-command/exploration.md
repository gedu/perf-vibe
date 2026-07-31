# Exploration: `perfvibe markers` command group (snippet + doctor)

## Current State

- `src/perf/cli/main.py:28` builds exactly ONE `typer.Typer()`; all commands
  (`run`/`compare`/`budget-check`/`history`/`init`) are registered flat via
  `app.command(name=...)(fn)` (lines 111-134). Repo-wide `typer.Typer(` → 1 hit,
  `add_typer` → 0 hits. A `markers` sub-group would be the **first nested-Typer
  precedent** here.
- `main_callback` (main.py:45-108) sets
  `ctx.obj = {"output": ..., "config": ..., "config_path": ...}` once at the
  root; every flat command reads it off `ctx.obj`. Typer/click propagate
  `ctx.obj` through `add_typer` by default, but this is **unverified in this
  repo** — no existing test exercises it.
- `AdbLogcatMarkerSource.parse(self, lines: Sequence[str], *, iterations: int)
  -> MarkerParseResult` (`src/perf/adapters/markers_adb_logcat.py:71`) is a plain
  public method. CLI commands already import adapters directly today
  (`budget_check.py:20-25` imports `perf.adapters.registry`), so `doctor` calling
  `AdbLogcatMarkerSource().parse([line], iterations=1)` directly from
  `cli/commands/markers.py` matches existing precedent — no port/use-case
  indirection needed, and violates no hexagonal rule.
- **Key simplification**: `parse()` already returns
  `MarkerParseResult.diagnostic` populated whenever coverage isn't full
  (`_build_diagnostic`, lines 117-145). With `iterations=1` and one line,
  `doctor` never needs to touch the private `_build_diagnostic` staticmethod —
  `result.markers` (0 or 1 items) + `result.diagnostic` from the single
  `parse()` call is enough. No reuse seam needed at all.
- `_PERF_TAG = "[PERF]"` (`markers_adb_logcat.py:42`) has exactly 3 references —
  the definition plus 2 uses (lines 83, 87), **all inside this one file**.
  Repo-wide grep confirms zero other references. Promotion is fully safe.
- `src/perf/domain/model.py` already hosts bare module-level string constants in
  this style (`GATE_PASS`/`GATE_FAIL`/`GATE_SKIPPED` at 268-270) — so the
  promoted tag belongs in `domain/model.py`, **not** a new `domain/constants.py`
  module (python-architecture rule 3: no new module until it earns its place).
- Contract modules
  (`src/perf/contracts/{json_v1,init_v1,compare_v1,budget_check_v1,compare_all_v1,history_v1}.py`)
  share one shape: docstring citing the SKILL rule, `SCHEMA_VERSION` int, one
  pure `build_*_payload(...) -> dict[str, Any]`, `__all__`. Most are
  `SCHEMA_VERSION = 1`; only `init_v1.py` bumped to 2. Two new modules following
  this pattern (e.g. `contracts/markers_snippet_v1.py`,
  `contracts/markers_doctor_v1.py`) are the natural convention fit.
- `cli/output/json_reporter.py`'s single `render_json()` and
  `cli/output/context.py`/`errors.py` (`OutputContext`, `emit_error`,
  `emit_warning`) are command-agnostic — both new subcommands consume them
  exactly like `init`/`compare` do, no changes required.
- `docs/commands.md` documents 5 commands with current schema versions — needs a
  new `markers` entry, not a rewrite. `README.md`'s "five commands" table (lines
  115-126) has **no markers/instrumentation section and no doc-sync test today**
  — the "docs: rewrite README with real output" commit was a manual, untested
  copy.
- `tests/contract/test_json_v1_contract.py` is the clearest pattern to mirror
  (required-keys-and-types dict, per-item shape pinning, exact-set schema-drift
  assertion). No existing test wires a snippet's emitted lines through the real
  parser or syncs README content — both are genuinely new test shapes.
- `openspec/config.yaml` confirms hybrid persistence; no prior `markers-command`
  folder existed.

## Affected Areas

- `src/perf/cli/main.py` — new `add_typer` wiring.
- `src/perf/adapters/markers_adb_logcat.py` — `_PERF_TAG` moves out;
  `parse()`/`MarkerParseResult` reused unchanged.
- `src/perf/domain/model.py` — gains the promoted tag constant next to `GATE_*`.
- `src/perf/cli/commands/markers.py` (new) — both subcommands, one module.
- `src/perf/contracts/` — one or two new `markers_*_v1.py` modules.
- `tests/contract/`, `tests/integration/test_cli_markers.py`, possibly
  `tests/unit/` — new tests.
- `README.md` + `docs/commands.md` — new instrumentation section / command entry,
  plus a new doc-sync test.

## Approaches

### 1. Typer sub-app via `add_typer` (`perfvibe markers snippet` / `markers doctor`)
- Pros: matches the requested UX exactly; typer/click propagate `ctx.obj`
  through nested apps by default; keeps `markers.py` self-contained.
- Cons: first nested-Typer precedent in the repo — `ctx.obj` propagation here is
  unproven, not test-covered yet.
- Effort: Low.

### 2. Flat hyphenated commands (`perfvibe markers-snippet` / `markers-doctor`)
- Pros: zero new pattern, byte-identical wiring, `ctx.obj` propagation already
  proven.
- Cons: does not match the requested "command group" UX; loses
  `perfvibe markers --help` grouping.
- Effort: Low.

## Recommendation

Approach 1 — it's what was explicitly requested and the risk (`ctx.obj`
propagation through `add_typer`) is a testing task, not a feasibility blocker.
Pair it with: (a) promote `PERF_TAG` into `domain/model.py`, not a new file;
(b) have `doctor` call `AdbLogcatMarkerSource().parse([line], iterations=1)`
directly — no need to touch `_build_diagnostic`; (c) two `markers_*_v1.py`
contract modules at `SCHEMA_VERSION = 1` each; (d) model the README doc-sync test
as a `tests/unit/` test that imports the snippet-rendering function directly and
asserts it matches the README's fenced block — no CLI subprocess needed.

## Risks

- Nested `add_typer` + `ctx.obj` propagation is unproven in this codebase —
  needs an explicit integration test, including that global flags before the
  subcommand (`perfvibe --json markers doctor "..."`) still resolve through
  `main_callback`.
- The anti-drift contract test (snippet's sample lines through the real parser)
  and the README doc-sync test are both genuinely new test shapes with no
  existing file to copy.

## Ready for Proposal

Yes.
