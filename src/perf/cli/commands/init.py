"""`perf init <flows-dir>` — typer command that scaffolds/merges a `perf.toml`
`[flows]` table plus a detected `bundle_id` from a Maestro flows directory
(spec `docs/specs/init-command/spec.md`, design
`docs/specs/init-command/design.md`). Pure CLI-adjacent scaffolding: local
fs I/O only (glob flow files, read headers, write TOML) — no device,
subprocess, git, or DB (design "Technical Approach").

ALL logic lives in this ONE module (design decision "Where code lives"):
the pure helpers below (`discover_flows`, `parse_app_id`,
`reconcile_bundle_id`, `serialize_toml`, `merge_config`, `has_comments`)
are module-level functions, directly unit-testable with zero CLI/typer
dependency, composed together by the `init` command at the bottom.
"""

from __future__ import annotations

import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import typer

from perf.cli.output.context import NON_TTY_NUDGE, OutputContext
from perf.cli.output.json_reporter import render_json
from perf.config.loader import PerfConfig
from perf.contracts.init_v1 import build_init_payload

__all__ = [
    "TEMPLATE",
    "BundleReconciliation",
    "FlowCollisionError",
    "discover_flows",
    "has_comments",
    "init",
    "merge_config",
    "parse_app_id",
    "reconcile_bundle_id",
    "serialize_toml",
]

# ===== Flow discovery (design "Flow discovery") =====

_FLOW_SUFFIXES: Final = {".yaml", ".yml"}


def _is_subflows_segment(segment: str) -> bool:
    """Case-insensitive match for a Maestro `subflows/` path segment
    (design "Flow discovery": Maestro `subflows/` are `runFlow` utilities,
    never top-level tests, regardless of case or depth)."""

    return segment.lower() == "subflows"


def discover_flows(flows_dir: Path) -> dict[str, Path]:
    """Recursively discovers `*.yaml`/`*.yml` files under `flows_dir`,
    excluding any file with a path segment (case-insensitive) equal to
    `subflows`, at any depth. Each remaining file becomes one candidate
    flow, keyed by its filename stem (spec "Recursive Flow Discovery").
    A nonexistent or empty directory yields zero candidates (I1)."""

    if not flows_dir.is_dir():
        return {}

    flows: dict[str, Path] = {}
    for path in sorted(flows_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _FLOW_SUFFIXES:
            continue
        relative_parts = path.relative_to(flows_dir).parts[:-1]
        if any(_is_subflows_segment(part) for part in relative_parts):
            continue
        flows[path.stem] = path
    return flows


# ===== appId parsing (design "appId line-scan (exact algorithm)") =====

TEMPLATE: Final = "TEMPLATE"
"""Sentinel returned by `parse_app_id` for a `${...}` templated value — an
env-var reference Maestro itself resolves at runtime, NEVER a concrete
detection (spec "Mandatory appId Parsing")."""

_MAX_HEADER_LINES: Final = 500
_MAX_LINE_LENGTH: Final = 4096


def _strip_matching_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_app_id(text: str) -> str | None:
    """Tolerant line-scan of the pre-`---` header block for an `appId:`
    key — no YAML dependency (design "appId parse"). Returns the concrete
    value, `TEMPLATE` for a `${...}` value, or `None` when absent/missing.
    Bounded lines/line-length; never `eval` (perf-cli-standards rule 5)."""

    for line in text.splitlines()[:_MAX_HEADER_LINES]:
        stripped = line[:_MAX_LINE_LENGTH].strip()
        if stripped == "---":
            return None
        if stripped.startswith("appId:"):
            value = _strip_matching_quotes(stripped[len("appId:") :].strip())
            return TEMPLATE if "${" in value else value
    return None


# ===== Bundle ID reconciliation (spec "Bundle ID Reconciliation") =====


@dataclass(frozen=True)
class BundleReconciliation:
    """Result of reconciling every discovered flow's `appId` (design
    "Data Flow"). `candidate` is the single agreed-upon concrete value, or
    `None` when there is zero or more than one distinct value. `conflict`
    lists the distinct conflicting values (sorted), or `None` when there
    is no mismatch."""

    candidate: str | None
    conflict: tuple[str, ...] | None


def reconcile_bundle_id(appid_by_flow: Mapping[str, str | None]) -> BundleReconciliation:
    """Reconciles concrete `appId` values across flows. `TEMPLATE` and
    `None` values are treated as absent — they contribute no signal
    (spec scenarios "Zero concrete values...", "Templated appId is not a
    concrete detection")."""

    concrete_values = {
        value for value in appid_by_flow.values() if value is not None and value != TEMPLATE
    }
    if not concrete_values:
        return BundleReconciliation(candidate=None, conflict=None)
    if len(concrete_values) == 1:
        return BundleReconciliation(candidate=next(iter(concrete_values)), conflict=None)
    return BundleReconciliation(candidate=None, conflict=tuple(sorted(concrete_values)))


# ===== TOML serialization (design "TOML write") =====


def _needs_basic_string(value: str) -> bool:
    if "'" in value:
        return True
    return any(ord(ch) < 0x20 and ch != "\t" for ch in value)


def _serialize_string(value: str) -> str:
    # Literal strings `'…'` are the DEFAULT (safe for backslash, no
    # escaping needed) — only fall back to an escaped basic string `"…"`
    # when the value itself contains a `'` or a control character (design
    # "String escaping").
    if not _needs_basic_string(value):
        return f"'{value}'"
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def _serialize_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return _serialize_string(value)
    raise TypeError(f"Unsupported TOML value type: {type(value)!r}")


def _serialize_table(lines: list[str], path: Sequence[str], table: Mapping[str, object]) -> None:
    scalar_fields = {k: v for k, v in table.items() if not isinstance(v, Mapping)}
    nested_tables = {k: v for k, v in table.items() if isinstance(v, Mapping)}

    if scalar_fields or not nested_tables:
        if lines:
            lines.append("")
        lines.append(f"[{'.'.join(path)}]")
        for key, value in scalar_fields.items():
            lines.append(f"{key} = {_serialize_value(value)}")

    for nested_name, nested_table in nested_tables.items():
        _serialize_table(lines, [*path, nested_name], nested_table)


def serialize_toml(data: Mapping[str, object]) -> str:
    """Canonical, full re-serialize of `data` into valid TOML text — NEVER
    a blind text-append (design "TOML write": appending a top-level key
    after existing tables would be invalid TOML). Root scalar keys are
    written first, followed by every nested-dict value as a `[table]` (or
    `[parent.child]`) block. Output always round-trips via
    `tomllib.loads`."""

    lines: list[str] = []
    root_scalars = {k: v for k, v in data.items() if not isinstance(v, Mapping)}
    root_tables = {k: v for k, v in data.items() if isinstance(v, Mapping)}

    for key, value in root_scalars.items():
        lines.append(f"{key} = {_serialize_value(value)}")

    for table_name, table_value in root_tables.items():
        _serialize_table(lines, [table_name], table_value)

    return "\n".join(lines) + ("\n" if lines else "")


# ===== Merge semantics (spec "perf.toml Writing and Merge Semantics") =====


class FlowCollisionError(Exception):
    """Raised by `merge_config` when a discovered flow name already exists
    in the target config and `force=False` (spec "Colliding flow name
    without --force is refused")."""

    def __init__(self, colliding_names: Sequence[str]) -> None:
        self.colliding_names: tuple[str, ...] = tuple(colliding_names)
        super().__init__(f"colliding flow name(s): {', '.join(self.colliding_names)}")


def has_comments(raw_toml_text: str) -> bool:
    """Detects a `#` outside any string literal — used to gate a
    comment-loss confirmation before re-serializing an existing
    `perf.toml` (tasks.md decision #3). Tracks single/basic-string quoting
    only; perf.toml is a small, tool-managed/hand-written file, never a
    multi-line-string-heavy document."""

    in_single = False
    in_double = False
    escaped = False
    for ch in raw_toml_text:
        if in_double:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_double = False
            continue
        if in_single:
            if ch == "'":
                in_single = False
            continue
        if ch == '"':
            in_double = True
        elif ch == "'":
            in_single = True
        elif ch == "#":
            return True
    return False


def merge_config(
    existing: Mapping[str, object],
    new_flows: Mapping[str, Path],
    bundle_id: str | None,
    force: bool,
) -> dict[str, object]:
    """Deep-merges `new_flows` into `existing`'s `[flows]` table, leaving
    every other existing entry/key untouched. A colliding flow NAME is
    refused via `FlowCollisionError` unless `force=True`, in which case it
    is overwritten (spec "perf.toml Writing and Merge Semantics")."""

    existing_flows_raw = existing.get("flows")
    existing_flows: dict[str, object] = (
        dict(existing_flows_raw) if isinstance(existing_flows_raw, Mapping) else {}
    )

    colliding = sorted(name for name in new_flows if name in existing_flows)
    if colliding and not force:
        raise FlowCollisionError(colliding)

    merged_flows = dict(existing_flows)
    for name, path in new_flows.items():
        merged_flows[name] = {"maestro_path": str(path)}

    merged: dict[str, object] = dict(existing)
    merged["flows"] = merged_flows
    if bundle_id is not None:
        merged["bundle_id"] = bundle_id
    return merged


# ===== Comment-loss warning (tasks.md decision #3, golden 3.10) =====


def _render_comment_loss_confirm_prompt(config_path: Path) -> str:
    """Interactive confirm-gate text — shown before overwriting an existing
    `perf.toml` that contains a hand-written `#` comment (decision #3).
    Extracted as a pure function so both call sites share identical wording
    and it is directly golden-testable (tasks.md 3.10)."""

    return f"{config_path} contains hand-written comments that will be lost on rewrite — continue?"


def _render_comment_loss_error(config_path: Path) -> str:
    """Non-interactive error text for the same guard — printed to stderr
    when `--force` was not supplied and there is no TTY to confirm in."""

    return (
        f"Error: {config_path} contains hand-written comments that would be "
        "lost on rewrite; pass --force to overwrite anyway"
    )


# ===== Pretty confirmation (design "Testing Strategy" — golden, Phase 3) =====

_DIM = "\x1b[2m"
_GREEN = "\x1b[32m"
_RESET = "\x1b[0m"


def _style(text: str, *, color: bool, code: str) -> str:
    return f"{code}{text}{_RESET}" if color else text


def _render_confirmation(
    *,
    config_path: Path,
    flows_added: Sequence[str],
    bundle_id: str | None,
    bundle_id_source: str,
    color: bool,
) -> str:
    lines: list[str] = []
    lines.append(_style(f"✓ perf init wrote {config_path}", color=color, code=_GREEN))
    lines.append(f"  flows added: {', '.join(sorted(flows_added)) or '(none)'}")
    lines.append(f"  bundle_id:   {bundle_id or '(unset)'} ({bundle_id_source})")
    return "\n".join(lines) + "\n"


# ===== Interactive wizard (spec "Interactive Wizard vs Non-Interactive Mode") =====


def _prompt_bundle_id(candidate: str | None, *, color: bool) -> str | None:
    """Dim, pre-filled placeholder default (spec "Wizard shows a dim
    placeholder default"): Enter accepts the detected value as-is; typed
    input overrides it."""

    styled_default = _style(candidate, color=color, code=_DIM) if candidate else ""
    prompt_text = f"bundle_id [{styled_default}]" if candidate else "bundle_id (none detected)"
    raw = typer.prompt(prompt_text, default=candidate or "", show_default=False)
    return raw.strip() or None


def _render_mismatch_conflict_message(conflict: Sequence[str], *, color: bool) -> str:
    """Text shown before the interactive mismatch-resolution prompt (spec
    "Mismatch — interactive prompt"). Extracted as a pure function (mirrors
    `_render_confirmation`) so it is directly golden-testable without
    needing a simulated TTY (tasks.md 3.10)."""

    text = f"Conflicting appId values detected: {', '.join(conflict)}"
    return _style(text, color=color, code=_DIM)


def _prompt_bundle_id_conflict(conflict: Sequence[str], *, color: bool) -> str | None:
    typer.echo(_render_mismatch_conflict_message(conflict, color=color))
    raw = typer.prompt("Enter the bundle_id to use", default="", show_default=False)
    return raw.strip() or None


# ===== The `init` command (design "Typer signature") =====


def init(
    ctx: typer.Context,
    flows_dir: str = typer.Argument(
        ..., help="Directory to recursively scan for Maestro flow files"
    ),
    bundle_id: str | None = typer.Option(
        None,
        "--bundle-id",
        help="Explicit bundle_id to write — wins over auto-detection or a mismatch",
    ),
    driver: str | None = typer.Option(
        None,
        "--driver",
        help='Write a literal driver = "..." key verbatim (no detection, decision #1)',
    ),
    db: str | None = typer.Option(
        None,
        "--db",
        help='Write a literal db_path = "..." key verbatim (no detection, decision #1)',
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite a colliding flow name or a perf.toml containing hand-written comments",
    ),
    yes: bool = typer.Option(
        False, "--yes", help="Force non-interactive mode even when stdin is a TTY"
    ),
) -> None:
    """Scaffold or merge a `perf.toml` from a Maestro flows directory
    (spec `docs/specs/init-command/spec.md`). Local fs I/O only — no
    device, subprocess, git, or DB access. Exit `0` success; `2` usage
    error; `3` runtime/tooling failure — NEVER `1` (that code is reserved
    for `compare`/`budget-check` regressions)."""

    state: dict = ctx.obj or {}
    output: OutputContext = state["output"]
    config: PerfConfig = state["config"]
    del config  # `init` performs no device/DB I/O; kept for signature symmetry with other commands.

    # Usage-error-before-work guards (mirrors run/compare/budget-check): a
    # nonexistent/non-directory `--flows-dir` (I1) and zero candidate flows
    # post-`subflows/`-exclusion (I1/I2) are both usage errors, checked
    # BEFORE any fs write.
    flows_path = Path(flows_dir)
    if not flows_path.is_dir():
        typer.echo(
            f"Error: --flows-dir {flows_dir!r} does not exist or is not a directory", err=True
        )
        raise typer.Exit(code=2)

    flows = discover_flows(flows_path)
    if not flows:
        typer.echo(
            f"Error: no candidate flows discovered under {flows_dir!r} "
            "(after excluding subflows/); nothing to scaffold",
            err=True,
        )
        raise typer.Exit(code=2)

    # TTY auto-detect; `--yes` forces non-interactive even under a TTY
    # (spec "Interactive Wizard vs Non-Interactive Mode").
    interactive = sys.stdin.isatty() and not yes

    try:
        appid_by_flow = {name: parse_app_id(path.read_text()) for name, path in flows.items()}
    except OSError as exc:
        typer.echo(f"Error: failed to read a flow header under {flows_dir!s}: {exc}", err=True)
        raise typer.Exit(code=3) from None

    reconciliation = reconcile_bundle_id(appid_by_flow)

    resolved_bundle_id: str | None
    bundle_id_source: str
    try:
        if bundle_id is not None:
            # An explicit flag always wins — interactive or not (spec
            # "Mismatch — non-interactive with --bundle-id resolves").
            resolved_bundle_id, bundle_id_source = bundle_id, "flag"
        elif reconciliation.conflict is not None:
            if interactive:
                resolved_bundle_id = _prompt_bundle_id_conflict(
                    reconciliation.conflict, color=output.color_enabled
                )
                bundle_id_source = "prompt" if resolved_bundle_id else "none"
            else:
                typer.echo(
                    "Error: conflicting appId values detected across flows: "
                    f"{', '.join(reconciliation.conflict)}; pass --bundle-id to resolve",
                    err=True,
                )
                raise typer.Exit(code=2)
        elif interactive:
            resolved_bundle_id = _prompt_bundle_id(
                reconciliation.candidate, color=output.color_enabled
            )
            bundle_id_source = "prompt" if resolved_bundle_id else "none"
        elif reconciliation.candidate is not None:
            resolved_bundle_id, bundle_id_source = reconciliation.candidate, "detected"
        else:
            resolved_bundle_id, bundle_id_source = None, "none"
    except typer.Abort:
        # Ctrl-C / EOF mid-prompt is a runtime interruption, never Python's
        # default exit 1 (perf-cli-standards rule 7 / spec "init never
        # exits 1").
        typer.echo("Error: aborted during interactive prompt", err=True)
        raise typer.Exit(code=3) from None

    # Output path resolution (tasks.md decision #2): reuse the global
    # `--config` option; default to `./perf.toml` in CWD when omitted,
    # matching `_find_project_config`'s CWD-only discovery.
    raw_config_path = state.get("config_path")
    config_path = Path(raw_config_path) if raw_config_path else Path.cwd() / "perf.toml"

    try:
        if config_path.is_file():
            existing_raw_text = config_path.read_text()
            existing_data: dict[str, object] = tomllib.loads(existing_raw_text)
        else:
            existing_raw_text = ""
            existing_data = {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        typer.echo(f"Error: failed to read existing {config_path}: {exc}", err=True)
        raise typer.Exit(code=3) from None

    # Comment-loss confirmation gate (tasks.md decision #3): re-serializing
    # ALWAYS drops hand-written comments — never silently destroy them.
    if existing_raw_text and has_comments(existing_raw_text) and not force:
        if interactive:
            try:
                confirmed = typer.confirm(
                    _render_comment_loss_confirm_prompt(config_path), default=False
                )
            except typer.Abort:
                typer.echo("Error: aborted during interactive prompt", err=True)
                raise typer.Exit(code=3) from None
            if not confirmed:
                typer.echo(
                    f"Error: aborted — re-run with --force to overwrite {config_path} anyway",
                    err=True,
                )
                raise typer.Exit(code=2)
        else:
            typer.echo(_render_comment_loss_error(config_path), err=True)
            raise typer.Exit(code=2)

    try:
        merged = merge_config(existing_data, flows, resolved_bundle_id, force)
    except FlowCollisionError as exc:
        typer.echo(
            f"Error: flow name(s) already exist in {config_path}: "
            f"{', '.join(exc.colliding_names)}; pass --force to overwrite",
            err=True,
        )
        raise typer.Exit(code=2) from None

    # `--driver`/`--db` are trivial, verbatim literal pass-through keys —
    # no detection logic (tasks.md decision #1) — written only when supplied.
    if driver is not None:
        merged["driver"] = driver
    if db is not None:
        merged["db_path"] = db

    try:
        config_path.write_text(serialize_toml(merged))
    except OSError as exc:
        typer.echo(f"Error: failed to write {config_path}: {exc}", err=True)
        raise typer.Exit(code=3) from None

    # The all-or-nothing collision refusal above means every discovered
    # flow this run either had no name conflict or was explicitly
    # `--force`-overwritten — there is no partial/skipped subset by the
    # time we reach here (flows_skipped stays empty under this atomic
    # merge design; reserved in the contract for a future partial-merge
    # mode).
    flows_added = sorted(flows)

    payload = build_init_payload(
        config_path=str(config_path),
        bundle_id=resolved_bundle_id,
        bundle_id_source=bundle_id_source,
        flows_added=flows_added,
        flows_skipped=[],
        flows_total=len(flows),
        appid_conflict=list(reconciliation.conflict) if reconciliation.conflict else None,
    )

    try:
        if output.json_mode:
            typer.echo(render_json(payload))
        else:
            if output.should_nudge_stderr:
                typer.echo(NON_TTY_NUDGE, err=True)
            typer.echo(
                _render_confirmation(
                    config_path=config_path,
                    flows_added=flows_added,
                    bundle_id=resolved_bundle_id,
                    bundle_id_source=bundle_id_source,
                    color=output.color_enabled,
                )
            )
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"Error: failed to render output for {config_path}: {exc}", err=True)
        raise typer.Exit(code=3) from None

    raise typer.Exit(code=0)
