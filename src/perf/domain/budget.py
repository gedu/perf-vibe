"""The ONE pure gate rule for `perf budget-check` (design §3, decision D3).

PURE MODULE — no adapter imports, no I/O. `evaluate` re-derives no
statistic: it consumes compare's already-shipped, corner-case-hardened
`CompareResult` (`Analyzer.compare_latest`'s return) and applies a single
fail-open/fail-closed rule over each `Verdict.status`. `strict` is a
PARAMETER of this one function, not a second code path — fail-open vs
fail-closed differ only in whether `insufficient-data` counts as an
offender (spec 'Fail-Open Default Behavior' / '--strict Fail-Closed Mode').

Gate-fail is a RETURN value (`BudgetVerdict.gate_status == GATE_FAIL`),
never a raise — the CLI owns the exception->exit mapping for usage/runtime
errors; this module only ever decides pass/fail/skipped.
"""

from __future__ import annotations

from perf.domain import regression
from perf.domain.model import (
    GATE_FAIL,
    GATE_PASS,
    GATE_SKIPPED,
    BudgetVerdict,
    CompareResult,
    GatedVerdict,
)

__all__ = ["GATE_FAIL", "GATE_PASS", "GATE_SKIPPED", "evaluate"]


def evaluate(result: CompareResult, *, strict: bool = False) -> BudgetVerdict:
    """Applies the relative regression gate over every metric verdict in
    `result` (design §3).

    Rule summary:
    - `regression` -> always an offender -> contributes to `fail`.
    - `insufficient-data` -> offender ONLY under `strict` ("guilty until
      proven safe" — spec '--strict Fail-Closed Mode').
    - `stable`/`improvement` -> never an offender.
    - `fail` if ANY offender; else `skipped` if NOTHING was gradeable (every
      metric `insufficient-data`, non-strict — fail-OPEN, unreachable under
      `strict` since insufficient-data always offends there); else `pass`.

    All-or-nothing: a single offender fails the whole flow, but the loop
    AGGREGATES every offender into `offending_metrics` — never stops at the
    first (spec 'All-or-Nothing Gating with Full Aggregation') — so the
    `--json` payload and pretty output report the full blast radius.
    """

    gated: list[GatedVerdict] = []
    offending: list[str] = []
    saw_real_verdict = False  # any metric that was actually gradeable

    for verdict in result.verdicts:
        if verdict.status == regression.STATUS_REGRESSION:
            is_offender = True
            saw_real_verdict = True
        elif verdict.status == regression.STATUS_INSUFFICIENT_DATA:
            is_offender = strict
        else:  # stable | improvement
            is_offender = False
            saw_real_verdict = True

        gated.append(GatedVerdict(verdict=verdict, gated=is_offender))
        if is_offender:
            offending.append(verdict.metric_name)

    if offending:
        status = GATE_FAIL
    elif not saw_real_verdict:
        status = GATE_SKIPPED
    else:
        status = GATE_PASS

    return BudgetVerdict(
        gate_status=status,
        gated_verdicts=tuple(gated),
        offending_metrics=tuple(offending),
        strict=strict,
        calibration=result.calibration,
    )
