"""Unit tests for `application.budget_check_flow.BudgetCheckUseCase` (design
§6, task 2.1) — orchestrates the `Analyzer` port + the pure `domain.budget`
gate rule. Driven entirely via `FakeAnalyzer` (tests/fakes.py) — no real
adapter, no I/O.
"""

from __future__ import annotations

import pytest

from fakes import FakeAnalyzer
from perf.application.budget_check_flow import (
    BudgetCheckFailedError,
    BudgetCheckRequest,
    BudgetCheckUseCase,
    UsageError,
)
from perf.domain import budget
from perf.domain.calibration import CalibrationReport
from perf.domain.model import CompareResult, Verdict
from perf.domain.regression import STATUS_REGRESSION, STATUS_STABLE

_CALIBRATION = CalibrationReport(metrics=(), status="reasonable", runs_flagged=0, runs_total=3)


def _verdict(metric_name: str, status: str) -> Verdict:
    return Verdict(metric_name=metric_name, delta_pct=1.0, threshold_pct=5.0, status=status)


def _result(*verdicts: Verdict) -> CompareResult:
    return CompareResult(verdicts=tuple(verdicts), calibration=_CALIBRATION)


def _request(*, strict: bool = False) -> BudgetCheckRequest:
    return BudgetCheckRequest(
        flow_name="checkout-warm", device_key="Pixel-Fake|14|physical", mode="warm", strict=strict
    )


def test_analyzer_raising_becomes_budget_check_failed_error():
    analyzer = FakeAnalyzer(raises=RuntimeError("store exploded"))
    use_case = BudgetCheckUseCase(analyzer=analyzer)

    with pytest.raises(BudgetCheckFailedError):
        use_case.execute(_request())


def test_analyzer_returning_none_becomes_usage_error():
    analyzer = FakeAnalyzer(result=None)
    use_case = BudgetCheckUseCase(analyzer=analyzer)

    with pytest.raises(UsageError):
        use_case.execute(_request())


def test_compare_result_delegates_to_domain_budget_evaluate_unchanged():
    result = _result(_verdict("checkout", STATUS_REGRESSION), _verdict("fps_avg", STATUS_STABLE))
    analyzer = FakeAnalyzer(result=result)
    use_case = BudgetCheckUseCase(analyzer=analyzer)

    expected = budget.evaluate(result, strict=False)
    actual = use_case.execute(_request(strict=False))

    # Assert the use-case genuinely calls `budget.evaluate` rather than
    # re-implementing the gate rule inline — same shape, same values.
    assert actual == expected
    assert actual.gate_status == budget.GATE_FAIL
    assert actual.offending_metrics == ("checkout",)


def test_strict_flag_is_forwarded_to_domain_budget_evaluate():
    result = _result(_verdict("checkout", STATUS_STABLE))
    analyzer = FakeAnalyzer(result=result)
    use_case = BudgetCheckUseCase(analyzer=analyzer)

    actual = use_case.execute(_request(strict=True))

    assert actual.strict is True
    assert actual == budget.evaluate(result, strict=True)


def test_analyzer_is_called_with_the_request_parameters():
    result = _result(_verdict("checkout", STATUS_STABLE))
    analyzer = FakeAnalyzer(result=result)
    use_case = BudgetCheckUseCase(analyzer=analyzer)

    use_case.execute(_request())

    assert analyzer.calls == [("checkout-warm", "Pixel-Fake|14|physical", "warm")]
