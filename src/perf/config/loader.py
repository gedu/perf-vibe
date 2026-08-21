"""Layered configuration loader (design §14, spec "CLI Options and
Configuration Surface").

Precedence, highest to lowest: CLI flags > env (`PERF_DB`, `NO_COLOR`,
`MAESTRO_DEVICE`) > project flow config (`perfvibe.toml`/`.perfvibe.toml` in the
current/given directory) > global `~/.config/perf/config.toml` > built-in
defaults. `BUNDLE_ID` and flow definitions are ALWAYS sourced from this
layered config — NEVER hardcoded anywhere in source (SKILL rule 9 /
hard boundary).

Uses stdlib `tomllib` only (Python 3.11+) — no new dependency (SKILL
rule 9). Adapter SELECTION (`driver`/`sampler`/`marker_source` names) is
resolved here by NAME only; this module never imports `adapters/` — the
CLI layer threads the resolved names into `adapters/registry.py`.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "DEFAULT_ADAPTIVE_FLOOR",
    "DEFAULT_BASELINE_N",
    "DEFAULT_FLOORS",
    "DEFAULT_ITERATIONS",
    "DEFAULT_MIN_BASELINE_COMMITS",
    "DEFAULT_REASSURE_PATH",
    "DEFAULT_THRESHOLD_PCT",
    "DEFAULT_WARMUP_K",
    "GLOBAL_CONFIG_PATH",
    "ConfigError",
    "FlowConfig",
    "PerfConfig",
    "load_config",
]


class ConfigError(Exception):
    """A user-facing configuration problem: an unparseable/unreadable TOML
    file, an ill-typed value, or an explicitly named `--config` path that
    does not exist. The CLI callback maps this to a usage error (exit 2) —
    a broken config must never escape as a raw traceback (which would exit
    1 and poison the `run`-never-exits-1 contract). `cause`/`hint` feed
    `emit_error`'s follow-up lines."""

    def __init__(self, message: str, *, cause: str | None = None, hint: str | None = None):
        super().__init__(message)
        self.cause = cause
        self.hint = hint


DEFAULT_ITERATIONS = 10
DEFAULT_DB_PATH = "perfvibe.db"
DEFAULT_RESULTS_DIR = "results"
DEFAULT_MODE = "warm"
DEFAULT_TOOL_VERSION = "0.1.0"

# Compare tuning defaults (design Rev 2 "Tuning defaults", decision #58):
# conservative/low-noise so the tool doesn't cry wolf — all overridable
# via `perfvibe.toml` / CLI flags.
DEFAULT_THRESHOLD_PCT = 5.0
DEFAULT_FLOORS: Mapping[str, float] = {"ms": 5.0, "mb": 5.0, "pct": 3.0, "fps": 2.0}
DEFAULT_MIN_BASELINE_COMMITS = 3
DEFAULT_WARMUP_K = 1
DEFAULT_BASELINE_N = 10
# Adaptive noise floor (anti-false-positive batch, Task 2): widen a metric's
# absolute floor to its own robust historical scatter when the baseline is
# noisy, so noisy metrics stop crying wolf. On by default; set
# `adaptive_floor = false` to restore the pre-batch static-floor behavior.
DEFAULT_ADAPTIVE_FLOOR = True

# `reassure-import`'s default input path (reassure-ingest PR4b). An INPUT
# path the user points at, like `flows[].maestro_path` — deliberately NOT
# anchored under `base_dir`/`_under_base` (see `PerfConfig.reassure_path`
# below and `_under_base`'s docstring).
DEFAULT_REASSURE_PATH = ".reassure/current.perf"

GLOBAL_CONFIG_PATH = Path.home() / ".config" / "perf" / "config.toml"
PROJECT_CONFIG_FILENAMES: tuple[str, ...] = ("perfvibe.toml", ".perfvibe.toml")


@dataclass(frozen=True)
class FlowConfig:
    """One config-known flow (spec: `flow_name` must be validated against
    this set BEFORE any driver invocation)."""

    name: str
    maestro_path: str | None = None
    prompt: str | None = None


@dataclass(frozen=True)
class PerfConfig:
    """Fully resolved, layered configuration for one CLI invocation."""

    db_path: str = DEFAULT_DB_PATH
    no_color: bool = False
    driver: str = "maestro"
    sampler: str | None = "flashlight"
    marker_source: str | None = "adb-logcat"
    bundle_id: str | None = None
    default_iterations: int = DEFAULT_ITERATIONS
    default_mode: str = DEFAULT_MODE
    device: str | None = None
    results_dir: str = DEFAULT_RESULTS_DIR
    base_dir: str | None = None
    build_variant: str | None = None
    tool_version: str = DEFAULT_TOOL_VERSION
    replay_logcat: str | None = None
    replay_flashlight: str | None = None
    # `reassure-import`'s default input path (reassure-ingest PR4b) — an
    # INPUT path the user points at, deliberately NOT run through
    # `_under_base` (matches `flows[].maestro_path`/`replay_logcat`/
    # `replay_flashlight` above: perfvibe reads FROM here, it does not
    # write here).
    reassure_path: str = DEFAULT_REASSURE_PATH
    flows: Mapping[str, FlowConfig] = field(default_factory=dict)

    # ===== compare tuning knobs (design Rev 2/3, decision #58) =====
    threshold_pct: float = DEFAULT_THRESHOLD_PCT
    floors: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_FLOORS))
    min_baseline_commits: int = DEFAULT_MIN_BASELINE_COMMITS
    warmup_k: int = DEFAULT_WARMUP_K
    baseline_n: int = DEFAULT_BASELINE_N
    adaptive_floor: bool = DEFAULT_ADAPTIVE_FLOOR


def _read_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"config file `{path}` is not valid TOML", cause=str(exc)) from exc
    except OSError as exc:
        raise ConfigError(f"config file `{path}` could not be read", cause=str(exc)) from exc


def _merge(base: dict, override: dict) -> dict:
    """Shallow-recursive merge: `override` wins key-by-key; nested dicts
    (e.g. `[flows.checkout]`) merge recursively rather than replacing the
    whole table wholesale."""

    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _find_project_config(start_dir: Path) -> Path | None:
    for filename in PROJECT_CONFIG_FILENAMES:
        candidate = start_dir / filename
        if candidate.is_file():
            return candidate
    return None


def _typed_int(layers: Mapping[str, object], key: str, default: int) -> int:
    try:
        return int(layers.get(key, default))  # type: ignore[call-overload]
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"invalid value for `{key}` in config", cause=str(exc)) from exc


def _typed_float(layers: Mapping[str, object], key: str, default: float) -> float:
    try:
        return float(layers.get(key, default))  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"invalid value for `{key}` in config", cause=str(exc)) from exc


def _normalize_optional_source(value: object) -> str | None:
    """An optional adapter source (`sampler`/`marker_source`) that is unset,
    empty, or the literal `"none"` (case-insensitive) resolves to `None` —
    i.e. "no adapter selected" (resilience batch, Task 4). This is what
    enables marker-only runs (`sampler = "none"`, e.g. react-native-
    performance without Flashlight installed) or sampler-only runs
    (`marker_source = "none"`). A real adapter name passes through verbatim
    for the registry to resolve (or reject)."""

    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() == "none":
        return None
    return text


def _build_flows(raw: Mapping[str, object]) -> Mapping[str, FlowConfig]:
    flows: dict[str, FlowConfig] = {}
    for name, spec in raw.items():
        if isinstance(spec, Mapping):
            flows[name] = FlowConfig(
                name=name,
                maestro_path=spec.get("maestro_path"),
                prompt=spec.get("prompt"),
            )
        else:
            # A bare `name = "path/to/flow.yaml"` shorthand.
            flows[name] = FlowConfig(name=name, maestro_path=str(spec))
    return flows


def load_config(
    *,
    cli_db: str | None = None,
    cli_config_path: str | None = None,
    cli_no_color: bool | None = None,
    cli_device: str | None = None,
    env: Mapping[str, str] | None = None,
    project_dir: Path | None = None,
) -> PerfConfig:
    """Resolve the layered config (design §14). `env` and `project_dir` are
    injectable for tests — production callers omit both and get
    `os.environ` / `Path.cwd()`."""

    env = env if env is not None else os.environ
    project_dir = project_dir if project_dir is not None else Path.cwd()

    layers: dict = {}
    layers = _merge(layers, _read_toml(GLOBAL_CONFIG_PATH))

    # An EXPLICITLY named `--config` must exist — silently falling back to
    # defaults turns a typo'd path into a baffling downstream "unknown flow"
    # error. Only the DISCOVERED project/global files may be silently absent.
    if cli_config_path is not None:
        explicit_path = Path(cli_config_path)
        if not explicit_path.is_file():
            raise ConfigError(
                f"config file `{cli_config_path}` not found",
                hint="check the `--config` path",
            )
        project_path: Path | None = explicit_path
    else:
        project_path = _find_project_config(project_dir)
    if project_path is not None:
        layers = _merge(layers, _read_toml(project_path))

    env_layer: dict = {}
    if "PERF_DB" in env:
        env_layer["db_path"] = env["PERF_DB"]
    if "NO_COLOR" in env:
        env_layer["no_color"] = True
    if "MAESTRO_DEVICE" in env:
        env_layer["device"] = env["MAESTRO_DEVICE"]
    layers = _merge(layers, env_layer)

    cli_layer: dict = {}
    if cli_db is not None:
        cli_layer["db_path"] = cli_db
    if cli_no_color is not None:
        cli_layer["no_color"] = cli_no_color
    if cli_device is not None:
        cli_layer["device"] = cli_device
    layers = _merge(layers, cli_layer)

    flows_raw = layers.pop("flows", {}) or {}
    if not isinstance(flows_raw, dict):
        raise ConfigError("`flows` must be a table of `[flows.<name>]` entries")
    flows = _build_flows(flows_raw)

    # Partial `[floors]` overrides (e.g. only `fps = 1.5`) must merge ON
    # TOP OF the defaults, never replace the whole per-unit map — a
    # single-unit override must not silently drop the other units' floors.
    floors_layer = layers.get("floors") or {}
    if not isinstance(floors_layer, dict):
        raise ConfigError("`floors` must be a table of per-unit values (e.g. `fps = 2.0`)")
    floors_raw = _merge(dict(DEFAULT_FLOORS), floors_layer)
    try:
        floors = {unit: float(value) for unit, value in floors_raw.items()}
    except (TypeError, ValueError) as exc:
        raise ConfigError("invalid value in the `[floors]` table", cause=str(exc)) from exc

    # `base_dir` anchors perfvibe's OUTPUT artifacts — the db and the results
    # dir — so they land NEXT TO the flows folder (e.g. inside an app's
    # `e2e/`) instead of wherever the CLI happens to be invoked from, while
    # the config file itself may live at the repo root. A relative `base_dir`
    # is resolved against the config file's own directory (or the working dir
    # when no config file was found). Flows are INPUTS the user points at, not
    # perfvibe outputs, so their `maestro_path` is deliberately NOT re-anchored
    # (that would double-resolve a path `init` already wrote relative to the
    # run dir). When `base_dir` is UNSET the feature is fully INERT: every path
    # stays exactly as before (relative, CWD-based), so existing configs are
    # untouched.
    base_dir_raw = layers.get("base_dir")
    base_dir: Path | None = None
    if base_dir_raw:
        anchor_dir = project_path.parent if project_path is not None else project_dir
        _raw_path = Path(str(base_dir_raw))
        base_dir = _raw_path if _raw_path.is_absolute() else (anchor_dir / _raw_path)

    def _under_base(path_value: str) -> str:
        # Base unset, or an already-absolute path, passes through unchanged;
        # a relative OUTPUT path is anchored under `base_dir`.
        if base_dir is None:
            return path_value
        candidate = Path(path_value)
        return str(candidate if candidate.is_absolute() else base_dir / candidate)

    return PerfConfig(
        db_path=_under_base(str(layers.get("db_path", DEFAULT_DB_PATH))),
        no_color=bool(layers.get("no_color", False)),
        driver=str(layers.get("driver", "maestro")),
        sampler=_normalize_optional_source(layers.get("sampler", "flashlight")),
        marker_source=_normalize_optional_source(layers.get("marker_source", "adb-logcat")),
        bundle_id=layers.get("bundle_id"),
        default_iterations=_typed_int(layers, "default_iterations", DEFAULT_ITERATIONS),
        default_mode=str(layers.get("default_mode", DEFAULT_MODE)),
        device=layers.get("device"),
        results_dir=_under_base(str(layers.get("results_dir", DEFAULT_RESULTS_DIR))),
        base_dir=str(base_dir) if base_dir is not None else None,
        build_variant=layers.get("build_variant"),
        tool_version=str(layers.get("tool_version", DEFAULT_TOOL_VERSION)),
        replay_logcat=layers.get("replay_logcat"),
        replay_flashlight=layers.get("replay_flashlight"),
        reassure_path=str(layers.get("reassure_path", DEFAULT_REASSURE_PATH)),
        flows=flows,
        threshold_pct=_typed_float(layers, "threshold_pct", DEFAULT_THRESHOLD_PCT),
        floors=floors,
        min_baseline_commits=_typed_int(
            layers, "min_baseline_commits", DEFAULT_MIN_BASELINE_COMMITS
        ),
        warmup_k=_typed_int(layers, "warmup_k", DEFAULT_WARMUP_K),
        adaptive_floor=bool(layers.get("adaptive_floor", DEFAULT_ADAPTIVE_FLOOR)),
        # FIX 3 (PR-B review): a 0/negative `baseline_n` would reach the
        # baseline query's `LIMIT ?`, where SQLite treats `LIMIT <= -1` as
        # UNBOUNDED — silently loading the entire history and defeating
        # the bounded-window guarantee. Clamp to a minimum of 1.
        baseline_n=max(1, _typed_int(layers, "baseline_n", DEFAULT_BASELINE_N)),
    )
