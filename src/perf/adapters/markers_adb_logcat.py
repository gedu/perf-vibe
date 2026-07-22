"""`MarkerSource` port adapter — `adb logcat -s ReactNativeJS:V` (design §4).

Contributes the logcat capture spec (pure `capture_spec()`) and parses the
buffer the driver returns (pure `parse()` — no I/O of its own; the driver
already captured the lines).

Parses BOTH forms into the same run-level `Marker(name, value, unit)`
shape: text `[PERF] <name>: <n>ms` and JSON `[PERF] {"name":...,
"value":...}`. Metric names are ARBITRARY — nothing here hardcodes a route
or metric name. JSON payloads are parsed with `json.loads` ONLY — NEVER
`eval`/`exec` (SKILL rule 5) — and malformed/oversized lines are skipped,
never raised.

`markStart`-without-`markEnd`: the completed marker line (`[PERF]
<name>: <n>ms` / JSON) is only ever emitted once a matching `markEnd`
actually fires. A bare `[PERF] markStart:<name>` line (started but never
completed, e.g. a crash mid-flow) is explicitly recognized and skipped —
it never produces a bogus/garbage `Marker`. Coverage is then judged by
comparing the count of COMPLETED marker occurrences against
`run.iterations`; fewer completed occurrences than iterations surfaces
`MarkerParseResult.partial_coverage=True`.

`[PERF-META]` lines are context only (consumed by `RunContextProvider`,
NOT markers) — this parser never emits a marker for one.

Fix (resilience review): on a host with 2+ connected devices, an unpinned
`adb logcat` dies with "more than one device" and the run silently yields
zero markers — indistinguishable from "the flow emitted none". `device`
mirrors the same pinning `MaestroDriver`/`BashRunContextProvider` already
apply.
"""

from __future__ import annotations

import json
import re
from typing import Optional, Sequence

from perf.domain.model import CaptureSpec, Marker, MarkerParseResult

_PERF_TAG = "[PERF]"
_PERF_META_TAG = "[PERF-META]"

# Bound line length before any regex/JSON parsing touches it (SKILL rule 5:
# "skip malformed lines and bound line length").
_MAX_LINE_LENGTH = 4096

_MARK_START_RE = re.compile(r"^markStart\b", re.IGNORECASE)

# `<name>: <n><unit?>` — name may be any arbitrary token (no metric name or
# app-domain route hardcoded); value MUST be numeric, so a non-numeric
# payload (e.g. a stray markStart line) simply fails to match and is
# skipped rather than crashing.
_TEXT_MARKER_RE = re.compile(
    r"^(?P<name>[^:]+):\s*(?P<value>\d+(?:\.\d+)?)(?P<unit>[a-zA-Z]*)\s*$"
)


class AdbLogcatMarkerSource:
    """`MarkerSource` (`domain/ports.py`) implementation."""

    def __init__(self, device: Optional[str] = None) -> None:
        self._device = device

    def capture_spec(self) -> Optional[CaptureSpec]:
        argv = ["adb"]
        if self._device is not None:
            argv += ["-s", self._device]
        argv += ["logcat", "-s", "ReactNativeJS:V"]
        return CaptureSpec(argv=argv)

    def parse(self, lines: Sequence[str], *, iterations: int) -> MarkerParseResult:
        markers: list[Marker] = []

        for raw_line in lines:
            if len(raw_line) > _MAX_LINE_LENGTH:
                continue  # bound line length — never regex/JSON-parse an oversized line

            line = raw_line.strip()
            if _PERF_META_TAG in line:
                continue  # context only — RunContextProvider's concern, not markers

            tag_index = line.find(_PERF_TAG)
            if tag_index == -1:
                continue

            payload = line[tag_index + len(_PERF_TAG):].strip()
            if not payload:
                continue

            if payload.startswith("{"):
                marker = self._parse_json_payload(payload)
            elif _MARK_START_RE.match(payload):
                # markStart with no matching markEnd — explicitly
                # recognized and skipped (design §4 / spec guard).
                marker = None
            else:
                marker = self._parse_text_payload(payload)

            if marker is not None:
                markers.append(marker)

        partial_coverage = len(markers) < iterations
        return MarkerParseResult(markers=tuple(markers), partial_coverage=partial_coverage)

    @staticmethod
    def _parse_text_payload(payload: str) -> Optional[Marker]:
        match = _TEXT_MARKER_RE.match(payload)
        if match is None:
            return None  # malformed — skip, never raise
        name = match.group("name").strip()
        value = float(match.group("value"))
        unit = match.group("unit") or "ms"
        return Marker(name=name, value=value, unit=unit)

    @staticmethod
    def _parse_json_payload(payload: str) -> Optional[Marker]:
        try:
            data = json.loads(payload)  # json.loads ONLY — never eval/exec (SKILL rule 5)
        except (json.JSONDecodeError, ValueError):
            return None  # malformed JSON — skip, never raise

        if not isinstance(data, dict):
            return None

        name = data.get("name")
        value = data.get("value")
        if name is None or value is None:
            return None
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None

        unit = data.get("unit") or "ms"
        return Marker(name=str(name), value=value, unit=str(unit))
