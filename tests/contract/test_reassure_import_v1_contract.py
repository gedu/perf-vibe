"""Contract test for `contracts/reassure_import_v1.build_reassure_import_payload`
(SKILL rule 8: "A contract test MUST fail on any `--json` shape change
without a `schema_version` bump."). Pins the exact NINE top-level keys —
mirrors `test_markers_doctor_v1_contract.py`'s exact-set discipline.

`kind` is the ninth key: PR4a added the `reassure_import.kind` column, and
PR4b is the first slice that WRITES it, so only now can the contract report
it — a contract pins a key set that already exists to report; `kind`
could not have been frozen before its column landed (spec requirement
"reassure_import_v1 --json Contract").

Deliberately absent, and asserted absent: `samples_imported` (one count
cannot describe two independently-sized series — `duration_samples_imported`
and `count_samples_imported` are reported separately) and `zero_entries`
(a pure derived AND of `entries_imported == 0` and
`already_imported == false` — the same second-source-of-truth problem this
change already rejects for `meanDuration`/`stdevDuration`).
"""

from __future__ import annotations

import json

from perf.contracts.reassure_import_v1 import SCHEMA_VERSION, build_reassure_import_payload

_REQUIRED_KEYS_AND_TYPES = {
    "schema_version": int,
    "path": str,
    "content_hash": str,
    "kind": str,
    "already_imported": bool,
    "entries_imported": int,
    "entries_skipped": int,
    "duration_samples_imported": int,
    "count_samples_imported": int,
}


def _sample_payload(**overrides: object) -> dict:
    defaults: dict[str, object] = {
        "path": "tests/fixtures/reassure_sample.perf",
        "content_hash": "deadbeef" * 8,
        "kind": "current",
        "already_imported": False,
        "entries_imported": 3,
        "entries_skipped": 6,
        "duration_samples_imported": 10,
        "count_samples_imported": 12,
    }
    defaults.update(overrides)
    return build_reassure_import_payload(**defaults)


def test_schema_version_is_1():
    assert SCHEMA_VERSION == 1
    payload = _sample_payload()
    assert payload["schema_version"] == 1


def test_required_keys_present_with_correct_types():
    payload = _sample_payload()
    for key, expected_type in _REQUIRED_KEYS_AND_TYPES.items():
        assert key in payload, f"missing required reassure_import_v1 key: {key!r}"
        assert isinstance(payload[key], expected_type), (
            f"{key!r} has type {type(payload[key])!r}, expected {expected_type!r}"
        )


def test_exact_nine_keys_no_more_no_fewer():
    payload = _sample_payload()
    assert set(payload.keys()) == set(_REQUIRED_KEYS_AND_TYPES)


def test_no_samples_imported_key_anywhere_in_the_payload():
    payload = _sample_payload()
    assert "samples_imported" not in payload


def test_no_zero_entries_key_anywhere_in_the_payload():
    payload = _sample_payload()
    assert "zero_entries" not in payload


def test_duration_and_count_counters_are_independent():
    payload = _sample_payload(duration_samples_imported=2, count_samples_imported=3)
    assert payload["duration_samples_imported"] == 2
    assert payload["count_samples_imported"] == 3


def test_kind_round_trips_verbatim():
    assert _sample_payload(kind="baseline")["kind"] == "baseline"
    assert _sample_payload(kind="unknown")["kind"] == "unknown"


def test_payload_is_flat_no_nested_objects_or_arrays():
    payload = _sample_payload()
    for key, value in payload.items():
        assert not isinstance(value, (dict, list, tuple)), f"{key!r} is not a flat scalar"


def test_payload_is_json_serializable_and_lossless():
    payload = _sample_payload()
    serialized = json.dumps(payload)
    assert json.loads(serialized) == payload


def test_contract_rejects_a_shape_change_without_version_bump():
    """Structural regression guard: any top-level key addition/removal/
    rename without a `SCHEMA_VERSION` bump fails this test (exact-set
    pinning, matching `test_markers_doctor_v1_contract.py`'s pattern)."""
    payload = _sample_payload()
    assert set(payload.keys()) == set(_REQUIRED_KEYS_AND_TYPES)
    assert payload["schema_version"] == 1, (
        "a shape change needs a SCHEMA_VERSION bump, not an inequality"
    )
