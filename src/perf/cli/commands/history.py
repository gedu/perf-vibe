"""`perf history <flow>` — typer command wiring the config loader + adapter
registry into `SqliteStore.history_runs`, then dispatching to the pretty or
`--json` renderer (SKILL rule 6). THE machine-readable per-flow historical
series the tool exists for: today runs go INTO the store but only `compare`'s
verdict ever comes out — `history` is the charting-export seam. Reads only —
writes nothing new.

`device_key` is derived the SAME way `compare`/`run` derive it
(`build_context_provider`, degrading gracefully to `unknown|unknown|physical`
with no device attached), so `history` sees whatever was actually persisted;
`--device-key` overrides it verbatim for exporting an arbitrary series.

Exit codes (SKILL rule 7): `0` success — a series was shown; `2` usage error
(unknown flow, an unknown `--metric` for that flow, or NO recorded history
for the flow+device+mode at all); `3` runtime/tooling error. NEVER `1` — this
command reports and does not gate (that is `budget-check`'s job).
"""

from __future__ import annotations

from collections.abc import Sequence

import typer

from perf.adapters.registry import build_context_provider, build_store
from perf.cli.output.context import NON_TTY_NUDGE, OutputContext
from perf.cli.output.errors import emit_error
from perf.cli.output.history_pretty import render_history
from perf.cli.output.json_reporter import render_json
from perf.config.loader import PerfConfig
from perf.contracts.history_v1 import build_history_payload
from perf.domain.model import HistoryRun

__all__ = ["history"]


def history(
    ctx: typer.Context,
    flow: str = typer.Argument(..., help="Config-known flow name to chart"),
    metric: str | None = typer.Option(
        None, "--metric", help="Restrict the series to ONE metric (default: every metric)"
    ),
    limit: int = typer.Option(
        50, "--limit", help="Most recent N runs to include (oldest→newest; clamped to >= 1)"
    ),
    restart: bool = typer.Option(
        False, "--restart", help="Chart the cold series (default: warm — matches `perf run`)"
    ),
    device: str | None = typer.Option(
        None, "--device", help="Pin a device serial (overrides MAESTRO_DEVICE/config)"
    ),
    device_key: str | None = typer.Option(
        None, "--device-key", help="Override the derived device_key VERBATIM"
    ),
) -> None:
    """Emit the per-flow historical series (per-metric {p50, p90, n, unit} for
    each recorded run, OLDEST→NEWEST). Performs NO device/subprocess I/O of
    its own beyond deriving the device_key — it only reads the local store."""

    state: dict = ctx.obj or {}
    output: OutputContext = state["output"]
    config: PerfConfig = state["config"]

    # Unknown flow is a usage error, checked UPFRONT against the config before
    # any store work (SKILL rule 5), mirroring `compare`.
    if flow not in config.flows:
        emit_error(
            output,
            f"unknown flow {flow!r}; must be one of the config-known "
            f"flows {sorted(config.flows)!r}",
        )
        raise typer.Exit(code=2)

    effective_limit = max(1, limit)
    mode = "cold" if restart else "warm"

    store = None
    try:
        resolved_device_key = _resolve_device_key(config, device=device, device_key=device_key)
        store = build_store(config.db_path)
        runs = store.history_runs(flow, resolved_device_key, mode, effective_limit)
    except Exception as exc:
        # Any unexpected failure is runtime/tooling (exit 3), never a usage
        # error and NEVER exit 1 (SKILL rule 7), matching `compare`.
        emit_error(output, f"unexpected failure reading history for {flow!r}: {exc}")
        raise typer.Exit(code=3) from None
    finally:
        _close_store(store)

    if not runs:
        # A config-known flow with ZERO recorded runs for this device/mode —
        # a usage error, not a runtime failure (mirrors `compare`).
        emit_error(
            output,
            f"no history for flow {flow!r} (device={resolved_device_key!r}, mode={mode!r})",
        )
        raise typer.Exit(code=2)

    if metric is not None:
        available = _available_metrics(runs)
        if metric not in available:
            emit_error(
                output,
                f"unknown metric {metric!r} for flow {flow!r}; available metrics: {available!r}",
            )
            raise typer.Exit(code=2)
        runs = _restrict_to_metric(runs, metric)

    try:
        if output.json_mode:
            payload = build_history_payload(flow, resolved_device_key, mode, runs)
            typer.echo(render_json(payload))
        else:
            if output.should_nudge_stderr:
                typer.echo(NON_TTY_NUDGE, err=True)
            typer.echo(
                render_history(flow, resolved_device_key, mode, runs, color=output.color_enabled)
            )
    except Exception as exc:
        emit_error(output, f"failed to render history output for {flow!r}: {exc}")
        raise typer.Exit(code=3) from None

    raise typer.Exit(code=0)


def _resolve_device_key(config: PerfConfig, *, device: str | None, device_key: str | None) -> str:
    """`--device-key` overrides the derived key VERBATIM; otherwise derive it
    exactly like `compare` does (`build_context_provider`), so `history`
    matches whatever `run` actually persisted."""

    if device_key is not None:
        return device_key
    resolved_device = device or config.device
    context_provider = build_context_provider(
        build_variant=config.build_variant,
        tool_version=config.tool_version,
        device=resolved_device,
    )
    return context_provider.context().device_key


def _available_metrics(runs: Sequence[HistoryRun]) -> list[str]:
    names: set[str] = set()
    for run in runs:
        for metric in run.metrics:
            names.add(metric.metric_name)
    return sorted(names)


def _restrict_to_metric(runs: Sequence[HistoryRun], metric_name: str) -> tuple[HistoryRun, ...]:
    """Keep every run (a run absent this metric still appears, with an empty
    metric set) but drop every metric except `metric_name` — so the exported
    series is exactly the requested single metric over time."""

    return tuple(
        HistoryRun(
            run_id=run.run_id,
            started_at=run.started_at,
            git_commit=run.git_commit,
            source=run.source,
            metrics=tuple(m for m in run.metrics if m.metric_name == metric_name),
        )
        for run in runs
    )


def _close_store(store: object) -> None:
    if store is not None and hasattr(store, "close"):
        try:
            store.close()
        except Exception as close_exc:
            # A store-close failure must NEVER override the computed exit code
            # (SKILL rule 7: never exit 1), matching `compare`.
            typer.echo(f"warning: failed to close store: {close_exc}", err=True)
