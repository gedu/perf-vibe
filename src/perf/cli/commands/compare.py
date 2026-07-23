"""`perf compare <flow>` — typer command wiring the config loader + adapter
registry into `SqlAnalyzer.compare_latest`, then dispatching to the pretty
or `--json` renderer (SKILL rule 6). Reads only — writes nothing new.

Exit codes (SKILL rule 7, refined by decision #53 for this compare-only
slice): `0` success — a verdict was computed and shown, WHATEVER it is
(including `regression`, which is purely INFORMATIONAL here; the CI-gating
exit `1` belongs to the DEFERRED `budget-check` follow-up and never
appears in this command); `2` usage error (unknown flow, or a config-known
flow with no recorded history at all for the flow/device/mode
combination); `3` runtime/tooling error. This command NEVER lets an
exception escape as Python's default exit code `1`.
"""

from __future__ import annotations

from typing import Optional

import typer

from perf.adapters.registry import build_analyzer, build_context_provider, build_store
from perf.cli.output.compare_pretty import render_compare
from perf.cli.output.context import NON_TTY_NUDGE, OutputContext
from perf.cli.output.json_reporter import render_json
from perf.config.loader import PerfConfig
from perf.contracts.compare_v1 import build_compare_payload

__all__ = ["compare"]


def compare(
    ctx: typer.Context,
    flow: str = typer.Argument(..., help="Config-known flow name to compare"),
    restart: bool = typer.Option(
        False, "--restart", help="Compare the cold series (default: warm — matches `perf run`)"
    ),
    device: Optional[str] = typer.Option(
        None, "--device", help="Pin a device serial (overrides MAESTRO_DEVICE/config)"
    ),
) -> None:
    """Compare the latest persisted run for `<flow>` against its recent
    history: a direction-aware, per-metric verdict plus the always-on
    config sanity label (decision #58). This command performs NO
    device/subprocess I/O of its own — it only reads the local store."""

    state: dict = ctx.obj or {}
    output: OutputContext = state["output"]
    config: PerfConfig = state["config"]

    # SKILL rule 5 (usage-error-before-work, mirroring `run`'s flow-name
    # guard): an unknown flow is a usage error, not a runtime failure —
    # checked before any store/analyzer construction (corner case C2).
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
    try:
        # `device_key` is derived the SAME way `run` derives it for a
        # persisted run (`BashRunContextProvider`, degrading gracefully to
        # `unknown|unknown|physical` with no device/adb attached) — never a
        # separate identifier scheme, so `compare` matches whatever `run`
        # actually persisted, device-free demos included.
        context_provider = build_context_provider(
            build_variant=config.build_variant,
            tool_version=config.tool_version,
            device=resolved_device,
        )
        device_key = context_provider.context().device_key

        store = build_store(config.db_path)
        analyzer = build_analyzer(
            store,
            threshold_pct=config.threshold_pct,
            floors=config.floors,
            min_baseline_commits=config.min_baseline_commits,
            warmup_k=config.warmup_k,
            baseline_n=config.baseline_n,
        )

        result = analyzer.compare_latest(flow, device_key, mode)
    except Exception as exc:  # noqa: BLE001 — last-resort guard: `compare`
        # must NEVER exit 1 (SKILL rule 7 / decision #53 — exit 1 is
        # DEFERRED to `budget-check`). Any unexpected exception is a
        # runtime/tooling failure, never a usage error.
        typer.echo(f"Error: unexpected failure comparing {flow!r}: {exc}", err=True)
        raise typer.Exit(code=3) from None
    finally:
        if store is not None and hasattr(store, "close"):
            try:
                store.close()
            except Exception as close_exc:  # noqa: BLE001 — a close()
                # failure must NEVER override the computed exit code
                # (SKILL rule 7: never exit 1).
                typer.echo(f"warning: failed to close store: {close_exc}", err=True)

    if result is None:
        # No runs at all for this flow/device/mode (corner cases C2/C7) —
        # a usage error, not a runtime failure (spec "Unknown flow is a
        # usage error": "a flow name with no history").
        typer.echo(
            f"Error: no history for flow {flow!r} (device={device_key!r}, mode={mode!r})",
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        if output.json_mode:
            payload = build_compare_payload(result)
            typer.echo(render_json(payload))
        else:
            if output.should_nudge_stderr:
                typer.echo(NON_TTY_NUDGE, err=True)
            typer.echo(render_compare(result, color=output.color_enabled))
    except Exception as exc:  # noqa: BLE001 — rendering runs outside the
        # main guarded block; an output failure is still a runtime
        # failure, never exit 1 (SKILL rule 7).
        typer.echo(f"Error: failed to render output for {flow!r}: {exc}", err=True)
        raise typer.Exit(code=3) from None

    raise typer.Exit(code=0)
