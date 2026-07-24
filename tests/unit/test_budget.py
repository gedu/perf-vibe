"""Pure-domain unit tests for `perf.domain.budget.evaluate` (budget-check
design §3, task 1.8) — the fail-open/fail-closed gate matrix.

No I/O, no adapters — only `CompareResult`/`Verdict` construction and the
ONE pure gate rule. `evaluate` reuses compare's already-shipped
`Verdict.status` classification wholesale; it re-derives nothing.
"""

from __future__ import annotations

from perf.domain import budget
from perf.domain.calibration import CalibrationReport, MetricCalibration
from perf.domain.model import CompareResult, Verdict
from perf.domain.regression import (
    STATUS_IMPROVEMENT,
    STATUS_INSUFFICIENT_DATA,
    STATUS_REGRESSION,
    STATUS_STABLE,
)

_CALIBRATION = CalibrationReport(
    metrics=(
        MetricCalibration(
            metric_name="checkout",
            status="reasonable",
            flagged_count=0,
            total_count=3,
            max_abs=1.0,
            noise_pct=1.0,
        ),
    ),
    status="reasonable",
    runs_flagged=0,
    runs_total=3,
)


def _verdict(metric_name: str, status: str) -> Verdict:
    return Verdict(metric_name=metric_name, delta_pct=1.0, threshold_pct=5.0, status=status)


def _result(*verdicts: Verdict) -> CompareResult:
    return CompareResult(verdicts=tuple(verdicts), calibration=_CALIBRATION)


def test_regression_present_fails_the_gate_and_lists_the_offender():
    result = _result(_verdict("checkout", STATUS_REGRESSION))
    bv = budget.evaluate(result)
    assert bv.gate_status == budget.GATE_FAIL
    assert bv.offending_metrics == ("checkout",)
    assert bv.gated_verdicts[0].gated is True


def test_multiple_regressions_are_all_aggregated_not_first_only():
    result = _result(
        _verdict("checkout", STATUS_REGRESSION),
        _verdict("fps_avg", STATUS_REGRESSION),
        _verdict("ram_avg_mb", STATUS_STABLE),
    )
    bv = budget.evaluate(result)
    assert bv.gate_status == budget.GATE_FAIL
    assert set(bv.offending_metrics) == {"checkout", "fps_avg"}


def test_mixed_regression_and_stable_is_all_or_nothing_only_regression_gated():
    result = _result(
        _verdict("checkout", STATUS_REGRESSION),
        _verdict("fps_avg", STATUS_STABLE),
    )
    bv = budget.evaluate(result)
    assert bv.gate_status == budget.GATE_FAIL
    gated_by_metric = {gv.verdict.metric_name: gv.gated for gv in bv.gated_verdicts}
    assert gated_by_metric == {"checkout": True, "fps_avg": False}


def test_all_stable_or_improvement_passes_with_no_offenders():
    result = _result(
        _verdict("checkout", STATUS_STABLE),
        _verdict("fps_avg", STATUS_IMPROVEMENT),
    )
    bv = budget.evaluate(result)
    assert bv.gate_status == budget.GATE_PASS
    assert bv.offending_metrics == ()


def test_all_insufficient_data_non_strict_is_skipped_fail_open():
    result = _result(
        _verdict("checkout", STATUS_INSUFFICIENT_DATA),
        _verdict("fps_avg", STATUS_INSUFFICIENT_DATA),
    )
    bv = budget.evaluate(result, strict=False)
    assert bv.gate_status == budget.GATE_SKIPPED
    assert bv.offending_metrics == ()


def test_all_insufficient_data_strict_fails_every_metric_gated():
    result = _result(
        _verdict("checkout", STATUS_INSUFFICIENT_DATA),
        _verdict("fps_avg", STATUS_INSUFFICIENT_DATA),
    )
    bv = budget.evaluate(result, strict=True)
    assert bv.gate_status == budget.GATE_FAIL
    assert set(bv.offending_metrics) == {"checkout", "fps_avg"}
    assert all(gv.gated for gv in bv.gated_verdicts)


def test_mixed_stable_and_insufficient_non_strict_passes():
    result = _result(
        _verdict("checkout", STATUS_STABLE),
        _verdict("fps_avg", STATUS_INSUFFICIENT_DATA),
    )
    bv = budget.evaluate(result, strict=False)
    assert bv.gate_status == budget.GATE_PASS


def test_mixed_stable_and_insufficient_strict_fails():
    result = _result(
        _verdict("checkout", STATUS_STABLE),
        _verdict("fps_avg", STATUS_INSUFFICIENT_DATA),
    )
    bv = budget.evaluate(result, strict=True)
    assert bv.gate_status == budget.GATE_FAIL
    gated_by_metric = {gv.verdict.metric_name: gv.gated for gv in bv.gated_verdicts}
    assert gated_by_metric == {"checkout": False, "fps_avg": True}


def test_improvement_never_gates():
    result = _result(_verdict("checkout", STATUS_IMPROVEMENT))
    bv = budget.evaluate(result, strict=True)  # strict must not change improvement's outcome
    assert bv.gate_status == budget.GATE_PASS
    assert bv.gated_verdicts[0].gated is False


def test_calibration_passed_through_unchanged_and_never_alters_gate_status():
    result = _result(_verdict("checkout", STATUS_REGRESSION))
    bv = budget.evaluate(result)
    assert bv.calibration is _CALIBRATION
    assert bv.gate_status == budget.GATE_FAIL  # calibration status ("reasonable") never leaks in


def test_strict_flag_is_carried_on_the_returned_budget_verdict():
    result = _result(_verdict("checkout", STATUS_STABLE))
    assert budget.evaluate(result, strict=True).strict is True
    assert budget.evaluate(result, strict=False).strict is False
