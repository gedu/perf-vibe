"""Contract test for `contracts/markers_snippet_v1.build_snippet_payload`
(SKILL rule 8: "A contract test MUST fail on any `--json` shape change
without a `schema_version` bump."). Asserts required keys + types for the
`markers_snippet_v1` payload — mirrors `test_init_v1_contract.py`.
markers-command Phase 2 task 2.2.
"""

from __future__ import annotations

import json

from perf.contracts.markers_snippet_v1 import SCHEMA_VERSION, build_snippet_payload

_REQUIRED_KEYS_AND_TYPES = {
    "schema_version": int,
    "lang": str,
    "code": str,
}


def _sample_payload(*, lang: str = "ts", code: str = "export const markStart = ...;") -> dict:
    return build_snippet_payload(lang=lang, code=code)


def test_schema_version_is_1():
    assert SCHEMA_VERSION == 1
    payload = _sample_payload()
    assert payload["schema_version"] == 1


def test_required_keys_present_with_correct_types():
    payload = _sample_payload()
    for key, expected_type in _REQUIRED_KEYS_AND_TYPES.items():
        assert key in payload, f"missing required markers_snippet_v1 key: {key!r}"
        assert isinstance(payload[key], expected_type), (
            f"{key!r} has type {type(payload[key])!r}, expected {expected_type!r}"
        )


def test_lang_round_trips_ts_and_js():
    assert _sample_payload(lang="ts")["lang"] == "ts"
    assert _sample_payload(lang="js")["lang"] == "js"


def test_code_round_trips_verbatim():
    code = "import { markStart, markEnd } from './perf';"
    payload = _sample_payload(code=code)
    assert payload["code"] == code


def test_payload_is_json_serializable_and_lossless():
    payload = _sample_payload()
    serialized = json.dumps(payload)
    assert json.loads(serialized) == payload


def test_contract_rejects_a_shape_change_without_version_bump():
    """Structural regression guard: removing/renaming/adding a key without
    bumping `SCHEMA_VERSION` fails this test (exact-set pinning, matching
    `test_json_v1_contract.py`'s pattern)."""
    payload = _sample_payload()
    assert set(payload.keys()) == set(_REQUIRED_KEYS_AND_TYPES)
    assert payload["schema_version"] >= 1
