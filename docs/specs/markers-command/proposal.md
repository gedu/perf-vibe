# Proposal: `perfvibe markers` command group (snippet + doctor)

## Intent

The CLI CONSUMES `[PERF]` markers from logcat (`AdbLogcatMarkerSource`) but never teaches app devs how to EMIT them. The producer/consumer contract is undocumented: a malformed marker silently yields "0 markers" with no way to tell why. Give app devs a copy-paste emitter (single source of truth) and a diagnostic that validates real logcat against the ACTUAL parser and explains the verdict.

## Scope

### In Scope
- New `perfvibe markers` sub-app (Typer via `add_typer` — first nested-Typer in repo).
- `markers snippet [--lang ts|js] [--json]`: prints the TEXT-form emitter (`[PERF] <name>: <n>ms`) mirroring the user's `react-native-performance` module (markStart/markEnd/measureMark trio + MARKERS route map). `--json`: {schema_version, lang, code}.
- `markers doctor [<logcat line>] [--json]`: validates a single positional line OR a piped stdin stream against `AdbLogcatMarkerSource().parse(...)`. Single line → iterations=1. Stdin → whole buffer treated as one capture, INFORMATIONAL breakdown (no coverage gate). Reports parsed markers, markStart-without-markEnd, `[PERF-META]` skips, `[PERF]` parse failures (with reason), non-`[PERF]` ignored; surfaces `result.diagnostic`.
- Promote `_PERF_TAG` → public `PERF_TAG` in `domain/model.py` (next to `GATE_*`); parser + snippet both import it.
- Two contracts `markers_snippet_v1.py` / `markers_doctor_v1.py` (SCHEMA_VERSION=1).
- README instrumentation section (snippet inline) + `docs/commands.md` entry.

### Out of Scope
- JSON emitter form (text form only; no `--form` flag).
- `doctor` as a CI gate (advisory, never exits 1).
- Native iOS/Android emitters (TS/JS only).

## Capabilities

### New Capabilities
- `markers`: the `markers snippet` + `markers doctor` subcommands, the emitter text-form contract, the shared `PERF_TAG` constant, and their `--json` schemas.

### Modified Capabilities
- `markers-parser` (additive): `AdbLogcatMarkerSource` gains ONE public line-classification function that both `parse()` and `markers doctor` call — the single source of truth for per-line verdicts, so `doctor` never duplicates tag/regex/JSON logic (drift). This is ADDITIVE: `parse()`'s signature, inputs, and `MarkerParseResult` output are unchanged; it delegates classification internally. `_PERF_TAG`'s home also moves to `domain/model.py` (no behavior change).

## Approach

Exploration Approach 1. `markers.py` holds both subcommands (init.py-style single module) registered via `add_typer`. `doctor` calls `parse()` directly (adapter-import precedent) — no port/use-case seam. Exit codes: snippet 0/2; doctor 0/2/3, never 1. `--json` payloads share a coherent schema across single-line and stdin modes.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/perf/cli/main.py` | Modified | `add_typer` wiring for `markers` |
| `src/perf/cli/commands/markers.py` | New | Both subcommands, one module |
| `src/perf/domain/model.py` | Modified | Promoted `PERF_TAG` constant |
| `src/perf/adapters/markers_adb_logcat.py` | Modified | `_PERF_TAG` moves out; imports it |
| `src/perf/contracts/markers_{snippet,doctor}_v1.py` | New | Payload builders, SCHEMA_VERSION=1 |
| `README.md`, `docs/commands.md` | Modified | Instrumentation section + command entry |
| `tests/{contract,integration,unit}/` | New | Anti-drift, ctx.obj, doc-sync tests |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| `ctx.obj` propagation through nested `add_typer` unproven | Med | Integration test incl. `perfvibe --json markers doctor "..."` resolving through main_callback |
| Shipped snippet format drifts from parser | Med | Contract test: feed snippet's sample line through real `parse()`, assert Marker |
| README snippet drifts from code | Med | Unit doc-sync test: render fn output == README fenced block (no subprocess) |
| stdin-vs-arg ambiguity (both/neither) | Low | Detect; both-or-neither(TTY) = usage error (exit 2) |

## Rollback Plan

Revert the change set. `markers` is purely additive; `PERF_TAG` promotion is a pure rename (both refs updated atomically) with no behavior change, so no data/migration impact.

## Dependencies

- No new packages. Reuses `AdbLogcatMarkerSource`, `render_json`, `OutputContext`.

## Success Criteria

- [ ] `markers snippet --lang ts|js` prints a paste-ready emitter; `--json` carries schema_version.
- [ ] `markers doctor` diagnoses single-line and piped stdin, exits 0/2/3 (never 1).
- [ ] Anti-drift + doc-sync + `ctx.obj`-propagation tests pass.
- [ ] `PERF_TAG` shared; README + docs/commands.md updated.
