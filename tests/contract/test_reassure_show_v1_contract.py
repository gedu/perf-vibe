"""Contract test for `contracts/reassure_show_v1.build_reassure_show_payload`
(SKILL rule 8: "A contract test MUST fail on any `--json` shape change
without a `schema_version` bump."). Pins the exact FLAT top-level key set —
TEN keys — mirroring `test_reassure_entries_v1_contract.py`'s exact-set
discipline.

The three D5 keys (design "D5 — State Transition in Both Views",
`design.md:385-393`): `initial_update_count: int|null`,
`baseline_initial_update_count: int|null`, `initial_update_state: str`.
D5 is a STATE TRANSITION, never a delta — this file carries the explicit
negative: no key matching `*_delta_pct`/`*_pct` exists anywhere in the
payload for the update count.

`duration`/`count` reuse `reassure_entries_v1._metric_payload`'s exact
nested shape (`{p50, p90, n, unit}` or `null`) — mirrors
`test_reassure_entries_v1_contract.py`'s pattern one level up, not nested
inside a list this time (`show` reports exactly ONE entry, not many).

`runs` is the file-DECLARED count and is NEVER reconciled against
`duration.n`/`count.n` here — same no-second-source-of-truth rule
`reassure_entries_v1` already established; a `declared runs N != stored n
M` line is the PRETTY view's job (task 2b.10), not this builder's."""

from __future__ import annotations

import json

from perf.contracts.reassure_show_v1 import SCHEMA_VERSION, build_reassure_show_payload
from perf.domain.model import HistoryMetric, ReassureEntryRow

_TOP_LEVEL_KEYS_AND_TYPES = {
    "schema_version": int,
    "import_id": int,
    "name": str,
    "entry_type": str,
    "runs": int,
    "duration": (dict, type(None)),
    "count": (dict, type(None)),
    "initial_update_count": (int, type(None)),
    "baseline_initial_update_count": (int, type(None)),
    "initial_update_state": str,
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
        "name": "WidgetPanel renders correctly",
        "entry_type": "render",
        "runs": 8,
        "duration": _metric(metric_name="duration_ms", p50=10.2, p90=10.6, n=6, unit="ms"),
        "count": _metric(metric_name="render_count", p50=1.0, p90=2.0, n=8, unit="count"),
        "initial_update_count": None,
    }
    defaults.update(overrides)
    return ReassureEntryRow(**defaults)


def _payload(
    *, import_id: int = 7, entry: ReassureEntryRow | None = None, baseline: int | None = None
) -> dict:
    return build_reassure_show_payload(
        import_id=import_id,
        entry=entry or _entry(),
        baseline_initial_update_count=baseline,
    )


def test_schema_version_is_1():
    assert SCHEMA_VERSION == 1
    payload = _payload()
    assert payload["schema_version"] == 1


def test_top_level_keys_present_with_correct_types():
    payload = _payload()
    for key, expected_type in _TOP_LEVEL_KEYS_AND_TYPES.items():
        assert key in payload, f"missing reassure_show_v1 key: {key!r}"
        assert isinstance(payload[key], expected_type)


def test_exact_ten_top_level_keys_no_more_no_fewer():
    payload = _payload()
    assert set(payload.keys()) == set(_TOP_LEVEL_KEYS_AND_TYPES)
    assert len(payload) == 10


def test_import_id_and_name_match_the_caller_arguments():
    payload = _payload(import_id=42, entry=_entry(name="Custom"))
    assert payload["import_id"] == 42
    assert payload["name"] == "Custom"


def test_duration_and_count_are_nested_metric_dicts_with_exact_keys():
    payload = _payload()
    for series in ("duration", "count"):
        metric = payload[series]
        assert set(metric.keys()) == set(_METRIC_KEYS_AND_TYPES)
        for key, expected_type in _METRIC_KEYS_AND_TYPES.items():
            assert isinstance(metric[key], expected_type)


def test_absent_duration_series_is_null_never_a_zero_valued_metric():
    payload = _payload(entry=_entry(duration=None))
    assert payload["duration"] is None


def test_absent_count_series_is_null_never_a_zero_valued_metric():
    payload = _payload(entry=_entry(count=None))
    assert payload["count"] is None


def test_runs_is_the_declared_value_never_reconciled_against_series_n():
    payload = _payload(
        entry=_entry(
            runs=8,
            duration=_metric(metric_name="duration_ms", n=6, unit="ms"),
            count=_metric(metric_name="render_count", n=8, unit="count"),
        )
    )
    assert payload["runs"] == 8
    assert payload["duration"]["n"] == 6
    assert payload["count"]["n"] == 8


# ===== D5 — the three flat keys =====


def test_initial_update_count_is_the_latest_entrys_value():
    payload = _payload(entry=_entry(initial_update_count=1))
    assert payload["initial_update_count"] == 1


def test_baseline_initial_update_count_is_the_caller_supplied_baseline():
    payload = _payload(baseline=0)
    assert payload["baseline_initial_update_count"] == 0


def test_null_baseline_is_none_never_a_falsy_zero():
    """[unmissable] `baseline=None` (no prior diagnostic / never measured)
    MUST serialize as `null` and MUST NOT be confused with `baseline=0`
    (measured, clean) — asserted with `is None`, never a falsy check,
    which `0` would also pass (design.md:391)."""
    payload = _payload(baseline=None, entry=_entry(initial_update_count=0))
    assert payload["baseline_initial_update_count"] is None
    assert payload["initial_update_count"] == 0
    assert payload["initial_update_state"] == "unknown"


def test_zero_to_one_transition_is_introduced():
    payload = _payload(baseline=0, entry=_entry(initial_update_count=1))
    assert payload["initial_update_state"] == "introduced"


def test_one_to_zero_transition_is_resolved():
    payload = _payload(baseline=1, entry=_entry(initial_update_count=0))
    assert payload["initial_update_state"] == "resolved"


def test_different_nonzero_transition_is_changed():
    payload = _payload(baseline=1, entry=_entry(initial_update_count=2))
    assert payload["initial_update_state"] == "changed"


def test_equal_nonzero_transition_is_unchanged():
    payload = _payload(baseline=1, entry=_entry(initial_update_count=1))
    assert payload["initial_update_state"] == "unchanged"


def test_no_delta_pct_or_pct_key_anywhere_for_the_update_count():
    """D5 is a state transition, never a delta — this is the explicit
    negative `design.md:392-393` requires."""
    payload = _payload(baseline=1, entry=_entry(initial_update_count=2))
    assert not any("delta_pct" in key or key.endswith("_pct") for key in payload)


def test_no_entry_id_anywhere_in_the_payload():
    payload = _payload()
    assert "entry_id" not in payload


def test_payload_is_json_serializable_and_lossless():
    payload = _payload(baseline=None)
    serialized = json.dumps(payload)
    assert json.loads(serialized) == payload


def test_contract_rejects_a_shape_change_without_version_bump():
    payload = _payload()
    assert set(payload.keys()) == set(_TOP_LEVEL_KEYS_AND_TYPES)
    assert payload["schema_version"] == 1, (
        "a shape change needs a SCHEMA_VERSION bump, not an inequality"
    )
