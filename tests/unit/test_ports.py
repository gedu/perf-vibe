"""Unit tests for the `ProgressReporter` port (`domain/ports.py`) — Slice A
task A.1. RED-before-GREEN: written before `ProgressReporter` existed.

`Protocol` in this codebase is never `@runtime_checkable` (no existing port
uses it — see `test_domain_boundary.py`), so "structurally satisfies" is
proven the same way the rest of the suite proves Protocol conformance: a
plain stub with NO inheritance from `ProgressReporter` is assigned to a
`ProgressReporter`-typed name (mypy's static structural check) and every
method is called successfully at runtime.
"""

from __future__ import annotations

import inspect

from perf.domain.ports import ProgressReporter


class _StubProgressReporter:
    """Deliberately does NOT inherit from `ProgressReporter` — proves the
    port is structural (duck-typed), not nominal."""

    def iteration_started(self, index: int, total: int) -> None:
        pass

    def iteration_finished(self, index: int, total: int, *, ok: bool) -> None:
        pass

    def awaiting_user_input(self, prompt: str) -> None:
        pass

    def relayed_line(self, text: str) -> None:
        pass


def test_progress_reporter_exposes_exactly_the_locked_live_methods():
    """Only the 4 LIVE semantic events — `recap` is intentionally absent
    (design "recap() placement": it lives on the concrete CLI reporter
    only, Slice C, never on this pure domain Protocol)."""
    method_names = {
        name
        for name, _ in inspect.getmembers(ProgressReporter, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    assert method_names == {
        "iteration_started",
        "iteration_finished",
        "awaiting_user_input",
        "relayed_line",
    }
    assert not hasattr(ProgressReporter, "recap")


def test_a_minimal_stub_structurally_satisfies_progress_reporter():
    stub: ProgressReporter = _StubProgressReporter()
    stub.iteration_started(1, 3)
    stub.iteration_finished(1, 3, ok=True)
    stub.awaiting_user_input("Perform the flow manually, then press Enter.")
    stub.relayed_line("some relayed tool output")
