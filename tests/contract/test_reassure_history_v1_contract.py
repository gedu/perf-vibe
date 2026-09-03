"""Contract test for
`contracts/reassure_history_v1.build_reassure_history_payload` (SKILL rule 8:
"A contract test MUST fail on any `--json` shape change without a
`schema_version` bump."). Mirrors `test_history_v1_contract.py`'s
top-level-list shape (`{schema_version, ..., items: [...]}`) and
`test_reassure_show_v1_contract.py`'s exact-key-set discipline, applied at
BOTH levels here: the top-level dict AND every per-point dict inside
`points`.

`schema_version=1`. `{schema_version, name, points: [...]}` — ONE point per
IMPORT containing `name` (design D2: one import = one point, never a
per-commit collapse), OLDEST→NEWEST — the SAME direction
`store.reassure_series` already returns and `history_v1`'s `runs` already
uses.

Each point carries exactly SIX keys: `import_id`, `ordered_at`,
`ordering_key`, `commit_hash` (a LABEL only, invariant I2 — this contract
never groups or filters on it), and the two independently-reduced
`duration`/`count` series (invariant I1 — never one zipped pair). `branch`
is deliberately absent: nothing in this command's spec or its renderer ever
reads a point's branch (unlike `reassure_list_v1`, which reports the roster
and does carry it) — the explicit negative is asserted below.
"""

from __future__ import annotations

import json

from perf.contracts.reassure_history_v1 import SCHEMA_VERSION, build_reassure_history_payload
from perf.domain.model import HistoryMetric, ReassureEntryRow, ReassureSeriesPoint

_NAME = "WidgetPanel renders correctly"

_TOP_LEVEL_KEYS_AND_TYPES = {
    "schema_version": int,
    "name": str,
    "points": list,
}

_POINT_KEYS_AND_TYPES = {
    "import_id": int,
    "ordered_at": str,
    "ordering_key": str,
    "commit_hash": (str, type(None)),
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


def _entry(**overrides: object) -> ReassureEntryRow:
    defaults: dict[str, object] = {
        "entry_id": 1,
        "name": _NAME,
        "entry_type": "render",
        "runs": 8,
        "duration": _metric(metric_name="duration_ms", p50=10.2, p90=10.6, n=6, unit="ms"),
        "count": _metric(metric_name="render_count", p50=1.0, p90=2.0, n=8, unit="count"),
        "initial_update_count": None,
    }
    defaults.update(overrides)
    return ReassureEntryRow(**defaults)


def _point(**overrides: object) -> ReassureSeriesPoint:
    defaults: dict[str, object] = {
        "import_id": 7,
        "ordered_at": "2026-01-01",
        "ordering_key": "created_date",
        "entry": _entry(),
        "commit_hash": "abc1234",
        "branch": "main",
    }
    defaults.update(overrides)
    return ReassureSeriesPoint(**defaults)


def _payload(*, name: str = _NAME, points: list[ReassureSeriesPoint] | None = None) -> dict:
    return build_reassure_history_payload(
        name=name, points=points if points is not None else [_point()]
    )


def test_schema_version_is_1():
    assert SCHEMA_VERSION == 1
    assert _payload()["schema_version"] == 1


def test_top_level_keys_present_with_correct_types():
    payload = _payload()
    for key, expected_type in _TOP_LEVEL_KEYS_AND_TYPES.items():
        assert key in payload, f"missing reassure_history_v1 key: {key!r}"
        assert isinstance(payload[key], expected_type)


def test_exact_three_top_level_keys_no_more_no_fewer():
    payload = _payload()
    assert set(payload.keys()) == set(_TOP_LEVEL_KEYS_AND_TYPES)
    assert len(payload) == 3


def test_name_matches_the_caller_argument():
    payload = _payload(name="Custom")
    assert payload["name"] == "Custom"


def test_empty_points_is_an_empty_list_not_omitted():
    payload = _payload(points=[])
    assert payload["points"] == []


def test_point_keys_present_with_correct_types():
    point = _payload()["points"][0]
    for key, expected_type in _POINT_KEYS_AND_TYPES.items():
        assert key in point, f"missing reassure_history_v1 point key: {key!r}"
        assert isinstance(point[key], expected_type)


def test_exact_six_point_keys_no_more_no_fewer():
    point = _payload()["points"][0]
    assert set(point.keys()) == set(_POINT_KEYS_AND_TYPES)
    assert len(point) == 6


def test_branch_never_appears_in_any_point():
    """[unmissable] `reassure_list_v1` carries `branch`; this contract
    deliberately does not — nothing here groups, filters, or renders on
    it."""
    point = _payload()["points"][0]
    assert "branch" not in point


def test_duration_and_count_are_nested_metric_dicts_with_exact_keys():
    point = _payload()["points"][0]
    for series in ("duration", "count"):
        metric = point[series]
        assert set(metric.keys()) == set(_METRIC_KEYS_AND_TYPES)
        for key, expected_type in _METRIC_KEYS_AND_TYPES.items():
            assert isinstance(metric[key], expected_type)


def test_absent_duration_series_is_null_never_a_zero_valued_metric():
    point = _point(entry=_entry(duration=None))
    payload = _payload(points=[point])
    assert payload["points"][0]["duration"] is None


def test_absent_count_series_is_null_never_a_zero_valued_metric():
    point = _point(entry=_entry(count=None))
    payload = _payload(points=[point])
    assert payload["points"][0]["count"] is None


def test_commit_hash_is_null_when_absent_never_an_empty_string():
    point = _point(commit_hash=None)
    payload = _payload(points=[point])
    assert payload["points"][0]["commit_hash"] is None


def test_points_preserve_caller_order_never_resorted():
    """This builder never sorts — ordering (D2, OLDEST→NEWEST) is entirely
    `store.reassure_series`'s job."""
    older = _point(import_id=1, ordered_at="2026-01-01")
    newer = _point(import_id=2, ordered_at="2026-01-02")
    payload = _payload(points=[older, newer])
    assert [p["import_id"] for p in payload["points"]] == [1, 2]


def test_import_id_and_ordering_fields_match_the_point():
    point = _point(import_id=42, ordered_at="2026-03-03", ordering_key="imported_at")
    payload = _payload(points=[point])
    point_payload = payload["points"][0]
    assert point_payload["import_id"] == 42
    assert point_payload["ordered_at"] == "2026-03-03"
    assert point_payload["ordering_key"] == "imported_at"


def test_two_points_sharing_commit_and_branch_stay_two_distinct_points():
    """[unmissable] I2's own precedent (0006's real baseline/current pair):
    two points may legitimately share `commit_hash` AND `branch` and MUST
    still surface as two distinct entries in `points`, never collapsed."""
    first = _point(import_id=1, commit_hash="same", branch="main")
    second = _point(import_id=2, commit_hash="same", branch="main")
    payload = _payload(points=[first, second])
    assert len(payload["points"]) == 2
    assert [p["import_id"] for p in payload["points"]] == [1, 2]


def test_payload_is_json_serializable_and_lossless():
    payload = _payload()
    serialized = json.dumps(payload)
    assert json.loads(serialized) == payload


def test_contract_rejects_a_shape_change_without_version_bump():
    payload = _payload()
    assert set(payload.keys()) == set(_TOP_LEVEL_KEYS_AND_TYPES)
    assert payload["schema_version"] == 1, (
        "a shape change needs a SCHEMA_VERSION bump, not an inequality"
    )
