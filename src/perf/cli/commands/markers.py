"""`perfvibe markers` command group — `snippet` (emits a paste-ready TS/JS
instrumentation module) and `doctor` (diagnoses a real logcat line or piped
capture against the SAME parser `perf run` uses) (markers-command spec,
design `openspec/changes/markers-command/design.md`).

First nested-`Typer` sub-app in the repo (design "Technical Approach"):
`markers_app` mounts under the root `app` via `add_typer(markers_app,
name="markers")` in `cli/main.py`; Typer/Click propagate the root
`Context.obj` (`output`/`config`/`config_path`, set once by `main_callback`)
down through `add_typer` with no extra plumbing.

ALL logic lives in this ONE module (design decision "Where code lives"):
pure, directly unit-testable helpers at the top (`render_snippet`,
`emitted_sample`, `detect_mode`, `bucket_lines`), composed by the two
`snippet`/`doctor` callbacks at the bottom. `doctor` classifies exclusively
through `perf.adapters.markers_adb_logcat.classify_line` — the SAME shared
classifier `AdbLogcatMarkerSource.parse()` consumes (spec "Shared
Line-Classification Function") — never re-deriving tag/regex/JSON logic of
its own.

Both subcommands are read-only, perform no device/subprocess I/O, and NEVER
exit `1` (that code stays reserved for `compare`/`budget-check`
regressions, perf-cli-standards rule 7): `0` success (including a clean
diagnosis that found zero markers), `2` usage error, `3` runtime/tooling
failure.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import typer

from perf.adapters.markers_adb_logcat import (
    REASON_OVERSIZED,
    AdbLogcatMarkerSource,
    LineKind,
    classify_line,
)
from perf.cli.output.context import NON_TTY_NUDGE, OutputContext
from perf.cli.output.errors import emit_error
from perf.cli.output.json_reporter import render_json
from perf.contracts.markers_doctor_v1 import build_doctor_payload
from perf.contracts.markers_snippet_v1 import build_snippet_payload
from perf.domain.model import PERF_TAG, Marker

__all__ = [
    "AmbiguousDoctorInputError",
    "DoctorBreakdown",
    "Lang",
    "bucket_lines",
    "detect_mode",
    "doctor",
    "emitted_sample",
    "markers_app",
    "render_snippet",
    "snippet",
]

# ===== snippet: pure emitter source (spec "Text-Form Emitter Contract") =====


class Lang(StrEnum):
    """`markers snippet --lang` choice (spec "Snippet Language Selection").
    An unknown value is rejected by Typer/Click's own Enum validation before
    any command code runs — a usage error (exit `2`), never a runtime
    branch here."""

    ts = "ts"
    js = "js"


_TAG_PLACEHOLDER = "__PERF_TAG__"
_LINE_PLACEHOLDER = "__MARKER_LINE__"


def _marker_line(name: str, duration: str | float, *, tag: str = PERF_TAG) -> str:
    """The ONE shared text-form marker-line format (verify-report Phase 3
    finding W-1 fix): the snippet's emitted `console.log` template AND
    `emitted_sample()` BOTH derive from this SAME function — never two
    independently hand-maintained strings that could silently drift apart.
    `tag` defaults to the REAL `PERF_TAG`; `render_snippet` passes the
    placeholder token instead so the templates below stay drift-proof
    against the parser's own tag (spec "Shared PERF_TAG Constant")."""

    return f"{tag} {name}: {duration}ms"


# The exact template-literal body embedded in the snippet's `console.log`
# call, built from the SAME `_marker_line` function `emitted_sample()`
# calls below — substituted into `_TS_SNIPPET`/`_JS_SNIPPET` via
# `_LINE_PLACEHOLDER` at render time.
_CONSOLE_LOG_LINE = _marker_line("${name}", "${measureEntry.duration}", tag=_TAG_PLACEHOLDER)

# Mirrors the user's real, proven-working `react-native-performance`
# reference module VERBATIM (markers-command design "Snippet source of
# truth"; verify-report Phase 3 finding C-1 fix): a DEFAULT import (never a
# named `{ performance }` import — the package's documented public API),
# a `markStart`/`markEnd`/`measureMark` trio, a `MARKERS` route-name map,
# and a load-bearing try/catch around `performance.measure` (it THROWS when
# a referenced start/end mark is absent — e.g. a `markStart` without a
# matching `markEnd` — so pasted code must degrade via `console.warn`
# rather than crash). The tag is a placeholder token substituted with the
# REAL `PERF_TAG` constant at render time (never an f-string literal
# `[PERF]`) so the emitted text can never drift from the parser's own tag
# (spec "Shared PERF_TAG Constant").
_TS_SNIPPET = """\
import performance from 'react-native-performance';

const MARKERS = {
  LENDING: '/loans',
};

function markStart(name: string) {
  performance.mark(`${name}_start`);
}

function markEnd(name: string) {
  performance.mark(`${name}_end`);
  measureMark(name);
}

function measureMark(name: string) {
  try {
    const measureEntry = performance.measure(`${name}_measure`, `${name}_start`, `${name}_end`);
    console.log(`__MARKER_LINE__`);
  } catch (error) {
    console.warn(`Performance measure failed for ${name}:`, error);
  }
}

export { markStart, markEnd, MARKERS };
"""

_JS_SNIPPET = """\
import performance from 'react-native-performance';

const MARKERS = {
  LENDING: '/loans',
};

function markStart(name) {
  performance.mark(`${name}_start`);
}

function markEnd(name) {
  performance.mark(`${name}_end`);
  measureMark(name);
}

function measureMark(name) {
  try {
    const measureEntry = performance.measure(`${name}_measure`, `${name}_start`, `${name}_end`);
    console.log(`__MARKER_LINE__`);
  } catch (error) {
    console.warn(`Performance measure failed for ${name}:`, error);
  }
}

export { markStart, markEnd, MARKERS };
"""


def render_snippet(lang: str) -> str:
    """Renders the paste-ready TS (`lang="ts"`) or JS (`lang="js"`) emitter
    module (spec "Text-Form Emitter Contract"), a verbatim mirror of the
    user's proven-working `react-native-performance` module (C-1 fix).
    Substitutes the shared `_CONSOLE_LOG_LINE` (built from `_marker_line`,
    the same function `emitted_sample()` uses) before substituting the REAL
    `PERF_TAG` so the emitted tag can never independently drift from the
    parser's own (spec "Shared PERF_TAG Constant")."""

    template = _TS_SNIPPET if lang == Lang.ts else _JS_SNIPPET
    code = template.replace(_LINE_PLACEHOLDER, _CONSOLE_LOG_LINE)
    return code.replace(_TAG_PLACEHOLDER, PERF_TAG)


def emitted_sample() -> str:
    """ONE representative marker line the generated snippet would emit at
    runtime — the anti-drift single source of truth (design "Snippet source
    of truth"): built from the SAME `_marker_line` function `render_snippet`
    substitutes into its `console.log` template, so a contract test feeding
    this exact line through the REAL `AdbLogcatMarkerSource.parse()` (spec
    scenario "Emitted line parses cleanly") genuinely pins the shared
    format, not two independently maintained strings (verify-report Phase 3
    finding W-1 fix)."""

    return _marker_line("example", 123)


# ===== doctor: input-mode detection (spec "Doctor Input Mode Detection") =====


class AmbiguousDoctorInputError(Exception):
    """Raised by `detect_mode` when exactly one input source cannot be
    determined: either BOTH a `<logcat line>` argument and piped (non-TTY)
    stdin were given, or NEITHER (no argument, and stdin is a bare TTY)."""

    def __init__(self, *, arg_present: bool) -> None:
        self.arg_present = arg_present
        if arg_present:
            detail = "both a <logcat line> argument and piped stdin were provided"
        else:
            detail = "no <logcat line> argument was given and stdin is not piped"
        super().__init__(f"{detail} — exactly one input source is required")


def detect_mode(arg: str | None, *, stdin_is_tty: bool) -> str:
    """Resolves `"line"` or `"stdin"` mode from the argument's presence and
    stdin's TTY-ness alone (spec "Doctor Input Mode Detection"):

    - argument present, stdin IS a TTY (nothing piped)      -> `"line"`
    - no argument, stdin is NOT a TTY (piped)                -> `"stdin"`
    - argument present AND stdin is NOT a TTY (both)          -> usage error
    - no argument AND stdin IS a TTY (neither)                -> usage error
    """

    if arg is not None and stdin_is_tty:
        return "line"
    if arg is None and not stdin_is_tty:
        return "stdin"
    raise AmbiguousDoctorInputError(arg_present=arg is not None)


# ===== doctor: per-line bucketing (spec "Diagnosis Categories") =====

_TRUNCATE_LENGTH = 120
_ELLIPSIS = "…"


def _truncate_oversized_line(raw_line: str) -> str:
    """An oversized line's echoed `line` field is truncated to its first
    120 characters + a single `…` (spec "Diagnosis Categories") — the raw
    line itself may be arbitrarily long; `parse()`'s marker extraction
    already excludes it from any produced `Marker`, unchanged."""

    return raw_line[:_TRUNCATE_LENGTH] + _ELLIPSIS


@dataclass(frozen=True)
class DoctorBreakdown:
    """Per-category tally of ONE `classify_line` pass over a buffer — the
    shape `doctor` feeds straight into `build_doctor_payload` (design "doctor
    breakdown source")."""

    parsed: tuple[Marker, ...]
    mark_start_without_end: int
    perf_meta: int
    parse_failures: tuple[tuple[str, str], ...]
    ignored: int


def bucket_lines(lines: Sequence[str]) -> DoctorBreakdown:
    """Classifies every line in `lines` through the SAME shared
    `classify_line` function `AdbLogcatMarkerSource.parse()` uses internally
    (spec "Shared Line-Classification Function") — `doctor` never
    re-derives tag/regex/JSON logic of its own."""

    parsed: list[Marker] = []
    mark_start_without_end = 0
    perf_meta = 0
    parse_failures: list[tuple[str, str]] = []
    ignored = 0

    for raw_line in lines:
        verdict = classify_line(raw_line)
        if verdict.kind is LineKind.COMPLETED:
            assert verdict.marker is not None  # COMPLETED always carries a marker
            parsed.append(verdict.marker)
        elif verdict.kind is LineKind.MARK_START:
            mark_start_without_end += 1
        elif verdict.kind is LineKind.PERF_META:
            perf_meta += 1
        elif verdict.kind is LineKind.IGNORED:
            ignored += 1
        else:  # LineKind.FAILURE
            assert verdict.reason is not None  # FAILURE always carries a reason
            echoed = (
                _truncate_oversized_line(raw_line)
                if verdict.reason == REASON_OVERSIZED
                else raw_line
            )
            parse_failures.append((echoed, verdict.reason))

    return DoctorBreakdown(
        parsed=tuple(parsed),
        mark_start_without_end=mark_start_without_end,
        perf_meta=perf_meta,
        parse_failures=tuple(parse_failures),
        ignored=ignored,
    )


# ===== doctor: pretty rendering =====


def _render_doctor_pretty(payload: dict) -> str:
    breakdown = payload["breakdown"]
    lines = [
        f"mode: {payload['mode']}",
        f"lines scanned: {payload['input_summary']['lines_scanned']}",
        f"parsed: {len(breakdown['parsed'])}",
    ]
    for marker in breakdown["parsed"]:
        lines.append(f"  - {marker['name']}: {marker['value']}{marker['unit']}")
    lines.append(f"mark_start_without_end: {breakdown['mark_start_without_end']}")
    lines.append(f"perf_meta: {breakdown['perf_meta']}")
    lines.append(f"ignored: {breakdown['ignored']}")
    failures = breakdown["parse_failures"]
    lines.append(f"parse_failures: {len(failures)}")
    for failure in failures:
        lines.append(f"  - {failure['reason']}: {failure['line']}")
    lines.append(f"coverage_ok: {payload['coverage_ok']}")
    if payload["diagnostic"]:
        lines.append(f"diagnostic: {payload['diagnostic']}")
    return "\n".join(lines) + "\n"


# ===== the `markers_app` sub-app (design "Technical Approach") =====

markers_app = typer.Typer(
    add_completion=False,
    context_settings={"help_option_names": ["--help", "-h"]},
    help="Instrumentation snippet + logcat line diagnostics for [PERF] markers.",
)


def snippet(
    ctx: typer.Context,
    # ruff's B008 can't statically prove an Enum-member default (`Lang.ts`)
    # is side-effect-free, unlike a bare literal — `typer.Option(...)` with
    # an Enum default is the standard, documented Typer idiom (immutable,
    # evaluated once at import time, matches every other Option default in
    # this codebase).
    lang: Lang = typer.Option(  # noqa: B008
        Lang.ts, "--lang", help="Emitter language to generate: ts (default) or js"
    ),
) -> None:
    """Emits a paste-ready TS/JS instrumentation module matching the shape
    `AdbLogcatMarkerSource.parse()` consumes (spec "Text-Form Emitter
    Contract"). Pretty output is the raw code only — no decoration that
    would break a copy-paste (spec "Snippet --json Payload"). Exit `0`
    always; an unknown `--lang` is rejected by Typer's own Enum validation
    (exit `2`) before this body ever runs."""

    state: dict = ctx.obj or {}
    output: OutputContext = state["output"]

    code = render_snippet(lang.value)

    if output.json_mode:
        typer.echo(render_json(build_snippet_payload(lang=lang.value, code=code)))
    else:
        if output.should_nudge_stderr:
            typer.echo(NON_TTY_NUDGE, err=True)
        typer.echo(code)

    raise typer.Exit(code=0)


def doctor(
    ctx: typer.Context,
    line: str | None = typer.Argument(
        None, help="A single [PERF]-tagged logcat line to diagnose (omit to read piped stdin)"
    ),
) -> None:
    """Diagnoses a single logcat line or a piped capture against the SAME
    classifier `AdbLogcatMarkerSource.parse()` uses (spec "Shared
    Line-Classification Function"). Exit `0` on ANY successful diagnosis —
    including zero markers found (spec "Nothing parsed is still a
    successful diagnosis") — `2` on a usage error (ambiguous input source,
    unknown flag), `3` only on a runtime failure (stdin read error). NEVER
    exit `1` (spec "Doctor Exit-Code Discipline")."""

    state: dict = ctx.obj or {}
    output: OutputContext = state["output"]

    try:
        mode = detect_mode(line, stdin_is_tty=sys.stdin.isatty())
    except AmbiguousDoctorInputError as exc:
        emit_error(
            output,
            str(exc),
            hint="pass exactly one of a `<logcat line>` argument or piped stdin",
        )
        raise typer.Exit(code=2) from None

    if mode == "line":
        assert line is not None  # guaranteed by detect_mode's "line" branch
        lines: list[str] = [line]
    else:
        try:
            buffer = sys.stdin.read()
        except OSError as exc:
            emit_error(output, f"failed to read piped stdin: {exc}")
            raise typer.Exit(code=3) from None
        lines = buffer.splitlines()

    breakdown = bucket_lines(lines)
    # `iterations=1` for BOTH modes (design "Data Flow"): reusing `parse()`
    # only for its `diagnostic` text, and it makes `partial_coverage` reduce
    # to exactly `not bool(parsed)` — matching spec "coverage_ok is
    # informational": single-line mode asks "did THIS line parse", stdin
    # mode asks "did ANY marker parse" — never a ratio against a real
    # per-run iteration count `doctor` has no way to know.
    result = AdbLogcatMarkerSource().parse(lines, iterations=1)
    coverage_ok = bool(breakdown.parsed)

    payload = build_doctor_payload(
        mode=mode,
        lines_scanned=len(lines),
        parsed=breakdown.parsed,
        mark_start_without_end=breakdown.mark_start_without_end,
        perf_meta=breakdown.perf_meta,
        parse_failures=breakdown.parse_failures,
        ignored=breakdown.ignored,
        coverage_ok=coverage_ok,
        diagnostic=result.diagnostic,
    )

    try:
        if output.json_mode:
            typer.echo(render_json(payload))
        else:
            if output.should_nudge_stderr:
                typer.echo(NON_TTY_NUDGE, err=True)
            typer.echo(_render_doctor_pretty(payload))
    except typer.Exit:
        raise
    except Exception as exc:  # exit-code safety net, never exit 1 (mirrors init.py)
        emit_error(output, f"failed to render doctor output: {exc}")
        raise typer.Exit(code=3) from None

    raise typer.Exit(code=0)


markers_app.command(
    name="snippet",
    context_settings={"help_option_names": ["--help", "-h"]},
)(snippet)

markers_app.command(
    name="doctor",
    context_settings={"help_option_names": ["--help", "-h"]},
)(doctor)
