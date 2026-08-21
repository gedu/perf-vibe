"""`ReassureParser` port adapter — parses `@callstack/reassure` `.perf`
JSON-Lines files (design "Adapter — adapters/reassure_jsonl.py").

LOAD-BEARING: `durations[]` and `counts[]` are two INDEPENDENTLY-indexed raw
series and are NEVER zipped, paired, truncated, or padded here — `durations`
is built from reassure's outlier-FILTERED set, `counts` from the UNFILTERED
post-warmup set, and `removeOutliers` defaults to `true`, so
`len(durations) <= len(counts)` and index `i` of one does NOT describe the
same run as index `i` of the other (design "Load-Bearing Invariant").

Tolerant per line, strict per file: a malformed line is skipped and
reported in `ReassureParseResult.skipped`, never fatal. Tolerance goes one
step further for the `issues` DIAGNOSTICS, which degrade per FIELD and never
discard the entry at all (see `_parse_issues`). An unreadable file
(missing, unreadable, not UTF-8) raises `ReassureParseError`, mapped by the
CLI to exit 2 in a later slice. This adapter prints nothing — it only
returns data; the CLI renders warnings from `skipped`.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from perf.domain.model import ReassureEntry, ReassureHeader, ReassureParseResult

# Bound line length before any JSON parsing touches it (SKILL rule 5: "skip
# malformed lines and bound line length"). `.perf` entry lines can carry
# long `durations`/`counts` arrays, so this is generous relative to
# `markers_adb_logcat._MAX_LINE_LENGTH` (4096, plain text markers).
_MAX_LINE_BYTES = 1_048_576  # 1 MiB

_VALID_ENTRY_TYPES = frozenset({"render", "function", "async function"})

# Distinguishes a MISSING key from a present-but-falsy one. `None` cannot
# serve as this sentinel because `null` is a legal JSON value that must be
# treated as present-and-invalid, not as absent.
_ABSENT = object()

# Reason vocabulary — mirrors `REASON_*` in `markers_adb_logcat.py`. Defined
# ONCE here so nothing downstream (a future `reassure-import --json` stderr
# warning) re-derives or duplicates them.
REASON_INVALID_JSON = "invalid_json"
REASON_NOT_OBJECT = "not_object"
REASON_MISSING_FIELD = "missing_field"
REASON_UNKNOWN_TYPE = "unknown_type"
REASON_INVALID_VALUE = "invalid_value"
REASON_OVERSIZED = "oversized"


def _is_finite_number(value: object) -> bool:
    """`json.loads` ACCEPTS `NaN`/`Infinity` literals, and a `.perf` line
    can carry junk. Mirrors `sampler_flashlight._is_finite_number` (adapters
    never import one another — a 3-line predicate does not earn a shared
    home, per design). `bool` is excluded — `True` is an `int` subclass, not
    a measurement."""

    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class ReassureParseError(RuntimeError):
    """The file could not be read at all (missing, unreadable, or not
    UTF-8) — mapped by the CLI to exit 2. A per-LINE problem never raises:
    it is skipped and reported in `ReassureParseResult.skipped`."""


class ReassureJsonlParser:
    """`ReassureParser` (`domain/ports.py`) implementation. Registers
    directly as its own factory (`adapters/registry.py`) and never inherits
    `ReassureParser` — structural typing only."""

    def __init__(self, *, max_line_bytes: int = _MAX_LINE_BYTES) -> None:
        self._max_line_bytes = max_line_bytes

    def parse(self, path: str | Path) -> ReassureParseResult:
        try:
            raw = Path(path).read_bytes()
        except OSError as exc:
            raise ReassureParseError(f"could not read reassure file {path!r}: {exc}") from exc

        # sha256 over the EXACT raw bytes, before any decode/normalization —
        # the whole idempotency key (design "Content-Hash Idempotency").
        content_hash = hashlib.sha256(raw).hexdigest()

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReassureParseError(f"reassure file {path!r} is not valid UTF-8: {exc}") from exc

        header: ReassureHeader | None = None
        entries: list[ReassureEntry] = []
        skipped: list[tuple[int, str]] = []

        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue  # blank/whitespace-only lines are not data, never "skipped"

            if len(line.encode("utf-8")) > self._max_line_bytes:
                # Skipped WITHOUT being handed to `json.loads` at all
                # (SKILL rule 5: bound line length before parsing).
                skipped.append((line_number, REASON_OVERSIZED))
                continue

            try:
                data = json.loads(line)  # json.loads ONLY — never eval/exec
            except (json.JSONDecodeError, ValueError):
                skipped.append((line_number, REASON_INVALID_JSON))
                continue

            if not isinstance(data, dict):
                skipped.append((line_number, REASON_NOT_OBJECT))
                continue

            if line_number == 1 and header is None and "metadata" in data and "name" not in data:
                # Line 1 only: an object carrying `metadata` and no `name`
                # is the header — never counted as an entry. Any other
                # line-1 object is treated as an entry.
                header = _parse_header(data)
                continue

            entry, reason = _parse_entry(data)
            if entry is None:
                skipped.append((line_number, reason or REASON_MISSING_FIELD))
                continue
            entries.append(entry)

        partial_coverage = bool(skipped)
        diagnostic = _build_diagnostic(skipped, entries) if partial_coverage else None

        return ReassureParseResult(
            header=header,
            entries=tuple(entries),
            content_hash=content_hash,
            skipped=tuple(skipped),
            partial_coverage=partial_coverage,
            diagnostic=diagnostic,
        )


def _parse_header(data: dict[str, object]) -> ReassureHeader:
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        return ReassureHeader()

    branch = metadata.get("branch")
    commit_hash = metadata.get("commitHash")
    created_date = metadata.get("creationDate")
    return ReassureHeader(
        branch=branch if isinstance(branch, str) else None,
        commit_hash=commit_hash if isinstance(commit_hash, str) else None,
        created_date=created_date if isinstance(created_date, str) else None,
    )


def _parse_entry(data: dict[str, object]) -> tuple[ReassureEntry | None, str | None]:
    """Validates `name`, `runs`, `durations`, and `counts` INDEPENDENTLY — no
    length relationship is ever asserted between the last two (design
    "Entry validation"), and `runs` is never reconciled against either of
    them. `durations: []` is legitimate (invariant 6) and is never a skip
    reason; only type/shape problems and missing required fields are."""

    name = data.get("name")
    if not isinstance(name, str) or not name:
        return None, REASON_MISSING_FIELD

    durations_raw = data.get("durations")
    if not isinstance(durations_raw, list):
        return None, REASON_MISSING_FIELD
    counts_raw = data.get("counts")
    if not isinstance(counts_raw, list):
        return None, REASON_MISSING_FIELD

    if not all(_is_finite_number(v) for v in durations_raw):
        return None, REASON_INVALID_VALUE
    if not all(_is_finite_number(v) for v in counts_raw):
        return None, REASON_INVALID_VALUE

    # ONLY a MISSING key defaults to 'render'. An earlier revision wrote
    # `data.get("type") or "render"`, and `or` swallows every falsy PRESENT
    # value: `""`, `0`, `false` and `null` were each silently coerced to
    # 'render' and ACCEPTED, while only truthy non-members (`"mount"`) were
    # skipped. The spec requires a skip whenever `type` is present but not
    # one of the three, so presence is tested with a sentinel, never with
    # truthiness. Reassure's own zod enum rejects all four upstream, so this
    # is unreachable from real output — but an unspecified branch that
    # accepts junk is exactly how a plausible-looking line goes wrong.
    #
    # The `isinstance` guard is load-bearing, not defensive noise: a JSON
    # `type` of `[]` or `{}` is UNHASHABLE, so testing membership against a
    # frozenset raised `TypeError` and crashed the whole parse. A malformed
    # line MUST be skipped, never fatal to the import (spec
    # "Malformed-Line Tolerance"), so the type check has to precede the
    # membership check.
    entry_type = data.get("type", _ABSENT)
    if entry_type is _ABSENT:
        entry_type = "render"
    if not isinstance(entry_type, str) or entry_type not in _VALID_ENTRY_TYPES:
        return None, REASON_UNKNOWN_TYPE

    # `runs` is REQUIRED, and its absence is never filled in. Storing the
    # declared cardinality is the only reason a truncated or hand-edited
    # `.perf` can announce itself — `runs: 10` alongside 3 counts is
    # detectable precisely because the two numbers are independent. Deriving
    # an absent `runs` from `len(counts)` would make declared == actual BY
    # CONSTRUCTION for that entry, silently destroying the signal the column
    # exists to carry. Reassure's own schema
    # (`packages/compare/src/type-schemas.ts`) types `runs` as required, so
    # its absence means the line is malformed, not that we may invent it.
    runs_raw = data.get("runs")
    if runs_raw is None:
        return None, REASON_MISSING_FIELD
    if isinstance(runs_raw, bool) or not isinstance(runs_raw, int):
        return None, REASON_INVALID_VALUE
    runs = runs_raw

    initial_update_count, redundant_updates_json = _parse_issues(data)

    return (
        ReassureEntry(
            name=name,
            entry_type=str(entry_type),
            runs=runs,
            durations=tuple(float(v) for v in durations_raw),
            counts=tuple(float(v) for v in counts_raw),
            warmup_durations_json=_passthrough_json(data, "warmupDurations"),
            outlier_durations_json=_passthrough_json(data, "outlierDurations"),
            initial_update_count=initial_update_count,
            redundant_updates_json=redundant_updates_json,
        ),
        None,
    )


def _parse_issues(data: dict[str, object]) -> tuple[int | None, str | None]:
    """Parses reassure's `issues` diagnostics into
    `(initial_update_count, redundant_updates_json)`.

    DECISION — a malformed `issues` is NEVER fatal and NEVER skips the entry.
    `issues` is DIAGNOSTIC, not identity and not measurement: the
    durations/counts series are the data, and discarding a real measurement
    because its optional diagnostic block was junk would lose more than it
    protects. So every failure mode here degrades the OFFENDING FIELD to
    `None` and keeps the entry — `issues` not an object, `initialUpdateCount`
    not an integer, or `redundantUpdates` not an array. Degradation is
    per-FIELD, so one bad subkey never takes its well-formed sibling with it.
    This is deliberately unlike `name`/`runs`/`durations`/`counts`, whose
    absence or wrong type DOES skip the line.

    `None` means the key was ABSENT, which stays distinct from a present `0`
    and from a present `"[]"` (see `ReassureEntry`'s docstring and
    `0007_add_reassure_entry_issues.sql`).
    """

    issues = data.get("issues")
    if not isinstance(issues, dict):
        # Absent, `null`, or the wrong shape entirely — indistinguishable at
        # rest (all three mean "no usable diagnostics"), and none of them is
        # a reason to drop the measurement.
        return None, None

    raw_count = issues.get("initialUpdateCount")
    # `bool` is an `int` subclass in Python but is not a count — the same
    # exclusion `_is_finite_number` makes for measurements.
    initial_update_count = (
        raw_count if isinstance(raw_count, int) and not isinstance(raw_count, bool) else None
    )

    # An ARRAY, verbatim: reassure's zod schema types `redundantUpdates` as
    # `number[]` (every observed real value was `[]`), so anything that is
    # not a list is degraded rather than stored as a misleading scalar.
    redundant_updates_json = (
        _passthrough_json(issues, "redundantUpdates")
        if isinstance(issues.get("redundantUpdates"), list)
        else None
    )

    return initial_update_count, redundant_updates_json


def _passthrough_json(data: dict[str, object], key: str) -> str | None:
    """`None` when the JSON key is ABSENT, `json.dumps(value)` when present
    — so `[]` round-trips as `"[]"`, preserving the absent-vs-empty
    distinction (design invariant 4). Never validated beyond "is present"."""

    if key not in data:
        return None
    return json.dumps(data[key])


def _build_diagnostic(skipped: list[tuple[int, str]], entries: list[ReassureEntry]) -> str | None:
    """A short, actionable explanation for a partial/zero-coverage import —
    mirrors `AdbLogcatMarkerSource._build_diagnostic`. `None` on a clean
    full-coverage parse (never reached here since only called when
    `skipped` is non-empty)."""

    if not entries:
        return f"all {len(skipped)} line(s) were skipped as malformed — zero entries recovered."
    return f"{len(skipped)} line(s) skipped as malformed; {len(entries)} entries imported."
