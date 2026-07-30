# Design: `perfvibe markers` command group (snippet + doctor)

## Technical Approach

First nested-Typer sub-app in the repo. `cli/commands/markers.py` builds its own
`typer.Typer()` (`markers_app`) with two callbacks (`snippet`, `doctor`); `main.py`
mounts it with `app.add_typer(markers_app, name="markers")` next to the five flat
`app.command(...)` registrations (main.py:111-134). Both subcommands read the SAME
`ctx.obj` (`output`, `config`, `config_path`) that `main_callback` sets once
(main.py:100) — Typer/Click propagate the root `Context.obj` down through
`add_typer`, so no new plumbing. All logic lives in the ONE module (init.py precedent),
pure helpers at top composed by the two callbacks at the bottom. `doctor` reuses the SAME
public line classifier that `parse()` consumes (see below) — CLI→adapter import is
existing precedent (budget_check.py). No port, no use-case, no new package.

## Modified Capabilities

**NOT None** (revises proposal). `adapters/markers_adb_logcat.py` gains a PUBLIC
classification surface: `classify_line(raw_line) -> LineVerdict` plus `LineVerdict`,
`LineKind`, and reason constants. `parse()` is REFACTORED to delegate every per-line
decision to `classify_line`, so tag/regex/JSON logic lives in ONE place and both
`parse()` and `doctor` classify through it — zero duplication. This is strictly
ADDITIVE: `parse()`'s signature and observable output (`markers`, `partial_coverage`,
`diagnostic`, and the internal `perf_lines_seen` used for the diagnostic) are
UNCHANGED — a characterization RED test pins byte-identical output before/after.

## Architecture Decisions

| Decision | Choice | Rejected | Rationale |
|---|---|---|---|
| CLI surface | `add_typer` nested sub-app (`markers snippet` / `markers doctor`) | Flat `markers-snippet` (budget-check style) | Requested "command group"; grouped `--help`. Propagation is a test task, not a blocker. |
| Where code lives | ALL logic in `cli/commands/markers.py`; pure module-level helpers imported by tests | New `domain/markers_tool.py`; port for `doctor` | Single behavior, one file to open (python-architecture r1); a port for one caller violates r3. |
| doctor reuse seam | Direct `AdbLogcatMarkerSource().parse(lines, iterations=…)`; read `result.markers`/`.diagnostic` | Call private `_build_diagnostic`; enrich `MarkerParseResult` | `parse()` already returns `diagnostic` for partial/zero coverage — zero new surface, parser unchanged. |
| doctor breakdown source | Per-line via a SHARED public `classify_line` that `parse()` also consumes; `doctor` buckets its verdicts into `parse_failures:[{line,reason}]` + category counts | Coarse `unparsed_perf_lines`+diagnostic only; duplicate parser regexes in doctor | Coordinator decision: a doctor MUST say WHY each line failed. Extracting the classifier keeps ONE source of truth (no regex/tag duplication anywhere) — richer than aggregate, still drift-free. |
| `PERF_TAG` home | Promote to public `PERF_TAG` in `domain/model.py` next to `GATE_*` (model.py:268-270) | New `domain/constants.py` | model.py already hosts bare string constants; blast radius is 3 lines in one file. |
| `--json` contracts | Two new `contracts/markers_snippet_v1.py` / `markers_doctor_v1.py`, `SCHEMA_VERSION=1` | One shared module; reuse `json_v1` | Brand-new payloads; mirror init_v1.py builder pattern (pure `build_*_payload`, `__all__`). |
| Snippet source of truth | Pure `render_snippet(lang)` + `emitted_sample()` in markers.py imported by command AND both tests | Inline string in command; hardcode sample in tests | One function feeds CLI, anti-drift contract test, and README doc-sync — no divergence possible. |

## Data Flow

    main_callback (main.py:45-108) ── sets ctx.obj{output,config,config_path} @100
        └─ app.add_typer(markers_app, name="markers")
             ├─ snippet(ctx, --lang): render_snippet(lang) ─→ json? build_snippet_payload → render_json ; else echo code   → 0
             └─ doctor(ctx, [line], stdin):
                  detect_mode(arg, sys.stdin.isatty()) → "line" | "stdin" | usage-error(2)
                  read buffer → AdbLogcatMarkerSource().parse(buffer, iterations=len_or_1)
                  bucket(buffer, PERF_TAG, PERF_META_TAG) + result.markers + result.diagnostic
                  → json? build_doctor_payload → render_json ; else pretty summary            → 0 (even zero-parsed)

## Interfaces / Contracts

`markers_snippet_v1` (`schema_version=1`): `{ "schema_version": 1, "lang": "ts"|"js", "code": "<snippet>" }`
`build_snippet_payload(*, lang: str, code: str) -> dict`.

**Shared classifier** (public, in `markers_adb_logcat.py`):

```python
class LineKind(str, Enum): COMPLETED, MARK_START, PERF_META, IGNORED, FAILURE
# reason constants (FAILURE only): REASON_MALFORMED_TEXT, REASON_INVALID_JSON,
#                                  REASON_INVALID_VALUE, REASON_OVERSIZED
@dataclass(frozen=True)
class LineVerdict: kind: LineKind; marker: Marker | None = None; reason: str | None = None
def classify_line(raw_line: str) -> LineVerdict: ...   # sole owner of tag/regex/JSON logic
```

`parse()` becomes: for each line, `v = classify_line(line)`; append `v.marker` when set;
count a PERF-payload line when `v.kind in {COMPLETED, MARK_START}` or `(v.kind is FAILURE and
v.reason != REASON_OVERSIZED)` — reproducing today's `perf_lines_seen`. `partial_coverage` +
`_build_diagnostic` unchanged.

`markers_doctor_v1` (`schema_version=1`) — ONE schema; single-line mode carries one line's worth:

```json
{ "schema_version": 1, "mode": "line" | "stdin",
  "input_summary": { "lines_scanned": 1 },
  "breakdown": {
    "parsed": [ { "name": "Home_render", "value": 128.0, "unit": "ms" } ],
    "mark_start_without_end": 0,
    "perf_meta": 0,
    "parse_failures": [ ],
    "ignored": 0 },
  "coverage_ok": true,
  "diagnostic": null }
```

`parse_failures` entries: `{"line": "<raw>", "reason": "malformed_text|invalid_json|invalid_value|oversized"}`.
`build_doctor_payload(*, mode, lines_scanned, parsed: Sequence[Marker], mark_start_without_end: int, perf_meta: int, parse_failures: Sequence[tuple[str, str]], ignored: int, coverage_ok: bool, diagnostic: str | None) -> dict`.
`doctor` fills these by iterating `classify_line` over the buffer (single pass, single source of truth), then calls `parse()` only for `diagnostic`/`partial_coverage` (which reuse the same classifier). `coverage_ok = bool(parsed) and not partial_coverage`.

Typer signatures: `snippet(ctx, lang: Lang = Option(Lang.ts, "--lang"))` where `Lang(str, Enum)` = ts|js (unknown value → Typer usage error exit 2, no runtime path). `doctor(ctx, line: str | None = Argument(None))`.

## Exit paths (NEVER 1)

| Cmd | 0 | 2 (usage) | 3 (runtime) |
|---|---|---|---|
| snippet | valid lang → code | unknown `--lang` (Enum) | — (pure string render, no I/O) |
| doctor | any parse result incl. zero markers | unknown flag; arg AND piped stdin (ambiguous); neither arg nor pipe with stdin=TTY (nothing to validate, `emit_error`+hint) | `sys.stdin.read()` raises `OSError` |

Reuse `OutputContext`, `emit_error`, `render_json` exactly like init.py (init.py:533-544). Mode detect via `sys.stdin.isatty()` mirroring compare.py:154.

## Testing Strategy

| Layer | What | Where |
|---|---|---|
| Integration | **#1 risk**: `CliRunner().invoke(app, ["--json","markers","doctor","[PERF] X: 12ms"])` → exit 0 AND payload reflects json_mode (proves global flag before subcommand resolves through `main_callback` and `ctx.obj` reaches the sub-app); exit-code matrix; piped stdin; ambiguous both/neither | `tests/integration/test_cli_markers.py` |
| Contract | anti-drift: feed `emitted_sample()` through REAL `parse()`, assert exact `Marker`; required-keys + `test_rejects_shape_change_without_version_bump` for both payloads | `tests/contract/test_markers_snippet_parses.py`, `test_markers_{snippet,doctor}_v1_contract.py` |
| Characterization | **`parse()` refactor is behavior-neutral**: golden inputs → identical `MarkerParseResult` (markers, partial_coverage, diagnostic) before/after delegating to `classify_line` | extend `tests/integration/test_markers_adb_logcat.py` |
| Unit | `classify_line` per verdict (each `FAILURE` reason, `MARK_START`, `PERF_META`, `IGNORED`, oversized); `render_snippet` per lang; `detect_mode`; doctor bucketing; builders; README doc-sync: `render_snippet("ts")` == README fenced block (import fn, NO subprocess) | `tests/unit/test_markers.py`, `tests/unit/test_readme_markers_sync.py` |

## Threat Matrix

N/A for all rows — `markers` spawns NO subprocess (doctor validates provided text; it does NOT run `adb`), performs no git/PR/exec-file work. One input boundary: untrusted arg/stdin text → `parse()`, already guarded by `_MAX_LINE_LENGTH` bound + `json.loads`-only (SKILL rule 5, markers_adb_logcat.py:47/160). RED test: oversized/garbage line is skipped, exit 0, never crashes.

## File Changes

| File | Action | Description |
|---|---|---|
| `src/perf/cli/commands/markers.py` | Create | `markers_app` + `snippet`/`doctor` callbacks + pure helpers (`render_snippet`, `emitted_sample`, `detect_mode`, bucketing) |
| `src/perf/cli/main.py` | Modify | import `markers_app`; `app.add_typer(markers_app, name="markers")` |
| `src/perf/domain/model.py` | Modify | add `PERF_TAG = "[PERF]"` near `GATE_*` (model.py:270) |
| `src/perf/adapters/markers_adb_logcat.py` | Modify | import `PERF_TAG` (line 40); delete local `_PERF_TAG` (line 42); keep `_PERF_META_TAG`; extract PUBLIC `classify_line`/`LineVerdict`/`LineKind`/reason constants; `parse()` delegates to it (behavior-neutral) |
| `src/perf/contracts/markers_snippet_v1.py` | Create | `build_snippet_payload` |
| `src/perf/contracts/markers_doctor_v1.py` | Create | `build_doctor_payload` |
| `README.md`, `docs/commands.md` | Modify | instrumentation section (fenced snippet) + `markers` entry extending the schema_version table |
| `tests/{integration,contract,unit}/…` | Create | per Testing Strategy |

## Migration / Rollout

No migration. Purely additive; `PERF_TAG` promotion is an atomic rename, no data/behavior impact.

## Open Questions

- [x] **doctor breakdown granularity** — RESOLVED (coordinator): per-line `parse_failures:[{line,reason}]` via a SHARED public `classify_line` extracted from `parse()`. The adapter gains an additive public API (Modified Capabilities); `parse()` stays behavior-neutral, pinned by a characterization test. No regex/tag logic is duplicated anywhere.
