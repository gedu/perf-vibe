"""Contract test for `contracts/reassure_list_v1.build_reassure_list_payload`
(SKILL rule 8: "A contract test MUST fail on any `--json` shape change
without a `schema_version` bump."). Pins the exact key set at BOTH levels —
top-level (`schema_version`, `imports`) and per-import (7 keys) — mirroring
`test_reassure_import_v1_contract.py`'s exact-set discipline.

Deliberately absent, and asserted absent, from each per-import dict:
- `ordering_key`/`ordered_at` — both mechanically derivable from
  `created_date`/`imported_at` already in the same dict (`ordering_key` is
  `'created_date' if created_date is not None else 'imported_at'`;
  `ordered_at` is `created_date or imported_at`), the same
  no-second-source-of-truth reasoning that refused `reassure_import_v1`'s
  `zero_entries`/`samples_imported`. The read model (`ReassureImportRow`)
  still carries both — the pretty renderer needs them — but the WIRE
  contract does not.
- `kind` — design A5: `reassure_import.kind` is never SELECTed by any query
  in this capability.
"""

from __future__ import annotations

import json

from perf.contracts.reassure_list_v1 import SCHEMA_VERSION, build_reassure_list_payload
from perf.domain.model import ReassureImportRow

_TOP_LEVEL_KEYS_AND_TYPES = {
    "schema_version": int,
    "imports": list,
}

_IMPORT_KEYS_AND_TYPES = {
    "import_id": int,
    "imported_at": str,
    "created_date": (str, type(None)),
    "branch": (str, type(None)),
    "commit_hash": (str, type(None)),
    "source_path": str,
    "entry_count": int,
}


def _row(**overrides: object) -> ReassureImportRow:
    defaults: dict[str, object] = {
        "import_id": 1,
        "ordered_at": "2026-01-01T00:00:00.000Z",
        "ordering_key": "created_date",
        "imported_at": "2026-01-01T00:05:00.000Z",
        "created_date": "2026-01-01T00:00:00.000Z",
        "branch": "main",
        "commit_hash": "abc123",
        "source_path": "current.perf",
        "entry_count": 4,
    }
    defaults.update(overrides)
    return ReassureImportRow(**defaults)


def _sample_payload(*rows: ReassureImportRow) -> dict:
    return build_reassure_list_payload(imports=rows or (_row(),))


def test_schema_version_is_1():
    assert SCHEMA_VERSION == 1
    payload = _sample_payload()
    assert payload["schema_version"] == 1


def test_top_level_keys_present_with_correct_types():
    payload = _sample_payload()
    for key, expected_type in _TOP_LEVEL_KEYS_AND_TYPES.items():
        assert key in payload, f"missing top-level reassure_list_v1 key: {key!r}"
        assert isinstance(payload[key], expected_type)


def test_exact_two_top_level_keys_no_more_no_fewer():
    payload = _sample_payload()
    assert set(payload.keys()) == set(_TOP_LEVEL_KEYS_AND_TYPES)
    assert len(payload) == 2


def test_per_import_keys_present_with_correct_types():
    payload = _sample_payload()
    row = payload["imports"][0]
    for key, expected_type in _IMPORT_KEYS_AND_TYPES.items():
        assert key in row, f"missing reassure_list_v1 import key: {key!r}"
        assert isinstance(row[key], expected_type)


def test_exact_seven_import_keys_no_more_no_fewer():
    payload = _sample_payload()
    row = payload["imports"][0]
    assert set(row.keys()) == set(_IMPORT_KEYS_AND_TYPES)
    assert len(row) == 7


def test_empty_imports_list_is_a_valid_payload():
    payload = build_reassure_list_payload(imports=())
    assert payload["imports"] == []


def test_no_ordering_key_anywhere_in_the_payload():
    payload = _sample_payload()
    assert "ordering_key" not in payload["imports"][0]


def test_no_ordered_at_anywhere_in_the_payload():
    payload = _sample_payload()
    assert "ordered_at" not in payload["imports"][0]


def test_no_kind_anywhere_in_the_payload():
    payload = _sample_payload()
    assert "kind" not in payload["imports"][0]


def test_created_date_null_survives_the_imported_at_fallback_case():
    row = _row(
        created_date=None,
        ordering_key="imported_at",
        ordered_at="2026-01-01T00:05:00.000Z",
    )
    payload = _sample_payload(row)
    assert payload["imports"][0]["created_date"] is None
    assert payload["imports"][0]["imported_at"] == "2026-01-01T00:05:00.000Z"


def test_multiple_imports_preserve_caller_order():
    """This builder never sorts — D2 ordering is the STORE's job
    (`store.reassure_imports`'s `ORDER BY` clause); this pure function
    shapes whatever sequence it is handed, verbatim."""
    first = _row(import_id=1)
    second = _row(import_id=2)
    payload = _sample_payload(first, second)
    assert [row["import_id"] for row in payload["imports"]] == [1, 2]


def test_payload_is_json_serializable_and_lossless():
    payload = _sample_payload()
    serialized = json.dumps(payload)
    assert json.loads(serialized) == payload


def test_contract_rejects_a_shape_change_without_version_bump():
    """Structural regression guard: any top-level key addition/removal/
    rename without a `SCHEMA_VERSION` bump fails this test (exact-set
    pinning, matching `test_reassure_import_v1_contract.py`'s pattern)."""
    payload = _sample_payload()
    assert set(payload.keys()) == set(_TOP_LEVEL_KEYS_AND_TYPES)
    assert payload["schema_version"] == 1, (
        "a shape change needs a SCHEMA_VERSION bump, not an inequality"
    )
