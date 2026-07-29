"""`perf compare <flow>...` — typer command wiring the config loader +
adapter registry into `SqlAnalyzer.compare_latest`, then dispatching to the
pretty or `--json` renderer (SKILL rule 6). Reads only — writes nothing new.

Accepts ONE flow (byte-identical to the original single-flow behavior —
exit codes, pretty output, and the `compare_v1` `--json` payload are
unchanged), MANY flows, `--all` (every config-known flow, sorted), or — on
an interactive TTY with no flow args — an fzf-style picker
(`cli/output/flow_picker*`). The multi-flow `--json` form emits the
`compare_all_v1` envelope; single-flow keeps the plain `compare_v1` payload.

Exit codes (SKILL rule 7, refined by decision #53 for this compare-only
slice): `0` success — a verdict was computed and shown, WHATEVER it is
(including `regression`, which is purely INFORMATIONAL here; the CI-gating
exit `1` belongs to `budget-check` and never appears here); `2` usage error
(unknown flow, a config-known flow with no recorded history, `--all` mixed
with explicit flows, or a no-arg invocation in a non-interactive/`--json`
context); `3` runtime/tooling error. This command NEVER lets an exception
escape as Python's default exit code `1`.
"""

from __future__ import annotations

import sys

import typer

from perf.adapters.registry import build_analyzer, build_context_provider, build_store
from perf.adapters.store_sqlite import SqliteStore
from perf.cli.output.compare_pretty import render_compare, render_flow_header
from perf.cli.output.context import NON_TTY_NUDGE, OutputContext
from perf.cli.output.errors import emit_error, emit_warning
from perf.cli.output.flow_picker_terminal import PickerUnavailable, pick_flows
from perf.cli.output.json_reporter import render_json
from perf.config.loader import PerfConfig
from perf.contracts.compare_all_v1 import build_compare_all_payload
from perf.contracts.compare_v1 import build_compare_payload
from perf.domain.model import CompareResult
from perf.domain.ports import Analyzer

__all__ = ["compare"]

_PICK_HINT = "pass a flow name or `--all`"

# Module-level singleton (ruff B008): a `list[str]`-annotated typer default
# must not be a call in the signature's defaults, so the `Argument` is built
# here once and referenced below.
_FLOWS_ARGUMENT = typer.Argument(
    None, help="Config-known flow name(s) to compare (omit for an interactive picker)"
)


def compare(
    ctx: typer.Context,
    flows: list[str] | None = _FLOWS_ARGUMENT,
    all_flows: bool = typer.Option(
        False, "--all", help="Compare every config-known flow (sorted); not with explicit flows"
    ),
    restart: bool = typer.Option(
        False, "--restart", help="Compare the cold series (default: warm — matches `perf run`)"
    ),
    device: str | None = typer.Option(
        None, "--device", help="Pin a device serial (overrides MAESTRO_DEVICE/config)"
    ),
    device_key: str | None = typer.Option(
        None,
        "--device-key",
        help="Use this device key verbatim (e.g. 'Pixel 8|Android 14|physical'); "
        "skips live-adb derivation and the last-recorded-key fallback",
    ),
) -> None:
    """Compare the latest persisted run for each selected `<flow>` against its
    recent history: a direction-aware, per-metric verdict plus the always-on
    config sanity label (decision #58). Performs NO device/subprocess I/O of
    its own — it only reads the local store."""

    state: dict = ctx.obj or {}
    output: OutputContext = state["output"]
    config: PerfConfig = state["config"]

    requested = list(flows or [])
    selected, force_multi = _resolve_selection(output, config, requested, all_flows=all_flows)

    resolved_device = device or config.device
    mode = "cold" if restart else "warm"

    if force_multi or len(selected) >= 2:
        _run_multi(
            output,
            config,
            selected,
            mode=mode,
            resolved_device=resolved_device,
            device_key_option=device_key,
        )
    else:
        _run_single(
            output,
            config,
            selected[0],
            mode=mode,
            resolved_device=resolved_device,
            device_key_option=device_key,
        )


def _resolve_selection(
    output: OutputContext,
    config: PerfConfig,
    requested: list[str],
    *,
    all_flows: bool,
) -> tuple[list[str], bool]:
    """Resolve which flows to compare, returning `(selected, force_multi)`.
    `force_multi` is `True` for `--all` so the multi-flow view/envelope is
    used even when the config happens to define a single flow. Raises
    `typer.Exit` for every usage/cancel path so the caller stays linear."""

    # `--all` is mutually exclusive with explicit flow args (usage error).
    if all_flows and requested:
        emit_error(output, "pass either flow name(s) or `--all`, not both")
        raise typer.Exit(code=2)

    if all_flows:
        selected = sorted(config.flows)
        if not selected:
            emit_error(output, "`--all` was given but the config defines no flows")
            raise typer.Exit(code=2)
        return selected, True

    if requested:
        # Unknown flow names are a usage error, checked UPFRONT against the
        # config before any store/analyzer work (SKILL rule 5, corner case
        # C2) — for every requested flow, not just the first.
        unknown = [name for name in requested if name not in config.flows]
        if unknown:
            emit_error(
                output,
                f"unknown flow {unknown[0]!r}; must be one of the config-known "
                f"flows {sorted(config.flows)!r}",
            )
            raise typer.Exit(code=2)
        return requested, False

    return _select_interactively(output, config), False


def _picker_available(output: OutputContext) -> bool:
    """The interactive picker needs an interactive stdout AND stdin (it reads
    keystrokes and paints to stderr) and must never run in `--json` mode
    (machine contexts stay explicit). Isolated so tests can drive the wiring
    without a real TTY."""

    return output.stdout_is_tty and not output.json_mode and bool(sys.stdin.isatty())


def _select_interactively(output: OutputContext, config: PerfConfig) -> list[str]:
    # No flow args and no `--all`: on a non-interactive stdout/stdin or in
    # `--json` mode, stay explicit — a usage error with a hint (machine
    # contexts must name the flow or `--all`).
    if not _picker_available(output):
        emit_error(output, "no flow given", hint=_PICK_HINT)
        raise typer.Exit(code=2)

    flow_names = tuple(sorted(config.flows))
    try:
        picked = pick_flows(flow_names, color=output.color_enabled)
    except PickerUnavailable:
        # Raw mode could not be enabled — fall back to the same explicit
        # usage-error path rather than crashing.
        emit_error(output, "no flow given", hint=_PICK_HINT)
        raise typer.Exit(code=2) from None

    if not picked:
        # Esc/Ctrl-C (or an accepted-but-empty selection): the user's choice,
        # not an error (spec 'Esc or Ctrl-C: cancel — exit 0').
        emit_warning(output, "no flow selected")
        raise typer.Exit(code=0)
    return picked


def _resolve_device_key(
    config: PerfConfig,
    *,
    device_key_option: str | None,
    resolved_device: str | None,
) -> str:
    """The device key to query history for. `--device-key` wins verbatim
    (resilience batch, Task 2) — NO live-adb derivation, so `compare`/
    `budget-check` work with no device attached. Otherwise derive it the
    SAME way `run` does (`BashRunContextProvider`, degrading gracefully to
    `unknown|unknown|physical`), keeping `compare` matched to whatever `run`
    persisted."""

    if device_key_option is not None:
        return device_key_option
    context_provider = build_context_provider(
        build_variant=config.build_variant,
        tool_version=config.tool_version,
        device=resolved_device,
    )
    return context_provider.context().device_key


def _compare_flow_with_fallback(
    output: OutputContext,
    store: SqliteStore,
    analyzer: Analyzer,
    flow: str,
    *,
    device_key: str,
    mode: str,
    device_key_explicit: bool,
) -> tuple[CompareResult | None, str]:
    """Compare ONE flow's latest run against history WITH the device-key
    fallback (resilience batch, Task 2): when the derived `device_key`
    matches NO history AND it was not pinned via `--device-key`, retry once
    with the most recent persisted device_key for this flow+mode, warning
    that it did so. Returns `(result, device_key_used)`. Shared by the
    single-, multi-, and picker-driven compare paths so ALL get the
    fallback (never just the single-flow one)."""

    result = analyzer.compare_latest(flow, device_key, mode)
    if result is not None or device_key_explicit:
        return result, device_key
    fallback = store.latest_device_key(flow, mode)
    if fallback is None or fallback == device_key:
        return result, device_key
    emit_warning(
        output,
        f"no history for derived device key {device_key!r}; "
        f"falling back to last recorded key {fallback!r}",
    )
    return analyzer.compare_latest(flow, fallback, mode), fallback


def _build_analyzer(config: PerfConfig, store: SqliteStore) -> Analyzer:
    return build_analyzer(
        store,
        threshold_pct=config.threshold_pct,
        floors=config.floors,
        min_baseline_commits=config.min_baseline_commits,
        warmup_k=config.warmup_k,
        baseline_n=config.baseline_n,
        adaptive_floor=config.adaptive_floor,
    )


def _run_single(
    output: OutputContext,
    config: PerfConfig,
    flow: str,
    *,
    mode: str,
    resolved_device: str | None,
    device_key_option: str | None = None,
) -> None:
    store = None
    try:
        device_key = _resolve_device_key(
            config, device_key_option=device_key_option, resolved_device=resolved_device
        )

        store = build_store(config.db_path)
        analyzer = _build_analyzer(config, store)
        result, device_key = _compare_flow_with_fallback(
            output,
            store,
            analyzer,
            flow,
            device_key=device_key,
            mode=mode,
            device_key_explicit=device_key_option is not None,
        )
    except Exception as exc:
        # must NEVER exit 1 (SKILL rule 7 / decision #53 — exit 1 is
        # DEFERRED to `budget-check`). Any unexpected exception is a
        # runtime/tooling failure, never a usage error.
        emit_error(output, f"unexpected failure comparing {flow!r}: {exc}")
        raise typer.Exit(code=3) from None
    finally:
        _close_store(store)

    if result is None:
        # No runs at all for this flow/device/mode (corner cases C2/C7) —
        # a usage error, not a runtime failure (spec "Unknown flow is a
        # usage error": "a flow name with no history").
        emit_error(
            output,
            f"no history for flow {flow!r} (device={device_key!r}, mode={mode!r})",
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
    except Exception as exc:
        # main guarded block; an output failure is still a runtime
        # failure, never exit 1 (SKILL rule 7).
        emit_error(output, f"failed to render output for {flow!r}: {exc}")
        raise typer.Exit(code=3) from None

    raise typer.Exit(code=0)


def _run_multi(
    output: OutputContext,
    config: PerfConfig,
    selected: list[str],
    *,
    mode: str,
    resolved_device: str | None,
    device_key_option: str | None = None,
) -> None:
    store = None
    try:
        device_key = _resolve_device_key(
            config, device_key_option=device_key_option, resolved_device=resolved_device
        )

        store = build_store(config.db_path)
        analyzer = _build_analyzer(config, store)
        # Compare every selected flow up front while the store is open; a
        # `None` result marks a flow with no recorded history (skipped, not
        # fatal). One store/analyzer, reused across flows. Each flow gets the
        # SAME device-key fallback as the single-flow path (Task 2).
        results: list[tuple[str, CompareResult | None]] = [
            (
                flow,
                _compare_flow_with_fallback(
                    output,
                    store,
                    analyzer,
                    flow,
                    device_key=device_key,
                    mode=mode,
                    device_key_explicit=device_key_option is not None,
                )[0],
            )
            for flow in selected
        ]
    except Exception as exc:
        # Any runtime failure over the whole set -> exit 3, never 1.
        emit_error(output, f"unexpected failure comparing {selected!r}: {exc}")
        raise typer.Exit(code=3) from None
    finally:
        _close_store(store)

    if all(result is None for _flow, result in results):
        # EVERY selected flow had no history — degenerate, nothing to show:
        # a usage error (the multi-flow analog of single-flow's exit 2).
        emit_error(
            output,
            f"no history for any of the selected flows {selected!r} "
            f"(device={device_key!r}, mode={mode!r})",
        )
        raise typer.Exit(code=2)

    try:
        if output.json_mode:
            typer.echo(render_json(build_compare_all_payload(results)))
        else:
            _render_multi_pretty(output, results, device_key=device_key, mode=mode)
    except Exception as exc:
        emit_error(output, f"failed to render output for {selected!r}: {exc}")
        raise typer.Exit(code=3) from None

    raise typer.Exit(code=0)


def _render_multi_pretty(
    output: OutputContext,
    results: list[tuple[str, CompareResult | None]],
    *,
    device_key: str,
    mode: str,
) -> None:
    if output.should_nudge_stderr:
        typer.echo(NON_TTY_NUDGE, err=True)
    for flow, result in results:
        if result is None:
            # A no-history flow is non-fatal: warn on stderr and skip it, so
            # one empty flow never kills the whole run (spec).
            emit_warning(
                output,
                f"no history for flow {flow!r} (device={device_key!r}, mode={mode!r}) — skipping",
            )
            continue
        typer.echo(render_flow_header(flow, color=output.color_enabled))
        typer.echo(render_compare(result, color=output.color_enabled))


def _close_store(store: object) -> None:
    if store is not None and hasattr(store, "close"):
        try:
            store.close()
        except Exception as close_exc:
            # failure must NEVER override the computed exit code
            # (SKILL rule 7: never exit 1).
            typer.echo(f"warning: failed to close store: {close_exc}", err=True)
