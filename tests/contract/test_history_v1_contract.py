"""Contract test for `contracts/history_v1.build_history_payload` (SKILL
rule 8: "A contract test MUST fail on any `--json` shape change without a
`schema_version` bump."). Pins the EXACT key set at EVERY level — top,
per-run item, and per-metric entry — mirroring `test_budget_check_v1.py`'s
exact-set discipline, so an additive change is caught just as a removal is.

This test never imports or delegates to `compare_v1`/`budget_check_v1`'s
contract tests — it validates ONLY against its own schema.
"""

from __future__ import annotations

import json
import math

from perf.contracts.history_v1 import SCHEMA_VERSION, build_history_payload
from perf.domain.model import HistoryMetric, HistoryRun

_TOP_LEVEL_KEYS = {"schema_version", "flow", "device", "mode", "runs"}
_RUN_ITEM_KEYS = {"run_id", "started_at", "commit", "source", "metrics"}
_METRIC_ENTRY_KEYS = {"p50", "p90", "n", "unit"}

_RUN_ITEM_TYPES = {
    "run_id": int,
    "started_at": str,
    "commit": (str, type(None)),
    "source": str,
    "metrics": dict,
}
_METRIC_ENTRY_TYPES = {
    "p50": (float, int, type(None)),
    "p90": (float, int, type(None)),
    "n": int,
    "unit": str,
}

_FLOW = "checkout"
_DEVICE = "TestDevice|14|physical"
_MODE = "warm"


def _sample_runs() -> tuple[HistoryRun, ...]:
    return (
        HistoryRun(
            run_id=1,
            started_at="2020-01-01T00:00:01+00:00",
            git_commit="c1",
            source="local:test",
            metrics=(
                HistoryMetric(metric_name="checkout", p50=100.0, p90=110.0, n=3, unit="ms"),
                HistoryMetric(metric_name="fps_avg", p50=60.0, p90=58.0, n=2, unit="fps"),
            ),
        ),
        HistoryRun(
            run_id=2,
            started_at="2020-01-01T00:00:02+00:00",
            git_commit=None,  # a run persisted with no resolvable git commit
            source="ci",
            metrics=(HistoryMetric(metric_name="checkout", p50=None, p90=None, n=0, unit="ms"),),
        ),
    )


def _payload() -> dict:
    return build_history_payload(_FLOW, _DEVICE, _MODE, _sample_runs())


def test_schema_version_is_1():
    assert SCHEMA_VERSION == 1
    assert _payload()["schema_version"] == 1


def test_top_level_keys_are_exactly_pinned():
    assert set(_payload().keys()) == _TOP_LEVEL_KEYS


def test_top_level_labels_reflect_the_invocation():
    payload = _payload()
    assert payload["flow"] == _FLOW
    assert payload["device"] == _DEVICE
    assert payload["mode"] == _MODE
    assert isinstance(payload["runs"], list)
    assert len(payload["runs"]) == 2


def test_each_run_item_key_set_and_types_are_exactly_pinned():
    for run_item in _payload()["runs"]:
        assert set(run_item.keys()) == _RUN_ITEM_KEYS
        for key, expected_type in _RUN_ITEM_TYPES.items():
            assert isinstance(run_item[key], expected_type), (
                f"{key!r} has type {type(run_item[key])!r}, expected {expected_type!r}"
            )


def test_each_metric_entry_key_set_and_types_are_exactly_pinned():
    for run_item in _payload()["runs"]:
        for entry in run_item["metrics"].values():
            assert set(entry.keys()) == _METRIC_ENTRY_KEYS
            for key, expected_type in _METRIC_ENTRY_TYPES.items():
                assert isinstance(entry[key], expected_type), (
                    f"{key!r} has type {type(entry[key])!r}, expected {expected_type!r}"
                )


def test_metrics_keyed_by_metric_name():
    first_run = _payload()["runs"][0]
    assert set(first_run["metrics"].keys()) == {"checkout", "fps_avg"}
    assert first_run["metrics"]["checkout"]["p90"] == 110.0
    assert first_run["metrics"]["fps_avg"]["unit"] == "fps"


def test_commit_is_null_when_run_had_no_git_commit():
    second_run = _payload()["runs"][1]
    assert second_run["commit"] is None


def test_runs_preserve_oldest_to_newest_order_from_the_read_model():
    run_ids = [run_item["run_id"] for run_item in _payload()["runs"]]
    assert run_ids == [1, 2]


def test_empty_series_still_valid_shape():
    payload = build_history_payload(_FLOW, _DEVICE, _MODE, ())
    assert set(payload.keys()) == _TOP_LEVEL_KEYS
    assert payload["runs"] == []


def test_payload_is_json_serializable():
    payload = _payload()
    assert json.loads(json.dumps(payload)) == payload


def test_non_finite_p90_survives_the_json_reporter_sanitizer():
    """The contract builder passes floats through verbatim; the invariant
    that a non-finite value renders as valid JSON `null` is owned by
    `cli/output/json_reporter.render_json` (shared with `compare`). Prove
    the two compose: an `inf` p90 becomes `null`, never the invalid literal
    `Infinity`."""
    from perf.cli.output.json_reporter import render_json

    runs = (
        HistoryRun(
            run_id=9,
            started_at="2020-01-01T00:00:09+00:00",
            git_commit="c9",
            source="local:test",
            metrics=(HistoryMetric(metric_name="checkout", p50=1.0, p90=math.inf, n=1, unit="ms"),),
        ),
    )
    rendered = render_json(build_history_payload(_FLOW, _DEVICE, _MODE, runs))
    assert "Infinity" not in rendered
    assert json.loads(rendered)["runs"][0]["metrics"]["checkout"]["p90"] is None


def test_contract_rejects_a_shape_change_without_version_bump():
    payload = _payload()
    assert set(payload.keys()) == _TOP_LEVEL_KEYS
    for run_item in payload["runs"]:
        assert set(run_item.keys()) == _RUN_ITEM_KEYS
        for entry in run_item["metrics"].values():
            assert set(entry.keys()) == _METRIC_ENTRY_KEYS
    assert payload["schema_version"] >= 1
