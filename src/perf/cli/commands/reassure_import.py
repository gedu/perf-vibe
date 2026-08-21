"""`perf reassure-import [path]` — typer command wiring the config loader +
adapter registry into `ReassureParser.parse()` then `Store.save_reassure_import`
(design "Registry / CLI", spec "purpose": "a flat CLI command calling ports
directly, `cli/commands/compare.py:28,53-80`-style"). Two sequential port
calls, no `application/` use-case — mirrors `compare.py`'s shape exactly.

FLAT command, not a `add_typer` sub-app: `markers` only got a nested sub-app
because TWO subcommands (`snippet`/`doctor`) shipped together; this change
ships exactly one command.

Call order is deliberate (design "Architecture Decisions" > "Call order"):
parse FIRST, store SECOND — a bad path must never create a SQLite file, and
this keeps the usage-error exit (`2`) checked ahead of the runtime/tooling
exit (`3`).

Exit codes (spec "Exit-Code Discipline", NEVER `1`): `2` missing/unreadable
path (`ReassureParseError`); `0` otherwise — including a byte-identical
duplicate re-import (`already_imported: true`, every `*_imported` counter
`0`) and a readable file with zero recovered entries; `3` a store/
transaction/render failure or any other unexpected exception.

Per-line skip detail is STDERR-only (spec "reassure_import_v1 --json
Contract": "all warnings go to stderr only"): `ReassureParseResult.skipped`
feeds one bounded `emit_warning` per line (line number + fixed reason
token, NEVER the raw line), and `entries_skipped` in the machine payload
carries only the COUNT — this is why `skipped`/`diagnostic` exist on the
domain result at all even though the flat `--json` contract carries
neither.
"""

from __future__ import annotations

from pathlib import Path

import typer

from perf.adapters.reassure_jsonl import ReassureParseError
from perf.adapters.registry import build_reassure_parser, build_store
from perf.cli.output.context import NON_TTY_NUDGE, OutputContext
from perf.cli.output.errors import emit_error, emit_warning
from perf.cli.output.json_reporter import render_json
from perf.config.loader import PerfConfig
from perf.contracts.reassure_import_v1 import build_reassure_import_payload

__all__ = ["derive_reassure_kind", "reassure_import"]

_CURRENT_BASENAME = "current.perf"
_BASELINE_BASENAME = "baseline.perf"

_PATH_ARGUMENT = typer.Argument(
    None,
    help="Path to a reassure `.perf` JSON-Lines file (defaults to the config's `reassure_path`)",
)
_KIND_OPTION = typer.Option(
    None,
    "--kind",
    help="Override the derived import kind (`current`|`baseline`|`unknown`)",
)


def derive_reassure_kind(path: str) -> str:
    """Pure, unit-testable derivation of `reassure_import.kind` from the
    file's BASENAME alone (never the directory): `current.perf` ->
    `'current'`, `baseline.perf` -> `'baseline'`, anything else ->
    `'unknown'`. `--kind` overrides this at the call site — see
    `reassure_import` below."""

    basename = Path(path).name
    if basename == _CURRENT_BASENAME:
        return "current"
    if basename == _BASELINE_BASENAME:
        return "baseline"
    return "unknown"


def _close_store(store: object) -> None:
    if store is not None and hasattr(store, "close"):
        try:
            store.close()
        except Exception as close_exc:
            # A close failure must NEVER override the already-computed
            # exit code (SKILL rule 7: never exit 1) — mirrors
            # `compare.py._close_store`.
            typer.echo(f"warning: failed to close store: {close_exc}", err=True)


def _render_import_pretty(payload: dict) -> str:
    lines = [
        f"path: {payload['path']}",
        f"kind: {payload['kind']}",
        f"content_hash: {payload['content_hash']}",
        f"already_imported: {payload['already_imported']}",
        f"entries_imported: {payload['entries_imported']}",
        f"entries_skipped: {payload['entries_skipped']}",
        f"duration_samples_imported: {payload['duration_samples_imported']}",
        f"count_samples_imported: {payload['count_samples_imported']}",
    ]
    return "\n".join(lines) + "\n"


def reassure_import(
    ctx: typer.Context,
    path: str | None = _PATH_ARGUMENT,
    kind: str | None = _KIND_OPTION,
) -> None:
    """Parse a `@callstack/reassure` `.perf` JSON-Lines file and persist it
    idempotently. Stores data and prints a confirmation only — it judges,
    compares, and gates nothing (spec "Purpose")."""

    state: dict = ctx.obj or {}
    output: OutputContext = state["output"]
    config: PerfConfig = state["config"]

    resolved_path = path or config.reassure_path
    resolved_kind = kind if kind is not None else derive_reassure_kind(resolved_path)

    try:
        result = build_reassure_parser().parse(resolved_path)
    except ReassureParseError as exc:
        emit_error(output, str(exc), hint="check the path exists and is readable")
        raise typer.Exit(code=2) from None

    store = None
    try:
        store = build_store(config.db_path)
        import_id = store.save_reassure_import(result, resolved_path, resolved_kind)
    except ValueError as exc:
        # `kind` failed the store's own adapter-boundary validation — a
        # usage error caused by `--kind`, not a runtime/store failure.
        emit_error(output, str(exc), hint="`--kind` must be `current`, `baseline`, or `unknown`")
        raise typer.Exit(code=2) from None
    except Exception as exc:
        emit_error(output, f"failed to persist reassure import: {exc}")
        raise typer.Exit(code=3) from None
    finally:
        _close_store(store)

    already_imported = import_id is None
    entries_imported = 0 if already_imported else len(result.entries)
    duration_samples_imported = (
        0 if already_imported else sum(len(entry.durations) for entry in result.entries)
    )
    count_samples_imported = (
        0 if already_imported else sum(len(entry.counts) for entry in result.entries)
    )

    for line_number, reason in result.skipped:
        emit_warning(output, f"line {line_number}: skipped ({reason})")
    if entries_imported == 0 and not already_imported:
        emit_warning(output, result.diagnostic or "no entries recovered from this file")

    payload = build_reassure_import_payload(
        path=resolved_path,
        content_hash=result.content_hash,
        kind=resolved_kind,
        already_imported=already_imported,
        entries_imported=entries_imported,
        entries_skipped=len(result.skipped),
        duration_samples_imported=duration_samples_imported,
        count_samples_imported=count_samples_imported,
    )

    try:
        if output.json_mode:
            typer.echo(render_json(payload))
        else:
            if output.should_nudge_stderr:
                typer.echo(NON_TTY_NUDGE, err=True)
            typer.echo(_render_import_pretty(payload))
    except Exception as exc:
        emit_error(output, f"failed to render output: {exc}")
        raise typer.Exit(code=3) from None

    raise typer.Exit(code=0)
