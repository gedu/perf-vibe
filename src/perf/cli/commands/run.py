"""`perf run <flow> [n]` — typer command wiring the config loader +
adapter registry into `RunFlowUseCase`, then dispatching to the pretty or
`--json` reporter (SKILL rule 6). Exit codes per SKILL rule 7: `0`
success, `2` usage error, `3` runtime/tooling error — this command NEVER
lets an exception escape as Python's default exit code `1`."""

from __future__ import annotations

import os
from pathlib import Path

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
from perf.cli.output.errors import (
    emit_error,
    emit_warning,
    hint_for_diagnostics,
    salient_tool_line,
)
from perf.cli.output.json_reporter import render_json
from perf.cli.output.pretty import render_confirmation
from perf.cli.output.progress import build_progress_reporter
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
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress all progress, relayed tool output, and the end recap "
        "on stderr (errors are still reported).",
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
        emit_error(
            output,
            f"unknown flow {flow!r}; must be one of the config-known "
            f"flows {sorted(config.flows)!r}",
        )
        raise typer.Exit(code=2)

    # FIX 2 (`run-live-progress` Slice D post-review): a fully-silent
    # `--quiet` manual driver is contradictory — `ManualDriver.drive()`
    # surfaces its per-iteration prompt ONLY via
    # `reporter.awaiting_user_input(...)`, which `NullProgressReporter`
    # (what `--quiet` wires up) makes a no-op, so the run would block on
    # `input()` with NO visible prompt: a silent, undiagnosable hang. Reject
    # this combination up front as a usage error (exit 2, never 1 — SKILL
    # rule 7), before any driver is built or interacted with, rather than
    # let it hang.
    if config.driver == "manual" and quiet:
        emit_error(
            output,
            "--quiet cannot be used with the manual driver: it must prompt "
            "you before each iteration. Re-run without --quiet.",
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
        reporter = build_progress_reporter(
            quiet=quiet,
            stderr_is_tty=output.stderr_is_tty,
            error_color_enabled=output.error_color_enabled,
        )
        driver = build_driver(
            config.driver,
            known_flows=known_flows,
            device=resolved_device,
            flow_prompts=flow_prompts,
            reporter=reporter,
            replay_logcat=config.replay_logcat,
            replay_flashlight=config.replay_flashlight,
        )
        sampler = build_sampler(config.sampler, bundle_id=config.bundle_id)
        if sampler is not None:
            # `RunFlowUseCase`/the sampler adapter only ever compose the
            # results path as a PURE string (application layer does no I/O)
            # and Flashlight itself `writeFileSync`s straight into it — if
            # `results_dir` doesn't exist yet, that write crashes with
            # `ENOENT`, on BOTH a failing and a successful run. This CLI
            # composition root already owns `config.results_dir` I/O
            # elsewhere, so it creates the directory here, before any device
            # interaction. A failure to create it (e.g. permissions) is a
            # runtime/tooling failure — exit 3, never Python's default
            # exit 1 (SKILL rule 7) — not a usage error.
            try:
                Path(config.results_dir).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                emit_error(
                    output,
                    f"failed to create results directory `{config.results_dir}`: {exc}",
                )
                raise typer.Exit(code=3) from None
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

        # `run-live-progress` cleanup fix (post-review): the TOOL_MANAGED
        # framing header used to be emitted from inside
        # `MaestroDriver._drive_tool_managed` via `relayed_line`. This CLI
        # layer already holds the concrete reporter and knows `flow`/
        # `resolved_iterations` — so it calls the non-indented
        # `reporter.run_header(...)` directly, inside this SAME try block so
        # an unexpected failure still maps to exit 3, never Python's default
        # exit 1 (SKILL rule 7), and never touches stdout.
        #
        # Task 4 fix: key the header off the BUILT sampler (the object the
        # registry produced), never the config adapter NAME — a `sampler =
        # "none"` marker-only run has no sampler and must show no
        # tool-managed header, while a present sampler owns the iteration
        # loop (TOOL_MANAGED) and frames the streamed output.
        if sampler is not None:
            reporter.run_header(flow, resolved_iterations)

        result = use_case.execute(request)
    except UsageError as exc:
        emit_error(output, str(exc))
        raise typer.Exit(code=2) from None
    except ValueError as exc:
        # An unknown/invalid adapter name from the registry (e.g. a typo in
        # perfvibe.toml `driver = "maestr"`) is a configuration/usage error →
        # exit 2, NOT a runtime/tooling failure (exit 3).
        emit_error(output, str(exc))
        raise typer.Exit(code=2) from None
    except RunFailedError as exc:
        # The raw `diagnostics` is often a huge multi-line tool stderr (e.g.
        # Flashlight dumping a Node stack trace on an adb failure) — surface
        # the ONE meaningful line as `cause` and, when the signature is
        # recognized, an actionable `hint`, instead of the whole blob.
        emit_error(
            output,
            str(exc),
            cause=salient_tool_line(exc.diagnostics),
            hint=hint_for_diagnostics(exc.diagnostics),
        )
        raise typer.Exit(code=3) from None
    except typer.Exit:
        # `typer.Exit` is (via `RuntimeError`) an `Exception` subclass, so
        # without this it would fall into the generic handler below and be
        # double-reported. Only code raised INSIDE this try body deliberately
        # (e.g. the results-directory mkdir guard above, which already
        # called `emit_error` itself) reaches here — let it propagate as-is.
        raise
    except Exception as exc:
        # NEVER exit 1 (SKILL rule 7). Any unexpected exception (a bug, an
        # adapter surprise) is still a runtime/tooling failure, not a usage
        # error — map it to exit 3 rather than let Python's default
        # traceback/exit-1 escape.
        emit_error(output, f"unexpected failure running {flow!r}: {exc}")
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
        # Slice C (`run-live-progress`): recap renders ONCE, on the success
        # path only, straight after `execute()` returned — a failure never
        # reaches here (the earlier `except RunFailedError`/`except
        # Exception` blocks already exited). STDERR-only, so it is
        # orthogonal to the `--json`/pretty STDOUT payload rendered right
        # after it; guarded in the SAME try/except as that rendering so an
        # unexpected recap failure maps to exit 3 like any other output
        # failure, never Python's default exit 1 (SKILL rule 7).
        reporter.recap(result)
        # A configured marker source that captured zero/partial markers is
        # surfaced as a WARNING (never silently) so the user can dig — on
        # STDERR (stdout stays byte-pure for --json), shown even under
        # --quiet since a warning is not progress chrome. The run still
        # succeeded (samples persisted), so this never changes the exit code.
        if result.marker_diagnostic:
            emit_warning(output, f"markers: {result.marker_diagnostic}")
        if output.json_mode:
            payload = build_run_payload(result)
            typer.echo(render_json(payload))
        else:
            # FIX 1 (`run-live-progress` Slice D post-review): this nudge is
            # progress-class chrome, not an error/warning — `--quiet` must
            # suppress it too, same as it suppresses the header/relay/recap
            # above. `emit_error`/the store-close warning are UNCHANGED —
            # only progress-class output + this nudge are gated on `quiet`.
            if output.should_nudge_stderr and not quiet:
                typer.echo(NON_TTY_NUDGE, err=True)
            typer.echo(render_confirmation(result, color=output.color_enabled))
    except Exception as exc:
        # guarded block; an output failure is still a runtime failure, never
        # exit 1 (SKILL rule 7).
        emit_error(output, f"failed to render output for {flow!r}: {exc}")
        raise typer.Exit(code=3) from None

    raise typer.Exit(code=0)
