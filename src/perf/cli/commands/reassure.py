"""`reassure` sub-app (`cli/main.py`, `add_typer` pattern, D1) — mirrors
`markers_app` (`markers.py:324-328`, `:440-448`) exactly: one shared
`typer.Typer` instance with per-subcommand `context_settings`, each
subcommand registered via `.command(name=..., ...)`.

`import` registers the EXISTING `reassure_import` function object
(`cli/commands/reassure_import.py`) — no wrapper, no partial, no copy
(design "Sub-App Wiring", A10). The flat `reassure-import` registration in
`cli/main.py` carries the SAME function object plus the deprecation
annotation; only THAT registration ever prints the native Click notice.

`list` (this slice, PR1b) is the first subcommand this module defines
itself: `store.reassure_imports(limit)` -> `build_reassure_list_payload`
for `--json`, `render_reassure_list` for the pretty view (spec "reassure
list — Import Roster", D2 ordering).
"""

from __future__ import annotations

import typer

from perf.adapters.registry import build_store
from perf.cli.commands.reassure_import import reassure_import
from perf.cli.output.context import NON_TTY_NUDGE, OutputContext
from perf.cli.output.errors import emit_error
from perf.cli.output.json_reporter import render_json
from perf.cli.output.reassure_list_pretty import render_reassure_list
from perf.config.loader import PerfConfig
from perf.contracts.reassure_list_v1 import build_reassure_list_payload

__all__ = ["reassure_app", "reassure_list"]

_CTX = {"help_option_names": ["--help", "-h"]}

reassure_app = typer.Typer(
    add_completion=False,
    context_settings=_CTX,
    help="Read, chart and compare persisted @callstack/reassure measurements.",
)

_LIMIT_OPTION = typer.Option(
    50, "--limit", help="Most recent N imports to include (newest→oldest; D2 ordering)"
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


reassure_app.command(name="import", context_settings=_CTX)(reassure_import)
reassure_app.command(name="list", context_settings=_CTX)(reassure_list)
