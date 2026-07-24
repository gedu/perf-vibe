"""`perf budget-check <flow>` — typer command wiring the config loader +
adapter registry into `BudgetCheckUseCase`, then dispatching to
budget-check's OWN pretty renderer or `--json` (design §7, decision D3).
Mirrors `cli/commands/compare.py`'s composition + exit discipline, but
budget-check SPENDS the exit `1` compare deliberately never uses.

Exit codes (design §7/§12, spec 'Exit-Code Contract'): `0` gate `pass` or
`skipped`; `1` gate `fail` — a CONFIRMED regression in default mode, or an
unprovable-safety case under `--strict` (decision D4); `2` usage error
(unknown flow, no history at all, or an unknown `--metric` name); `3`
runtime/tooling failure. This command NEVER lets an exception escape as
Python's default exit code `1`, and ALWAYS prints the verdict (pretty or
`--json`) before exiting — never silent, in any state (decision D3).
"""

from __future__ import annotations

import typer

from perf.adapters.registry import (
    build_analyzer,
    build_commit_log,
    build_context_provider,
    build_store,
)
from perf.application.budget_check_flow import (
    BudgetCheckFailedError,
    BudgetCheckRequest,
    BudgetCheckUseCase,
    UsageError,
)
from perf.cli.output.budget_check_pretty import render_metric_detail, render_summary
from perf.cli.output.context import NON_TTY_NUDGE, OutputContext
from perf.cli.output.json_reporter import render_json
from perf.config.loader import PerfConfig
from perf.contracts.budget_check_v1 import build_payload
from perf.domain.model import GATE_FAIL

__all__ = ["budget_check"]


def budget_check(
    ctx: typer.Context,
    flow: str = typer.Argument(..., help="Config-known flow name to gate"),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Fail-closed: insufficient-data/no-baseline cases gate the flow (default: fail-open)",
    ),
    metric: str | None = typer.Option(
        None, "--metric", help="Render a single-metric detail chart (pretty output only)"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", help="Auto-expand regressed metrics inline (pretty output only)"
    ),
    restart: bool = typer.Option(
        False, "--restart", help="Gate the cold series (default: warm — matches `perf run`)"
    ),
    device: str | None = typer.Option(
        None, "--device", help="Pin a device serial (overrides MAESTRO_DEVICE/config)"
    ),
) -> None:
    """The CI gate: reuses compare's already-shipped `Analyzer.compare_latest`
    seam wholesale and applies ONE pure gate rule (`domain/budget.evaluate`)
    over the returned verdicts. This command performs NO device/subprocess
    I/O of its own beyond the render-time `git log` commit-subject lookup
    (`CommitLog`, at most once per invocation)."""

    state: dict = ctx.obj or {}
    output: OutputContext = state["output"]
    config: PerfConfig = state["config"]

    # Usage-error-before-work guard (mirrors `compare.py`, corner case B2):
    # an unknown flow is a usage error regardless of `--strict`.
    if flow not in config.flows:
        typer.echo(
            f"Error: unknown flow {flow!r}; must be one of the config-known "
            f"flows {sorted(config.flows)!r}",
            err=True,
        )
        raise typer.Exit(code=2)

    resolved_device = device or config.device
    mode = "cold" if restart else "warm"

    store = None
    rc = None
    verdict = None
    try:
        context_provider = build_context_provider(
            build_variant=config.build_variant,
            tool_version=config.tool_version,
            device=resolved_device,
        )
        rc = context_provider.context()

        store = build_store(config.db_path)
        analyzer = build_analyzer(
            store,
            threshold_pct=config.threshold_pct,
            floors=config.floors,
            min_baseline_commits=config.min_baseline_commits,
            warmup_k=config.warmup_k,
            baseline_n=config.baseline_n,
        )
        use_case = BudgetCheckUseCase(analyzer=analyzer)
        verdict = use_case.execute(
            BudgetCheckRequest(flow_name=flow, device_key=rc.device_key, mode=mode, strict=strict)
        )
    except UsageError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from None
    except BudgetCheckFailedError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=3) from None
    except Exception as exc:
        # unexpected failure is a runtime/tooling failure, never Python's
        # default exit 1 and never silently 0 (spec 'Runtime/tooling
        # failure exits 3').
        typer.echo(f"Error: unexpected failure evaluating budget for {flow!r}: {exc}", err=True)
        raise typer.Exit(code=3) from None
    finally:
        if store is not None and hasattr(store, "close"):
            try:
                store.close()
            except Exception as close_exc:
                # NEVER override the already-computed exit code.
                typer.echo(f"warning: failed to close store: {close_exc}", err=True)

    assert verdict is not None and rc is not None  # guaranteed by the guards above

    try:
        if output.json_mode:
            # `--json` always emits the full flat payload, unaffected by
            # `--metric`/`--verbose`/`--no-color` (spec scenario, task
            # 3.16) — dispatched FIRST, before any `--metric` validation.
            typer.echo(render_json(build_payload(verdict)))
        elif metric is not None:
            valid_names = sorted(gv.verdict.metric_name for gv in verdict.gated_verdicts)
            if metric not in valid_names:
                # A typo'd `--metric` name is a usage error (task 3.12/
                # 3.13) — distinct from a valid name with no data THIS run
                # (task 3.14), which `render_metric_detail` handles inline
                # without ever reaching exit 2.
                typer.echo(
                    f"Error: unknown metric {metric!r} for flow {flow!r}; "
                    f"must be one of {valid_names!r}",
                    err=True,
                )
                raise typer.Exit(code=2)
            if output.should_nudge_stderr:
                typer.echo(NON_TTY_NUDGE, err=True)
            commit_log = build_commit_log()
            typer.echo(
                render_metric_detail(
                    verdict,
                    metric,
                    rc,
                    commit_log,
                    flow_name=flow,
                    mode=mode,
                    color=output.color_enabled,
                )
            )
        else:
            if output.should_nudge_stderr:
                typer.echo(NON_TTY_NUDGE, err=True)
            commit_log = build_commit_log()
            typer.echo(
                render_summary(
                    verdict,
                    rc,
                    commit_log,
                    flow_name=flow,
                    verbose=verbose,
                    color=output.color_enabled,
                )
            )
    except typer.Exit:
        raise
    except Exception as exc:
        # runtime failure, never exit 1 (this guarded block never lets an
        # exception escape uncaught).
        typer.echo(f"Error: failed to render output for {flow!r}: {exc}", err=True)
        raise typer.Exit(code=3) from None

    # ALWAYS render before mapping the gate to an exit code (decision D3):
    # `1` fires ONLY from a confirmed `gate_status == "fail"`.
    raise typer.Exit(code=1 if verdict.gate_status == GATE_FAIL else 0)
