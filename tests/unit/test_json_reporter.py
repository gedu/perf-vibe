"""`render_json` must ALWAYS emit RFC-8259-valid JSON — the `--json` stream
is the ONE machine contract (SKILL rule 6). Python's `json.dumps` default
(`allow_nan=True`) emits the literals `NaN`/`Infinity`, which `jq`,
`JSON.parse`, and every strict parser reject. A real trigger exists:
`regression.classify` sets `delta_pct = ±inf` when the baseline is exactly
0 (an instant marker) and the latest value is not.
"""

from __future__ import annotations

import json
import math

from perf.cli.output.json_reporter import render_json


def test_non_finite_floats_serialize_as_null():
    payload = {
        "delta_pct": float("inf"),
        "neg": float("-inf"),
        "nan": float("nan"),
        "fine": 1.5,
    }
    parsed = json.loads(render_json(payload))
    assert parsed["delta_pct"] is None
    assert parsed["neg"] is None
    assert parsed["nan"] is None
    assert parsed["fine"] == 1.5


def test_non_finite_floats_are_sanitized_recursively():
    payload = {
        "verdicts": [{"delta_pct": float("inf"), "series": (1.0, float("nan"), 3.0)}],
        "nested": {"deep": {"value": float("-inf")}},
    }
    parsed = json.loads(render_json(payload))
    assert parsed["verdicts"][0]["delta_pct"] is None
    assert parsed["verdicts"][0]["series"] == [1.0, None, 3.0]
    assert parsed["nested"]["deep"]["value"] is None


def test_output_never_contains_bare_json_literals():
    # Belt and braces: the rendered string itself must be strict-parseable
    # and free of the invalid literals, whatever the payload shape.
    rendered = render_json({"a": float("nan"), "b": [float("inf")], "label": "Infinity room"})
    assert "NaN" not in rendered
    # The STRING "Infinity room" must survive untouched — only float values
    # are sanitized, never text content.
    parsed = json.loads(rendered)
    assert parsed["label"] == "Infinity room"
    assert parsed["a"] is None
    assert parsed["b"] == [None]


def test_finite_payloads_are_byte_identical_to_before():
    payload = {"z": 1, "a": [1.0, 2.5], "m": {"k": "v"}}
    assert render_json(payload) == json.dumps(payload, sort_keys=True)


def test_math_isfinite_guard_matches_python_semantics():
    # Documents the boundary: bools/ints pass through (bool is an int
    # subclass and `math.isfinite(True)` is True — it must stay `true`).
    parsed = json.loads(render_json({"flag": True, "n": 3}))
    assert parsed["flag"] is True
    assert parsed["n"] == 3
    assert math.isfinite(3)
