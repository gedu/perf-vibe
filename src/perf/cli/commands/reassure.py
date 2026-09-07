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

`show` (PR2b) reports ONE entry's latest detail plus D5's state
transition (spec "reassure show <name> — Latest Detail With
State-Transition Issues (D5, D8)"). D8 default resolves the entry
against the most recent import OVERALL (`store.reassure_imports(1)`),
never the most recent import that happens to contain `name` — A14
forbids walking back, so a `name` missing from the true latest import is
a usage error (exit `2`), not a silent fallback to an older one.
`--import <id>` instead locates `id` inside `store.reassure_series(name,
limit=...)`'s own window — an `id` absent from that window (because it
does not exist, or exists but never measured `name`) is, by construction,
indistinguishable from "not found" and exits `2` the same way. The D5
baseline (the immediately preceding import that also contains `name`)
always comes from `store.reassure_series`, whose own coverage-gap
guarantee (PR2a `test_series_import_missing_name_contributes_nothing_
no_shift`) is exactly what makes `points[-2]` mean "the right thing" —
see `domain/reassure_compare.derive_update_count_change` for the state
derivation itself (created in THIS slice, ahead of PR4a's
`compare_series`, per `tasks.md`'s "D5 GAP — RESOLVED by reordering").

`history` (PR3) reports `name`'s FULL series — one point per import that
contains it (spec "reassure history <name> — Full Series", D2). Reuses
`store.reassure_series(name, limit=_IMPORT_HISTORY_LOOKUP_LIMIT)` — the
SAME store method and the SAME uncapped-window constant `show`'s
`--import` override already established, never a second "uncapped" value
for the same store method. An empty result (`name` in zero imports) is a
usage error (exit `2`), mirroring `entries`'s unknown-id / `show`'s
unknown-name discipline; a coverage gap within the series (an import that
does not contain `name`) is not an error at all — `reassure_series`'s own
JOIN already excludes it, contributing nothing and shifting nothing.

`compare` (this slice, PR4b) is the ONE view in this whole capability
that renders a verdict (spec "reassure compare <name> — Baseline Verdict
(D3, D7)"). Calls `store.reassure_series(name, limit=config.baseline_n +
1)` (A8) then the pure `domain.reassure_compare.compare_series` — same
empty-series-is-unknown-name discipline as `history` (both share
`_UnknownReassureSeriesName`, since `reassure_series`'s own coverage-gap
guarantee makes an empty result mean exactly the same thing for either
command). **D3 — THE LOAD-BEARING RULE FOR THIS COMMAND**: `compare`
ALWAYS exits `0`, including on a confirmed regression or an explicit
`insufficient-data` verdict — it reports, it never gates. The exit code
carries NO verdict information; only an unknown `name` (`2`) or a
store/render failure (`3`) ever differ. See `AGENTS.md`/`CLAUDE.md` for
the full agent-facing warning this asymmetry demands."""

from __future__ import annotations

import typer

from perf.adapters.registry import build_store
from perf.adapters.store_sqlite import SqliteStore
from perf.cli.commands.reassure_import import reassure_import
from perf.cli.output.context import NON_TTY_NUDGE, OutputContext
from perf.cli.output.errors import emit_error
from perf.cli.output.json_reporter import render_json
from perf.cli.output.reassure_compare_pretty import render_reassure_compare
from perf.cli.output.reassure_entries_pretty import render_reassure_entries
from perf.cli.output.reassure_history_pretty import render_reassure_history
from perf.cli.output.reassure_list_pretty import render_reassure_list
from perf.cli.output.reassure_show_pretty import render_reassure_show
from perf.config.loader import PerfConfig
from perf.contracts.reassure_compare_v1 import build_reassure_compare_payload
from perf.contracts.reassure_entries_v1 import build_reassure_entries_payload
from perf.contracts.reassure_history_v1 import build_reassure_history_payload
from perf.contracts.reassure_list_v1 import build_reassure_list_payload
from perf.contracts.reassure_show_v1 import build_reassure_show_payload
from perf.domain.model import ReassureEntryRow
from perf.domain.reassure_compare import compare_series

__all__ = [
    "reassure_app",
    "reassure_compare",
    "reassure_entries_command",
    "reassure_history",
    "reassure_list",
    "reassure_show",
]


class _UnknownReassureImport(Exception):
    """Internal control-flow signal ONLY — never escapes this module.
    Raised when `import_id` matches no persisted `reassure_import` row, to
    route that case to the usage-error branch (exit `2`) and keep it
    distinct from a genuine store/runtime failure (exit `3`). Deliberately
    NOT `ValueError`: a real `ValueError` from the store (e.g. a future
    validation bug) must still surface as exit `3`, not be silently
    reinterpreted as "unknown import"."""


class _UnknownReassureShowTarget(Exception):
    """Internal control-flow signal ONLY — never escapes this module.
    Raised when `reassure show <name>` cannot resolve a target entry: `name`
    absent from the (true, no-walk-back) latest import (A14), absent from
    every import, or `--import <id>` names an import that either does not
    exist or never measured `name` — all three collapse to the SAME usage
    error (exit `2`), never a crash and never a silently wrong baseline.
    Carries its own `message`/`hint` so each call site can still be
    specific about WHICH case fired."""

    def __init__(self, message: str, *, hint: str) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class _UnknownReassureSeriesName(Exception):
    """Internal control-flow signal ONLY — never escapes this module.
    Raised when `name` matches no entry in ANY persisted import —
    `store.reassure_series` returns an empty sequence exactly in that
    case (its own coverage-gap guarantee), which is a usage error (exit
    `2`), never a crash and never a silently empty `--json` payload.
    Shared by `history` AND `compare` (PR4b) — both call
    `store.reassure_series` and both mean exactly the same thing by "empty
    result": `name` was never measured, anywhere."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


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

_SHOW_NAME_ARGUMENT = typer.Argument(help="A `reassure_entry.name` (unknown name exits 2)")

_SHOW_IMPORT_OPTION = typer.Option(
    None,
    "--import",
    help="Show `name` as of this `import_id` instead of the most recent import (D8 default)",
)

# `--import <id>`'s baseline lookup (task 2b.8) needs a concrete, real
# `limit` — "uncapped" is not a value SQLite or Python accepts, and
# `reassure_series`'s own `LIMIT ?` is always a bound integer
# (`store_sqlite.py`). This ceiling exists ONLY to satisfy that parameter's
# type; the actual RESULT is naturally bounded by however many imports
# ever contained `name` — not by this number. Even an aggressive CI setup
# importing reassure results several times a day would take multiple
# YEARS to approach it, so it is never expected to bind in practice, but
# it is a real, finite integer rather than an unbounded sentinel (a `-1`
# `LIMIT` is valid SQLite but relies on adapter-specific semantics no
# other caller in this codebase depends on).
_IMPORT_HISTORY_LOOKUP_LIMIT = 10_000


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


def _resolve_show_target_latest(
    store: SqliteStore, name: str
) -> tuple[int, ReassureEntryRow, int | None]:
    """D8 default: the entry matching `name` in the true most-recent import
    OVERALL (never the most-recent import that merely happens to contain
    `name` — A14, no walk-back). `store.reassure_imports(1)` names that
    import regardless of whether it contains `name`;
    `store.reassure_entries(latest_id, name)` is the A14 guard — it comes
    back empty exactly when `name` is missing from THAT import, which is a
    usage error, never a silent fallback to an older one."""

    latest_imports = store.reassure_imports(1)
    if not latest_imports:
        raise _UnknownReassureShowTarget(
            f"no reassure imports recorded yet — cannot show `{name}`",
            hint="run `perfvibe reassure import` first",
        )
    latest_import_id = latest_imports[0].import_id
    entries = store.reassure_entries(latest_import_id, name)
    if not entries:
        raise _UnknownReassureShowTarget(
            f"no reassure entry named `{name}` in the most recent import",
            hint=(
                "no walk-back to an older import — pass `--import <id>` or "
                "check `perfvibe reassure history <name>`"
            ),
        )

    # D5 baseline: the immediately preceding import that ALSO contains
    # `name` (design "D5 GAP — RESOLVED by reordering"). Because the
    # latest import overall was just proven to contain `name`, it is also
    # the latest import CONTAINING `name`, so `points[-1]` is that same
    # import and `points[-2]` (if it exists) is the right baseline.
    points = store.reassure_series(name, limit=2)
    baseline = points[-2].entry.initial_update_count if len(points) >= 2 else None
    return latest_import_id, entries[0], baseline


def _resolve_show_target_explicit(
    store: SqliteStore, name: str, import_id: int
) -> tuple[int, ReassureEntryRow, int | None]:
    """`--import <id>` override: locate `id` inside `reassure_series(name,
    limit=<a real, generous ceiling>)`'s own window. An `id` that either
    does not exist at all, or exists but never measured `name`, is simply
    ABSENT from that window (PR2a's own coverage-gap guarantee) — both
    collapse to the same usage error, never a crash and never a silently
    wrong baseline."""

    points = store.reassure_series(name, limit=_IMPORT_HISTORY_LOOKUP_LIMIT)
    matched_index = next(
        (index for index, point in enumerate(points) if point.import_id == import_id), None
    )
    if matched_index is None:
        raise _UnknownReassureShowTarget(
            f"import {import_id} has no reassure entry named `{name}`",
            hint="see `perfvibe reassure entries <import-id>` for that import's entries",
        )
    baseline = points[matched_index - 1].entry.initial_update_count if matched_index > 0 else None
    return import_id, points[matched_index].entry, baseline


def reassure_show(
    ctx: typer.Context,
    name: str = _SHOW_NAME_ARGUMENT,
    import_id: int | None = _SHOW_IMPORT_OPTION,
) -> None:
    """Reports one `name`'s latest detail — declared `runs`, the
    independent duration/count summaries, and `issues.initialUpdateCount`
    rendered as a D5 state transition against the immediately preceding
    import that also contains `name` (spec "reassure show <name> — Latest
    Detail With State-Transition Issues (D5, D8)"). Defaults to the most
    recent import OVERALL (D8); `--import <id>` selects a specific import
    instead. Read-only. `name` absent from the target import is a usage
    error (exit `2`, no `--json` payload) — A14 forbids walking back to an
    older import that happens to contain it."""

    state: dict = ctx.obj or {}
    output: OutputContext = state["output"]
    config: PerfConfig = state["config"]

    store = None
    try:
        store = build_store(config.db_path)
        if import_id is None:
            resolved_import_id, entry, baseline = _resolve_show_target_latest(store, name)
        else:
            resolved_import_id, entry, baseline = _resolve_show_target_explicit(
                store, name, import_id
            )
    except _UnknownReassureShowTarget as exc:
        emit_error(output, exc.message, hint=exc.hint)
        raise typer.Exit(code=2) from None
    except Exception as exc:
        emit_error(output, f"unexpected failure reading reassure show data: {exc}")
        raise typer.Exit(code=3) from None
    finally:
        _close_store(store)

    try:
        if output.json_mode:
            payload = build_reassure_show_payload(
                import_id=resolved_import_id, entry=entry, baseline_initial_update_count=baseline
            )
            typer.echo(render_json(payload))
        else:
            if output.should_nudge_stderr:
                typer.echo(NON_TTY_NUDGE, err=True)
            typer.echo(
                render_reassure_show(
                    resolved_import_id, entry, baseline, color=output.color_enabled
                )
            )
    except Exception as exc:
        emit_error(output, f"failed to render reassure show output: {exc}")
        raise typer.Exit(code=3) from None

    raise typer.Exit(code=0)


def reassure_history(
    ctx: typer.Context,
    name: str = _SHOW_NAME_ARGUMENT,
) -> None:
    """Reports `name`'s full series — one point per import that contains
    it, ordered per D2 (oldest first), each carrying independently-reduced
    duration/count summaries (spec "reassure history <name> — Full
    Series"). Read-only. `name` absent from EVERY import is a usage error
    (exit `2`, no `--json` payload); a coverage gap within an otherwise
    non-empty series is not an error at all — the store's own JOIN already
    excludes an import that never measured `name`, contributing nothing
    and shifting nothing."""

    state: dict = ctx.obj or {}
    output: OutputContext = state["output"]
    config: PerfConfig = state["config"]

    store = None
    try:
        store = build_store(config.db_path)
        points = store.reassure_series(name, limit=_IMPORT_HISTORY_LOOKUP_LIMIT)
        if not points:
            raise _UnknownReassureSeriesName(name)
    except _UnknownReassureSeriesName as exc:
        emit_error(
            output,
            f"no reassure entry named `{exc.name}` in any import",
            hint="see `perfvibe reassure list` for known imports",
        )
        raise typer.Exit(code=2) from None
    except Exception as exc:
        emit_error(output, f"unexpected failure reading reassure history data: {exc}")
        raise typer.Exit(code=3) from None
    finally:
        _close_store(store)

    try:
        if output.json_mode:
            payload = build_reassure_history_payload(name=name, points=points)
            typer.echo(render_json(payload))
        else:
            if output.should_nudge_stderr:
                typer.echo(NON_TTY_NUDGE, err=True)
            typer.echo(render_reassure_history(name, points, color=output.color_enabled))
    except Exception as exc:
        emit_error(output, f"failed to render reassure history output: {exc}")
        raise typer.Exit(code=3) from None

    raise typer.Exit(code=0)


def reassure_compare(
    ctx: typer.Context,
    name: str = _SHOW_NAME_ARGUMENT,
) -> None:
    """Compares `name`'s latest import against a `config.baseline_n`-import
    baseline window (A8) via the pure `domain.reassure_compare.
    compare_series` (spec "reassure compare <name> — Baseline Verdict (D3,
    D7)"). Read-only, show-only.

    **D3 — ALWAYS exits `0`**, including on a confirmed regression on
    EITHER series, or an explicit `insufficient-data` state when the
    baseline window has fewer than `MIN_BASELINE_IMPORTS` (3) points —
    `reassure` reports, it never gates, and the exit code carries NO
    verdict information (read `--json`'s `verdicts` array instead). `name`
    absent from EVERY persisted import is a usage error (exit `2`, no
    `--json` payload, same discipline as `history`'s unknown-name case); a
    store/render failure exits `3`. Never exit `1`."""

    state: dict = ctx.obj or {}
    output: OutputContext = state["output"]
    config: PerfConfig = state["config"]

    store = None
    try:
        store = build_store(config.db_path)
        # A8: the baseline window is `config.baseline_n` PRIOR imports plus
        # the latest one — the same `reassure_series` method `history`
        # uses, just at a much smaller, config-driven limit.
        points = store.reassure_series(name, limit=config.baseline_n + 1)
        if not points:
            raise _UnknownReassureSeriesName(name)
        comparison = compare_series(
            points, threshold_pct=config.threshold_pct, floors=config.floors
        )
    except _UnknownReassureSeriesName as exc:
        emit_error(
            output,
            f"no reassure entry named `{exc.name}` in any import",
            hint="see `perfvibe reassure list` for known imports",
        )
        raise typer.Exit(code=2) from None
    except Exception as exc:
        emit_error(output, f"unexpected failure comparing reassure data: {exc}")
        raise typer.Exit(code=3) from None
    finally:
        _close_store(store)

    # `points` is non-empty here (the branch above exits before reaching
    # this line otherwise), and `compare_series` only ever returns `None`
    # for empty input (see its own docstring) — this narrows the type for
    # mypy without a second, redundant runtime check.
    assert comparison is not None

    try:
        if output.json_mode:
            payload = build_reassure_compare_payload(comparison=comparison)
            typer.echo(render_json(payload))
        else:
            if output.should_nudge_stderr:
                typer.echo(NON_TTY_NUDGE, err=True)
            typer.echo(render_reassure_compare(comparison, color=output.color_enabled))
    except Exception as exc:
        emit_error(output, f"failed to render reassure compare output: {exc}")
        raise typer.Exit(code=3) from None

    # D3: `reassure compare` is show-only and NEVER gates — exit `0` here
    # unconditionally, even when every verdict reports `regression` or
    # `insufficient-data` (spec "reassure compare ... ALWAYS exits 0").
    raise typer.Exit(code=0)


reassure_app.command(name="import", context_settings=_CTX)(reassure_import)
reassure_app.command(name="list", context_settings=_CTX)(reassure_list)
reassure_app.command(name="entries", context_settings=_CTX)(reassure_entries_command)
reassure_app.command(name="show", context_settings=_CTX)(reassure_show)
reassure_app.command(name="history", context_settings=_CTX)(reassure_history)
reassure_app.command(name="compare", context_settings=_CTX)(reassure_compare)
