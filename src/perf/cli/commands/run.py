"""`perf run <flow> [n]` — typer command wiring the config loader +
adapter registry into `RunFlowUseCase`, then dispatching to the pretty or
`--json` reporter (SKILL rule 6). Exit codes per SKILL rule 7: `0`
success, `2` usage error, `3` runtime/tooling error — this command NEVER
lets an exception escape as Python's default exit code `1`."""

from __future__ import annotations

import os

import typer

from perf.adapters.registry import (
    build_clock,
    build_context_provider,
    build_driver,
    build_marker_source,
    build_sampler,
    build_store,
)
from perf.application.run_flow import (
    RunFailedError,
    RunFlowRequest,
    RunFlowUseCase,
    UsageError,
)
from perf.cli.output.context import NON_TTY_NUDGE, OutputContext
from perf.cli.output.json_reporter import render_json
from perf.cli.output.pretty import render_confirmation
from perf.config.loader import PerfConfig
from perf.contracts.json_v1 import build_run_payload

__all__ = ["run"]


def run(
    ctx: typer.Context,
    flow: str = typer.Argument(..., help="Config-known flow name to run"),
    iterations: int | None = typer.Option(
        None,
        "--iterations",
        "-n",
        min=1,
        help="Number of iterations (default: from config, else 10)",
    ),
    restart: bool = typer.Option(False, "--restart", help="Force a cold run (default: warm)"),
    device: str | None = typer.Option(
        None, "--device", help="Pin a device serial (overrides MAESTRO_DEVICE/config)"
    ),
) -> None:
    """Drive a config-known flow N times, capture measurements, and
    persist exactly one run."""

    state: dict = ctx.obj or {}
    output: OutputContext = state["output"]
    config: PerfConfig = state["config"]

    # SKILL rule 5: `flow_name` MUST be validated against config-known flows
    # BEFORE any driver invocation — for EVERY driver, not just Maestro's
    # own internal check (`ManualDriver.command()` never rejects an
    # unknown flow name by itself, so this CLI-level guard is what actually
    # enforces the requirement uniformly).
    if flow not in config.flows:
        typer.echo(
            f"Error: unknown flow {flow!r}; must be one of the config-known "
            f"flows {sorted(config.flows)!r}",
            err=True,
        )
        raise typer.Exit(code=2)

    resolved_device = device or config.device
    resolved_iterations = iterations if iterations is not None else config.default_iterations

    known_flows = {name: (fc.maestro_path or name) for name, fc in config.flows.items()}
    flow_prompts = {
        name: prompt for name, fc in config.flows.items() if (prompt := getattr(fc, "prompt", None))
    }

    # The secret is read ONLY from the environment — never a CLI flag — so it
    # never lands in shell history or `ps`/`/proc/<pid>/cmdline`. It still
    # reaches the driver's --env mechanism and never touches --json/DB/logs.
    password = os.environ.get("PASSWORD")
    env = {"PASSWORD": password} if password else None

    store = None
    try:
        driver = build_driver(
            config.driver,
            known_flows=known_flows,
            device=resolved_device,
            flow_prompts=flow_prompts,
            replay_logcat=config.replay_logcat,
            replay_flashlight=config.replay_flashlight,
        )
        sampler = build_sampler(config.sampler)
        marker_source = build_marker_source(config.marker_source, device=resolved_device)
        context_provider = build_context_provider(
            build_variant=config.build_variant,
            tool_version=config.tool_version,
            device=resolved_device,
        )
        store = build_store(config.db_path)
        clock = build_clock()

        use_case = RunFlowUseCase(
            driver=driver,
            sampler=sampler,
            marker_source=marker_source,
            context_provider=context_provider,
            store=store,
            clock=clock,
        )

        request = RunFlowRequest(
            flow_name=flow,
            iterations=resolved_iterations,
            restart=restart,
            env=env,
            results_dir=config.results_dir if sampler is not None else None,
        )

        result = use_case.execute(request)
    except UsageError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from None
    except ValueError as exc:
        # An unknown/invalid adapter name from the registry (e.g. a typo in
        # perf.toml `driver = "maestr"`) is a configuration/usage error →
        # exit 2, NOT a runtime/tooling failure (exit 3).
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from None
    except RunFailedError as exc:
        typer.echo(f"Error: {exc}", err=True)
        if exc.diagnostics:
            typer.echo(f"  diagnostics: {exc.diagnostics}", err=True)
        raise typer.Exit(code=3) from None
    except Exception as exc:
        # NEVER exit 1 (SKILL rule 7). Any unexpected exception (a bug, an
        # adapter surprise) is still a runtime/tooling failure, not a usage
        # error — map it to exit 3 rather than let Python's default
        # traceback/exit-1 escape.
        typer.echo(f"Error: unexpected failure running {flow!r}: {exc}", err=True)
        raise typer.Exit(code=3) from None
    finally:
        if store is not None and hasattr(store, "close"):
            try:
                store.close()
            except Exception as close_exc:
                # must NEVER override the computed exit code (SKILL rule 7:
                # never exit 1). Report it, but do not let it escape.
                typer.echo(f"warning: failed to close store: {close_exc}", err=True)

    try:
        if output.json_mode:
            payload = build_run_payload(result)
            typer.echo(render_json(payload))
        else:
            if output.should_nudge_stderr:
                typer.echo(NON_TTY_NUDGE, err=True)
            typer.echo(render_confirmation(result, color=output.color_enabled))
    except Exception as exc:
        # guarded block; an output failure is still a runtime failure, never
        # exit 1 (SKILL rule 7).
        typer.echo(f"Error: failed to render output for {flow!r}: {exc}", err=True)
        raise typer.Exit(code=3) from None

    raise typer.Exit(code=0)
