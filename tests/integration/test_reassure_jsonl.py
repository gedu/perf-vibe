"""Integration tests for `ReassureJsonlParser` (design "Adapter —
adapters/reassure_jsonl.py"). Fixture-driven (`tests/fixtures/
reassure_sample.perf`) plus `tmp_path`-generated files for edge cases; the
adapter IS the I/O edge, so no fake is needed.

Lives in `tests/integration/` because it reads real files, matching every
other file-reading adapter in this repo — `test_sampler_flashlight.py` and
`test_markers_adb_logcat.py` are both here. `tasks.md` named a
`tests/unit/` path; the house convention wins.

The load-bearing test in this module (`test_non_alignment_...`) is the
regression guard for the entire feature: `durations[]` and `counts[]` are
NOT index-aligned (design "Load-Bearing Invariant") and must never be
zipped, padded, or truncated to a common length.

Fixture shape verified against real `@callstack/reassure` `.perf` output
(reassure-ingest PR4a): entry names carry NO delimiter at all -- no `>`,
`|`, `-`, `::`, or `/` -- reassure writes `expect.getState().currentTestName`
untransformed, which is plain space-joined text shaped like
`<Component> Performance Tests <Component> <scenario>`. Every good entry
also carries the full real key set, including `issues`.

Observed types across 101 real entries: `issues.initialUpdateCount` is an
INTEGER (values 0 and 1 seen, non-zero on roughly one entry in eight) and
`issues.redundantUpdates` is an ARRAY -- `[]` in every real entry. One
fixture entry carries a POPULATED `redundantUpdates` deliberately, so a
consumer that treats it as a scalar fails here instead of on real data. An
earlier revision of this fixture wrote it as a bare number; that was
fabricated and would have taught the wrong type to whatever persists
`issues` later.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from perf.adapters.reassure_jsonl import (
    REASON_INVALID_JSON,
    REASON_INVALID_VALUE,
    REASON_MISSING_FIELD,
    REASON_OVERSIZED,
    REASON_UNKNOWN_TYPE,
    ReassureJsonlParser,
    ReassureParseError,
)
from perf.domain.model import ReassureEntry

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
_FIXTURE_PATH = _FIXTURES_DIR / "reassure_sample.perf"


def _entry_by_name(entries: Sequence[ReassureEntry], name: str) -> ReassureEntry:
    for entry in entries:
        if entry.name == name:
            return entry
    raise AssertionError(f"no entry named {name!r} in {[e.name for e in entries]}")


def test_non_alignment_load_bearing_counts_and_durations_have_true_lengths():
    """[load-bearing] An entry with 8 counts and 6 durations must parse into
    two sequences at their OWN true lengths — no padding, no truncation, no
    `None` filler, and the two series are never zipped together."""
    parser = ReassureJsonlParser()
    result = parser.parse(str(_FIXTURE_PATH))

    entry = _entry_by_name(
        result.entries, "WidgetPanel Performance Tests WidgetPanel renders correctly"
    )

    assert len(entry.counts) == 8
    assert len(entry.durations) == 6
    assert entry.counts == (1.0, 1.0, 1.0, 1.0, 1.0, 2.0, 1.0, 1.0)
    assert entry.durations == (10.1, 10.2, 10.3, 10.4, 10.5, 10.6)
    # `runs` is the DECLARED cardinality, kept even though it equals
    # `len(counts)` here — never derived from `durations`.
    assert entry.runs == 8
    assert entry.entry_type == "render"  # `type` absent -> default


def test_empty_durations_with_nonempty_counts_is_not_skipped():
    """Invariant 6: every post-warmup run classified an outlier -> an
    entry with `durations: []` is still persisted, never skipped."""
    parser = ReassureJsonlParser()
    result = parser.parse(str(_FIXTURE_PATH))

    entry = _entry_by_name(
        result.entries,
        "NotificationBanner Performance Tests NotificationBanner renders after dismiss",
    )

    assert entry.durations == ()
    assert entry.counts == (4.0, 5.0, 6.0)
    assert entry.runs == 3


def test_header_absent_parses_identically_to_no_header(tmp_path):
    path = tmp_path / "no_header.perf"
    path.write_text(
        json.dumps({"name": "solo entry", "runs": 1, "durations": [1.0], "counts": [1.0]}) + "\n"
    )

    result = ReassureJsonlParser().parse(str(path))

    assert result.header is None
    assert len(result.entries) == 1
    assert result.entries[0].name == "solo entry"


def test_header_present_with_field_subset_is_not_counted_as_entry():
    parser = ReassureJsonlParser()
    result = parser.parse(str(_FIXTURE_PATH))

    assert result.header is not None
    assert result.header.branch == "main"
    assert result.header.commit_hash == "abc123"
    assert result.header.created_date == "2026-01-01T00:00:00.000Z"
    # The header line is not present among the entries.
    assert all(entry.name != "" for entry in result.entries)
    assert "metadata" not in {entry.name for entry in result.entries}


def test_header_with_only_branch_leaves_other_fields_none(tmp_path):
    path = tmp_path / "partial_header.perf"
    lines = [
        json.dumps({"metadata": {"branch": "feature/x"}}),
        json.dumps({"name": "an entry", "runs": 1, "durations": [1.0], "counts": [1.0]}),
    ]
    path.write_text("\n".join(lines) + "\n")

    result = ReassureJsonlParser().parse(str(path))

    assert result.header is not None
    assert result.header.branch == "feature/x"
    assert result.header.commit_hash is None
    assert result.header.created_date is None
    assert len(result.entries) == 1


def test_type_defaults_to_render_when_absent(tmp_path):
    path = tmp_path / "no_type.perf"
    path.write_text(
        json.dumps({"name": "no type entry", "runs": 1, "durations": [1.0], "counts": [1.0]}) + "\n"
    )

    result = ReassureJsonlParser().parse(str(path))

    assert result.entries[0].entry_type == "render"


def test_malformed_lines_are_skipped_with_reason_and_never_fatal():
    """Bad JSON, a missing required field, or an unrecognized `type` must be
    skipped and reported — never fatal to the whole import."""
    parser = ReassureJsonlParser()
    result = parser.parse(str(_FIXTURE_PATH))

    reasons = {reason for _, reason in result.skipped}
    assert reasons == {
        REASON_INVALID_JSON,
        REASON_MISSING_FIELD,
        REASON_UNKNOWN_TYPE,
        REASON_INVALID_VALUE,
    }
    assert len(result.skipped) == 6
    assert result.partial_coverage is True
    # Good lines are still imported despite the bad ones interleaved.
    assert len(result.entries) == 3


def test_oversized_line_is_skipped_without_being_handed_to_json_loads(tmp_path):
    good_entry = json.dumps({"name": "ok", "runs": 1, "durations": [1.0], "counts": [1.0]})
    # Deliberately invalid JSON (unterminated object) AND oversized: if the
    # implementation checked size before attempting to parse, the reason
    # MUST be REASON_OVERSIZED, never REASON_INVALID_JSON.
    oversized_and_invalid = "{" + ("x" * 100)
    path = tmp_path / "oversized.perf"
    path.write_text(good_entry + "\n" + oversized_and_invalid + "\n")

    result = ReassureJsonlParser(max_line_bytes=len(good_entry.encode("utf-8"))).parse(str(path))

    assert len(result.entries) == 1
    assert result.skipped == ((2, REASON_OVERSIZED),)


def test_sha256_is_computed_over_exact_raw_bytes(tmp_path):
    path = tmp_path / "sample.perf"
    content = b'{"name": "a", "runs": 1, "durations": [1.0], "counts": [1.0]}\n'
    path.write_bytes(content)

    result = ReassureJsonlParser().parse(str(path))

    assert result.content_hash == hashlib.sha256(content).hexdigest()


def test_missing_path_raises_reassure_parse_error(tmp_path):
    parser = ReassureJsonlParser()
    missing = tmp_path / "does-not-exist.perf"

    with pytest.raises(ReassureParseError):
        parser.parse(str(missing))


def test_unreadable_path_raises_reassure_parse_error(tmp_path):
    parser = ReassureJsonlParser()
    directory = tmp_path / "a-directory"
    directory.mkdir()

    with pytest.raises(ReassureParseError):
        parser.parse(str(directory))


def test_outlier_durations_absent_is_none_present_empty_is_literal_empty_array():
    """Invariant 4: `outlierDurations` ABSENT (key not present, e.g.
    `removeOutliers` off) must round-trip as `None`, distinct from
    present-but-empty (`"[]"`)."""
    parser = ReassureJsonlParser()
    result = parser.parse(str(_FIXTURE_PATH))

    absent_entry = _entry_by_name(
        result.entries,
        "NotificationBanner Performance Tests NotificationBanner renders after dismiss",
    )
    present_empty_entry = _entry_by_name(
        result.entries, "SearchInput Performance Tests SearchInput renders with results"
    )

    assert absent_entry.outlier_durations_json is None
    assert present_empty_entry.outlier_durations_json == "[]"


def test_warmup_durations_passthrough_serializes_verbatim():
    parser = ReassureJsonlParser()
    result = parser.parse(str(_FIXTURE_PATH))

    entry = _entry_by_name(
        result.entries, "WidgetPanel Performance Tests WidgetPanel renders correctly"
    )

    assert entry.warmup_durations_json == json.dumps([5.0, 5.1])


def test_blank_lines_are_ignored_silently_never_counted_as_skipped(tmp_path):
    path = tmp_path / "blank_lines.perf"
    entry_line = json.dumps({"name": "solo entry", "runs": 1, "durations": [1.0], "counts": [1.0]})
    path.write_text(f"\n   \n{entry_line}\n\n")

    result = ReassureJsonlParser().parse(str(path))

    assert len(result.entries) == 1
    assert result.skipped == ()
    assert result.partial_coverage is False


def test_non_object_json_line_is_skipped_as_not_object(tmp_path):
    path = tmp_path / "not_object.perf"
    path.write_text("[1, 2, 3]\n")

    result = ReassureJsonlParser().parse(str(path))

    assert len(result.entries) == 0
    reasons = {reason for _, reason in result.skipped}
    assert "not_object" in reasons


def test_unreadable_utf8_raises_reassure_parse_error(tmp_path):
    path = tmp_path / "not_utf8.perf"
    path.write_bytes(b"\xff\xfe\x00invalid utf-8")

    with pytest.raises(ReassureParseError):
        ReassureJsonlParser().parse(str(path))


def test_header_with_non_dict_metadata_yields_empty_header(tmp_path):
    path = tmp_path / "bad_metadata.perf"
    lines = [
        json.dumps({"metadata": "not-a-dict"}),
        json.dumps({"name": "an entry", "runs": 1, "durations": [1.0], "counts": [1.0]}),
    ]
    path.write_text("\n".join(lines) + "\n")

    result = ReassureJsonlParser().parse(str(path))

    assert result.header is not None
    assert result.header.branch is None
    assert result.header.commit_hash is None
    assert result.header.created_date is None


def test_nan_in_counts_is_skipped_with_invalid_value(tmp_path):
    path = tmp_path / "nan_counts.perf"
    path.write_text('{"name": "bad counts", "durations": [1.0], "counts": [NaN]}\n')

    result = ReassureJsonlParser().parse(str(path))

    assert len(result.entries) == 0
    assert result.skipped == ((1, REASON_INVALID_VALUE),)


def test_non_integer_runs_is_skipped_with_invalid_value(tmp_path):
    path = tmp_path / "bad_runs.perf"
    path.write_text('{"name": "bad runs", "runs": "ten", "durations": [1.0], "counts": [1.0]}\n')

    result = ReassureJsonlParser().parse(str(path))

    assert len(result.entries) == 0
    assert result.skipped == ((1, REASON_INVALID_VALUE),)


def test_absent_runs_is_skipped_and_never_synthesised_from_counts(tmp_path):
    """`runs` is the DECLARED cardinality, and storing it is the only reason
    a truncated or hand-edited `.perf` can announce itself: a file claiming
    `runs: 10` while carrying 3 counts is detectable precisely because the
    two numbers are independent.

    Synthesising an absent `runs` from `len(counts)` would make
    declared == actual BY CONSTRUCTION for that entry, permanently and
    silently destroying the signal the column exists to carry. Reassure's own
    schema (`packages/compare/src/type-schemas.ts`) types `runs` as required,
    so its absence means the line is malformed — not that we may invent it.
    """
    path = tmp_path / "absent_runs.perf"
    path.write_text('{"name": "no runs", "durations": [1.0, 2.0], "counts": [3.0, 4.0, 5.0]}\n')

    result = ReassureJsonlParser().parse(str(path))

    assert len(result.entries) == 0, "an entry with no declared `runs` must not be kept"
    assert result.skipped == ((1, REASON_MISSING_FIELD),)
