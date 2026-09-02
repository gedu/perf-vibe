"""Contract test for
`contracts/reassure_entries_v1.build_reassure_entries_payload` (SKILL rule
8: "A contract test MUST fail on any `--json` shape change without a
`schema_version` bump."). Pins the exact key set at BOTH levels —
top-level (`schema_version`, `import_id`, `entries`) and per-entry (5 keys:
`name`, `entry_type`, `runs`, `duration`, `count`) — mirroring
`test_reassure_list_v1_contract.py`'s exact-set discipline. `duration`/
`count` are each EITHER `null` (that series had zero stored samples — I1:
never a zero-valued metric standing in for "no series") OR a nested
`{p50, p90, n, unit}` dict, mirroring `history_v1._metric_payload`'s shape.

Deliberately absent, and asserted absent, from each per-entry dict:
- `entry_id` — the `reassure_entry` table's internal primary key. It has
  no meaning to a consumer: `name` is the identifier every other reassure
  command (`show`/`history`/`compare`) already looks entries up by, and
  nothing in this capability ever re-derives an entry FROM an id.
- `initial_update_count` — `ReassureEntryRow` carries it, but spec
  "reassure entries <import-id>" names only `name`/`entry_type`/`runs`/
  each series' `n`; the D5 state-transition rendering of
  `initial_update_count` is `reassure show`'s own requirement (spec
  "reassure show <name>", D5), a later slice. Carrying it here ahead of
  that slice's actual consumer would be exposing a fact this command makes
  no use of.
- `import_id` PER ENTRY — already the top-level payload key; repeating it
  on every entry would be a pure derived redundancy the no-second-source-
  of-truth rule already refuses elsewhere in this capability
  (`reassure_list_v1`'s `ordering_key`/`ordered_at`).

`runs` is the file-DECLARED count and is NEVER reconciled against
`duration.n`/`count.n` — a mismatch is a real, surfaceable fact (a
truncated `.perf` file), not something this builder repairs or hides
behind a single "corrected" number. There is deliberately no
`declared_actual_mismatch`/`runs_match` field: a consumer already holding
`runs`, `duration.n` and `count.n` can compare them itself in one line,
which is exactly the no-second-source-of-truth rule again.
"""

from __future__ import annotations

import json

from perf.contracts.reassure_entries_v1 import SCHEMA_VERSION, build_reassure_entries_payload
from perf.domain.model import HistoryMetric, ReassureEntryRow

_TOP_LEVEL_KEYS_AND_TYPES = {
    "schema_version": int,
    "import_id": int,
    "entries": list,
}

_ENTRY_KEYS_AND_TYPES = {
    "name": str,
    "entry_type": str,
    "runs": int,
    "duration": (dict, type(None)),
    "count": (dict, type(None)),
}

_METRIC_KEYS_AND_TYPES = {
    "p50": (float, int, type(None)),
    "p90": (float, int, type(None)),
    "n": int,
    "unit": str,
}


def _metric(**overrides: object) -> HistoryMetric:
    defaults: dict[str, object] = {
        "metric_name": "duration_ms",
        "p50": 10.2,
        "p90": 10.6,
        "n": 6,
        "unit": "ms",
    }
    defaults.update(overrides)
    return HistoryMetric(**defaults)


def _row(**overrides: object) -> ReassureEntryRow:
    defaults: dict[str, object] = {
        "entry_id": 1,
        "name": "WidgetPanel renders correctly",
        "entry_type": "render",
        "runs": 8,
        "duration": _metric(metric_name="duration_ms", p50=10.2, p90=10.6, n=6, unit="ms"),
        "count": _metric(metric_name="render_count", p50=1.0, p90=2.0, n=8, unit="count"),
        "initial_update_count": None,
    }
    defaults.update(overrides)
    return ReassureEntryRow(**defaults)


def _sample_payload(*rows: ReassureEntryRow, import_id: int = 7) -> dict:
    return build_reassure_entries_payload(import_id=import_id, entries=rows or (_row(),))


def test_schema_version_is_1():
    assert SCHEMA_VERSION == 1
    payload = _sample_payload()
    assert payload["schema_version"] == 1


def test_top_level_keys_present_with_correct_types():
    payload = _sample_payload()
    for key, expected_type in _TOP_LEVEL_KEYS_AND_TYPES.items():
        assert key in payload, f"missing top-level reassure_entries_v1 key: {key!r}"
        assert isinstance(payload[key], expected_type)


def test_exact_three_top_level_keys_no_more_no_fewer():
    payload = _sample_payload()
    assert set(payload.keys()) == set(_TOP_LEVEL_KEYS_AND_TYPES)
    assert len(payload) == 3


def test_import_id_matches_the_caller_argument():
    payload = _sample_payload(import_id=42)
    assert payload["import_id"] == 42


def test_per_entry_keys_present_with_correct_types():
    payload = _sample_payload()
    entry = payload["entries"][0]
    for key, expected_type in _ENTRY_KEYS_AND_TYPES.items():
        assert key in entry, f"missing reassure_entries_v1 entry key: {key!r}"
        assert isinstance(entry[key], expected_type)


def test_exact_five_entry_keys_no_more_no_fewer():
    payload = _sample_payload()
    entry = payload["entries"][0]
    assert set(entry.keys()) == set(_ENTRY_KEYS_AND_TYPES)
    assert len(entry) == 5


def test_duration_and_count_are_nested_metric_dicts_with_exact_keys():
    payload = _sample_payload()
    entry = payload["entries"][0]
    for series in ("duration", "count"):
        metric = entry[series]
        assert set(metric.keys()) == set(_METRIC_KEYS_AND_TYPES)
        for key, expected_type in _METRIC_KEYS_AND_TYPES.items():
            assert isinstance(metric[key], expected_type)


def test_duration_and_count_are_independent_never_forced_equal():
    """The 5-count/8-duration case the spec names explicitly (`spec.md`
    "invariant #3"): neither series' `n` is derived from, or forced to
    match, the other."""
    row = _row(
        duration=_metric(metric_name="duration_ms", n=8, unit="ms"),
        count=_metric(metric_name="render_count", n=5, unit="count"),
    )
    payload = _sample_payload(row)
    entry = payload["entries"][0]
    assert entry["duration"]["n"] == 8
    assert entry["count"]["n"] == 5


def test_absent_duration_series_is_null_never_a_zero_valued_metric():
    row = _row(duration=None)
    payload = _sample_payload(row)
    assert payload["entries"][0]["duration"] is None


def test_absent_count_series_is_null_never_a_zero_valued_metric():
    row = _row(count=None)
    payload = _sample_payload(row)
    assert payload["entries"][0]["count"] is None


def test_runs_is_the_declared_value_never_reconciled_against_series_n():
    """`runs` (declared) and `duration.n`/`count.n` (actual) may legitimately
    disagree — a truncated `.perf` file — and this builder must report both
    verbatim, never a silently "corrected" value."""
    row = _row(
        runs=8,
        duration=_metric(metric_name="duration_ms", n=6, unit="ms"),
        count=_metric(metric_name="render_count", n=8, unit="count"),
    )
    payload = _sample_payload(row)
    entry = payload["entries"][0]
    assert entry["runs"] == 8
    assert entry["duration"]["n"] == 6
    assert entry["count"]["n"] == 8


def test_empty_entries_list_is_a_valid_payload():
    payload = build_reassure_entries_payload(import_id=1, entries=())
    assert payload["entries"] == []


def test_no_entry_id_anywhere_in_the_payload():
    payload = _sample_payload()
    assert "entry_id" not in payload["entries"][0]


def test_no_initial_update_count_anywhere_in_the_payload():
    payload = _sample_payload()
    assert "initial_update_count" not in payload["entries"][0]


def test_no_import_id_on_a_per_entry_dict():
    payload = _sample_payload()
    assert "import_id" not in payload["entries"][0]


def test_no_declared_actual_mismatch_field_anywhere():
    payload = _sample_payload()
    entry = payload["entries"][0]
    assert "runs_match" not in entry
    assert "declared_actual_mismatch" not in entry


def test_multiple_entries_preserve_caller_order():
    first = _row(name="A")
    second = _row(name="B")
    payload = _sample_payload(first, second)
    assert [entry["name"] for entry in payload["entries"]] == ["A", "B"]


def test_payload_is_json_serializable_and_lossless():
    payload = _sample_payload()
    serialized = json.dumps(payload)
    assert json.loads(serialized) == payload


def test_contract_rejects_a_shape_change_without_version_bump():
    """Structural regression guard: any top-level key addition/removal/
    rename without a `SCHEMA_VERSION` bump fails this test (exact-set
    pinning, matching `test_reassure_list_v1_contract.py`'s pattern)."""
    payload = _sample_payload()
    assert set(payload.keys()) == set(_TOP_LEVEL_KEYS_AND_TYPES)
    assert payload["schema_version"] == 1, (
        "a shape change needs a SCHEMA_VERSION bump, not an inequality"
    )
