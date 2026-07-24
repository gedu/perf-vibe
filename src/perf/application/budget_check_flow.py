"""`BudgetCheckUseCase` — application layer orchestration for `perf
budget-check` (design §6, decision D3). PURE orchestration: no I/O of its
own, no adapter imports (SKILL rule 1) — the only side effect (reading
history) happens behind the already-shipped `Analyzer` port
(`domain/ports.py`), reused wholesale from `compare` (design "Reuse, not
re-derivation"). This module re-derives no statistic and introduces no new
gate logic of its own — the gate decision lives ENTIRELY in the pure
`domain/budget.evaluate` (design §3), which this use-case merely calls.

Error mapping (mirrors `run_flow.py`'s exception->exit-code contract,
design §6):
  - `Analyzer.compare_latest` returning `None` (no history for the
    flow/device/mode at all) -> `UsageError` (CLI maps to exit 2).
  - `Analyzer.compare_latest` raising (store/tooling failure) ->
    `BudgetCheckFailedError` (CLI maps to exit 3).
  - A gate FAIL is a normal RETURN VALUE (`BudgetVerdict.gate_status ==
    GATE_FAIL`), never a raise (decision D3) — this use-case NEVER raises
    for a failing gate. The CLI (PR-C) maps `gate_status == "fail"` to
    exit 1.
"""

from __future__ import annotations

from dataclasses import dataclass

from perf.domain import budget
from perf.domain.model import BudgetVerdict
from perf.domain.ports import Analyzer

__all__ = [
    "BudgetCheckFailedError",
    "BudgetCheckRequest",
    "BudgetCheckUseCase",
    "UsageError",
]


class UsageError(Exception):
    """Bad invocation, resolved BEFORE any gate decision — no history at
    all for the flow/device/mode (mirrors `run_flow.UsageError`). The CLI
    maps this to exit code 2."""


class BudgetCheckFailedError(Exception):
    """Runtime/tooling failure while reading history for the gate (mirrors
    `run_flow.RunFailedError`) — the CLI maps this to exit code 3.
    `diagnostics` carries bounded detail for the CLI to surface."""

    def __init__(self, message: str, *, diagnostics: str | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


@dataclass(frozen=True)
class BudgetCheckRequest:
    """Everything `BudgetCheckUseCase.execute()` needs for one invocation.
    Adapter SELECTION already happened at composition time (the CLI,
    design §7) — this request only carries per-invocation parameters,
    never adapter instances."""

    flow_name: str
    device_key: str
    mode: str  # 'warm' | 'cold'
    strict: bool = False


class BudgetCheckUseCase:
    """Orchestrates one `perf budget-check` invocation (design §6).
    Depends ONLY on the `Analyzer` port + the pure `domain.budget` module —
    never an adapter module (SKILL rule 1; enforced by
    `tests/unit/test_domain_boundary.py`-style static checks for
    `domain/`; this class simply never imports one)."""

    def __init__(self, *, analyzer: Analyzer) -> None:
        self._analyzer = analyzer

    def execute(self, request: BudgetCheckRequest) -> BudgetVerdict:
        try:
            result = self._analyzer.compare_latest(
                request.flow_name, request.device_key, request.mode
            )
        except Exception as exc:  # store/tooling failure — mirrors run_flow.py
            raise BudgetCheckFailedError(
                f"Failed to evaluate budget for {request.flow_name!r}: {exc}",
                diagnostics=str(exc),
            ) from exc

        if result is None:  # no runs at all (mirrors compare's C2/C7)
            raise UsageError(
                f"no history for flow {request.flow_name!r} "
                f"(device={request.device_key!r}, mode={request.mode!r})"
            )

        return budget.evaluate(result, strict=request.strict)
