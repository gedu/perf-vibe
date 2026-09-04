"""Contract test for
`contracts/reassure_compare_v1.build_reassure_compare_payload` (SKILL rule
8: "A contract test MUST fail on any `--json` shape change without a
`schema_version` bump."). Mirrors `test_compare_v1_contract.py`'s
per-verdict key set and `test_reassure_show_v1_contract.py`'s D5
exact-key/negative discipline.

`schema_version=1`. Top-level FLAT dict with exactly EIGHT keys:
`schema_version`, `name`, `latest_import_id`, `baseline_import_n`
(IMPORTS, never commits — `ReassureComparison`'s honest name, design "The
Verdict Function"), `verdicts` (a list, FIXED order — `duration_ms` then
`render_count`, never re-sorted), and the THREE flat D5 keys:
`initial_update_count`, `baseline_initial_update_count` (both `int | None`
— `None` means "never measured", `0` means "measured, clean"; the two
facts must never collapse), and `initial_update_state`.

There is deliberately NO `*_delta_pct`/`*_pct` key anywhere for the update
count (D5 is a state transition, never a delta — design A13); the negative
is asserted below alongside the `is None` (never a falsy) NULL check.

Each verdict entry mirrors `compare_v1`'s own per-verdict shape exactly
(`metric`, `unit`, `direction`, `latest_value`, `baseline_value`,
`delta_pct`, `threshold_pct`, `floor`, `status`, `sample_n`,
`baseline_commit_n`) — this module owns its OWN mapping rather than
importing `compare_v1`'s private `_verdict_payload`, matching
`budget_check_v1`'s precedent of each contract module owning its exact key
set independently (spec "Five new `_v1` contracts... own contract test
with exact key set/count")."""

from __future__ import annotations

import json

from perf.contracts.reassure_compare_v1 import SCHEMA_VERSION, build_reassure_compare_payload
from perf.domain.model import HistoryMetric, ReassureEntryRow, ReassureSeriesPoint, Verdict
from perf.domain.reassure_compare import (
    SERIES_DURATION,
    SERIES_RENDER_COUNT,
    ReassureComparison,
    UpdateCountChange,
)

_NAME = "WidgetPanel renders correctly"

_TOP_LEVEL_KEYS_AND_TYPES = {
    "schema_version": int,
    "name": str,
    "latest_import_id": int,
    "baseline_import_n": int,
    "verdicts": list,
    "initial_update_count": (int, type(None)),
    "baseline_initial_update_count": (int, type(None)),
    "initial_update_state": str,
}

_VERDICT_KEYS_AND_TYPES = {
    "metric": str,
    "unit": str,
    "direction": str,
    "latest_value": (float, int, type(None)),
    "baseline_value": (float, int, type(None)),
    "delta_pct": (float, int),
    "threshold_pct": (float, int),
    "floor": (float, int),
    "status": str,
    "sample_n": int,
}


def _verdict(**overrides: object) -> Verdict:
    defaults: dict[str, object] = {
        "metric_name": SERIES_DURATION,
        "delta_pct": 20.0,
        "threshold_pct": 5.0,
        "status": "regression",
        "latest_value": 120.0,
        "baseline_value": 100.0,
        "unit": "ms",
        "sample_n": 6,
        "baseline_commit_n": 5,
        "series": (100.0, 102.0, 98.0, 120.0),
        "floor": 5.0,
        "higher_is_better": False,
    }
    defaults.update(overrides)
    return Verdict(**defaults)


def _latest_point(**overrides: object) -> ReassureSeriesPoint:
    defaults: dict[str, object] = {
        "import_id": 7,
        "ordered_at": "2026-01-04",
        "ordering_key": "created_date",
        "entry": ReassureEntryRow(
            entry_id=7,
            name=_NAME,
            entry_type="render",
            runs=8,
            duration=HistoryMetric(metric_name="duration_ms", p50=118.0, p90=120.0, n=6, unit="ms"),
            count=HistoryMetric(metric_name="render_count", p50=2.0, p90=2.0, n=8, unit="count"),
            initial_update_count=1,
        ),
        "commit_hash": "abc1234",
        "branch": "main",
    }
    defaults.update(overrides)
    return ReassureSeriesPoint(**defaults)


def _comparison(**overrides: object) -> ReassureComparison:
    defaults: dict[str, object] = {
        "name": _NAME,
        "latest": _latest_point(),
        "baseline_import_n": 5,
        "verdicts": (
            _verdict(metric_name=SERIES_DURATION, unit="ms"),
            _verdict(metric_name=SERIES_RENDER_COUNT, unit="count", status="stable"),
        ),
        "update_count": UpdateCountChange(baseline=0, latest=1, state="introduced"),
    }
    defaults.update(overrides)
    return ReassureComparison(**defaults)


def _payload(**overrides: object) -> dict:
    return build_reassure_compare_payload(comparison=_comparison(**overrides))


def test_schema_version_is_1():
    assert SCHEMA_VERSION == 1
    assert _payload()["schema_version"] == 1


def test_top_level_keys_present_with_correct_types():
    payload = _payload()
    for key, expected_type in _TOP_LEVEL_KEYS_AND_TYPES.items():
        assert key in payload, f"missing reassure_compare_v1 key: {key!r}"
        assert isinstance(payload[key], expected_type)


def test_exact_eight_top_level_keys_no_more_no_fewer():
    payload = _payload()
    assert set(payload.keys()) == set(_TOP_LEVEL_KEYS_AND_TYPES)
    assert len(payload) == 8


def test_name_matches_the_comparison():
    payload = _payload()
    assert payload["name"] == _NAME


def test_latest_import_id_matches_the_latest_point():
    payload = _payload(latest=_latest_point(import_id=42))
    assert payload["latest_import_id"] == 42


def test_baseline_import_n_matches_the_comparison_never_commits():
    payload = _payload(baseline_import_n=9)
    assert payload["baseline_import_n"] == 9


def test_verdicts_are_in_fixed_order_duration_then_render_count():
    payload = _payload()
    assert [v["metric"] for v in payload["verdicts"]] == [SERIES_DURATION, SERIES_RENDER_COUNT]


def test_verdict_keys_present_with_correct_types():
    payload = _payload()
    for verdict_payload in payload["verdicts"]:
        for key, expected_type in _VERDICT_KEYS_AND_TYPES.items():
            assert key in verdict_payload, f"missing verdict key: {key!r}"
            assert isinstance(verdict_payload[key], expected_type)


def test_exact_verdict_key_set():
    payload = _payload()
    for verdict_payload in payload["verdicts"]:
        assert set(verdict_payload.keys()) == set(_VERDICT_KEYS_AND_TYPES)


def test_baseline_commit_n_is_never_on_the_wire():
    """W-2 (re-verification finding): `baseline_commit_n` duplicated the
    top-level `baseline_import_n` under the one name D2 exists to keep out
    of reassure (`ReassureComparison`'s honest name for the same count) —
    mechanically derivable from a field already in the same payload, which
    the "no second source of truth" requirement forbids. Reassure-only
    correction: `compare_v1`/`budget_check_v1`'s OWN `baseline_commit_n`
    (the flow world, where the count really is commits) is untouched."""
    payload = _payload()
    for verdict_payload in payload["verdicts"]:
        assert "baseline_commit_n" not in verdict_payload


# ===== D5: three flat keys, NULL survives, no *_delta_pct anywhere =====


def test_initial_update_state_matches_the_comparison():
    payload = _payload(update_count=UpdateCountChange(baseline=1, latest=2, state="changed"))
    assert payload["initial_update_state"] == "changed"
    assert payload["initial_update_count"] == 2
    assert payload["baseline_initial_update_count"] == 1


def test_never_measured_survives_as_null_never_falsy_zero():
    """[unmissable] `baseline=None, latest=None` MUST serialize as `None`
    on both D5 keys, asserted with `is None` — a falsy check would also
    pass for a real, measured `0` and silently hide the never-measured
    case."""
    payload = _payload(update_count=UpdateCountChange(baseline=None, latest=None, state="unknown"))
    assert payload["initial_update_count"] is None
    assert payload["baseline_initial_update_count"] is None
    assert payload["initial_update_state"] == "unknown"


def test_no_delta_pct_or_pct_key_exists_for_the_update_count():
    """[unmissable] D5 negative (design A13): a state transition is never a
    delta — no top-level key matching `*_delta_pct`/`*_pct` for the update
    count may ever exist, however this payload evolves."""
    payload = _payload()
    for key in payload:
        if key == "verdicts":
            continue  # per-metric `delta_pct` is a real, separate concept
        assert "delta_pct" not in key
        assert not key.endswith("_pct")


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
