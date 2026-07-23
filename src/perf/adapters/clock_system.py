"""`Clock` port adapter — the real wall clock (design §5: `RunFlowUseCase`
depends on `Clock` for its results-filename timestamp slug; `SqliteStore`
already has its own private equivalent for `run.started_at` — this is the
one CLI callers wire in via the registry so both layers share the same
notion of "now" without either importing the other)."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """`Clock` (`domain/ports.py`) implementation — real UTC wall clock."""

    def now_utc_iso(self) -> str:
        return datetime.now(UTC).isoformat()
