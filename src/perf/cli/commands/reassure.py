"""`reassure` sub-app (`cli/main.py`, `add_typer` pattern, D1) — mirrors
`markers_app` (`markers.py:324-328`, `:440-448`) exactly: one shared
`typer.Typer` instance with per-subcommand `context_settings`, each
subcommand registered via `.command(name=..., ...)`.

`import` registers the EXISTING `reassure_import` function object
(`cli/commands/reassure_import.py`) — no wrapper, no partial, no copy
(design "Sub-App Wiring", A10). The flat `reassure-import` registration in
`cli/main.py` carries the SAME function object plus the deprecation
annotation; only THAT registration ever prints the native Click notice.

`list` (PR1b) is the first subcommand this module defines itself:
`store.reassure_imports(limit)` -> `build_reassure_list_payload` for
`--json`, `render_reassure_list` for the pretty view (spec "reassure
list — Import Roster", D2 ordering).

`entries` (this slice, PR1c) reports every `reassure_entry` row for ONE
import. Its `import_id` argument needs its OWN existence check —
`store.reassure_import_exists(import_id)` — before calling
`store.reassure_entries(import_id)`: the latter returns an empty sequence
for BOTH an unknown `import_id` and a real import with zero entries, and
the spec requires different exit codes for each (`2` vs `0`). See
`domain/ports.py`'s `Store.reassure_import_exists` docstring for the full
reasoning.
"""

from __future__ import annotations

import typer

from perf.adapters.registry import build_store
from perf.cli.commands.reassure_import import reassure_import
from perf.cli.output.context import NON_TTY_NUDGE, OutputContext
from perf.cli.output.errors import emit_error
from perf.cli.output.json_reporter import render_json
from perf.cli.output.reassure_entries_pretty import render_reassure_entries
from perf.cli.output.reassure_list_pretty import render_reassure_list
from perf.config.loader import PerfConfig
from perf.contracts.reassure_entries_v1 import build_reassure_entries_payload
from perf.contracts.reassure_list_v1 import build_reassure_list_payload

__all__ = ["reassure_app", "reassure_entries_command", "reassure_list"]


class _UnknownReassureImport(Exception):
    """Internal control-flow signal ONLY — never escapes this module.
    Raised when `import_id` matches no persisted `reassure_import` row, to
    route that case to the usage-error branch (exit `2`) and keep it
    distinct from a genuine store/runtime failure (exit `3`). Deliberately
    NOT `ValueError`: a real `ValueError` from the store (e.g. a future
    validation bug) must still surface as exit `3`, not be silently
    reinterpreted as "unknown import"."""


_CTX = {"help_option_names": ["--help", "-h"]}

reassure_app = typer.Typer(
    add_completion=False,
    context_settings=_CTX,
    help="Read, chart and compare persisted @callstack/reassure measurements.",
)

_LIMIT_OPTION = typer.Option(
    50, "--limit", help="Most recent N imports to include (newest→oldest; D2 ordering)"
)

_IMPORT_ID_ARGUMENT = typer.Argument(
    help="A `reassure_import.import_id` from `perfvibe reassure list` (unknown id exits 2)"
)


def _close_store(store: object) -> None:
    if store is not None and hasattr(store, "close"):
        try:
            store.close()
        except Exception as close_exc:
            # A close failure must NEVER override the already-computed exit
            # code (SKILL rule 7: never exit 1) — mirrors `history.py`.
            typer.echo(f"warning: failed to close store: {close_exc}", err=True)


def reassure_list(
    ctx: typer.Context,
    limit: int = _LIMIT_OPTION,
) -> None:
    """Reports the import roster, ordered per D2 (most recent first): an
    import identifier, `created_date`/`imported_at`, `branch`,
    `commit_hash` (a LABEL only), and each import's entry count (spec
    "reassure list — Import Roster"). Read-only. Exit `0` always (including
    an empty roster) — this command reports and does not gate."""

    state: dict = ctx.obj or {}
    output: OutputContext = state["output"]
    config: PerfConfig = state["config"]

    store = None
    try:
        store = build_store(config.db_path)
        imports = store.reassure_imports(limit)
    except Exception as exc:
        emit_error(output, f"unexpected failure reading the reassure import roster: {exc}")
        raise typer.Exit(code=3) from None
    finally:
        _close_store(store)

    try:
        if output.json_mode:
            payload = build_reassure_list_payload(imports=imports)
            typer.echo(render_json(payload))
        else:
            if output.should_nudge_stderr:
                typer.echo(NON_TTY_NUDGE, err=True)
            typer.echo(render_reassure_list(imports, color=output.color_enabled))
    except Exception as exc:
        emit_error(output, f"failed to render reassure list output: {exc}")
        raise typer.Exit(code=3) from None

    raise typer.Exit(code=0)


def reassure_entries_command(
    ctx: typer.Context,
    import_id: int = _IMPORT_ID_ARGUMENT,
) -> None:
    """Reports every `reassure_entry` row for one import: `name`,
    `entry_type`, the declared `runs`, and each series' independently
    reduced duration/count summary (spec "reassure entries <import-id> —
    One Import's Entries"). Read-only. An unknown `import_id` is a usage
    error (exit `2`, no `--json` payload); a real import with zero
    entries is a valid state (exit `0`, an empty list) — the two are never
    conflated."""

    state: dict = ctx.obj or {}
    output: OutputContext = state["output"]
    config: PerfConfig = state["config"]

    store = None
    try:
        store = build_store(config.db_path)
        if not store.reassure_import_exists(import_id):
            raise _UnknownReassureImport(import_id)
        entries = store.reassure_entries(import_id)
    except _UnknownReassureImport:
        emit_error(
            output,
            f"no reassure import with id {import_id}",
            hint="see `perfvibe reassure list` for known import ids",
        )
        raise typer.Exit(code=2) from None
    except Exception as exc:
        emit_error(output, f"unexpected failure reading reassure entries: {exc}")
        raise typer.Exit(code=3) from None
    finally:
        _close_store(store)

    try:
        if output.json_mode:
            payload = build_reassure_entries_payload(import_id=import_id, entries=entries)
            typer.echo(render_json(payload))
        else:
            if output.should_nudge_stderr:
                typer.echo(NON_TTY_NUDGE, err=True)
            typer.echo(render_reassure_entries(import_id, entries, color=output.color_enabled))
    except Exception as exc:
        emit_error(output, f"failed to render reassure entries output: {exc}")
        raise typer.Exit(code=3) from None

    raise typer.Exit(code=0)


reassure_app.command(name="import", context_settings=_CTX)(reassure_import)
reassure_app.command(name="list", context_settings=_CTX)(reassure_list)
reassure_app.command(name="entries", context_settings=_CTX)(reassure_entries_command)
