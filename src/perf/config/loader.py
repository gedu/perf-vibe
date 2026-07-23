"""Layered configuration loader (design §14, spec "CLI Options and
Configuration Surface").

Precedence, highest to lowest: CLI flags > env (`PERF_DB`, `NO_COLOR`,
`MAESTRO_DEVICE`) > project flow config (`perf.toml`/`.perf.toml` in the
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

__all__ = [
    "FlowConfig",
    "PerfConfig",
    "load_config",
    "GLOBAL_CONFIG_PATH",
    "DEFAULT_ITERATIONS",
    "DEFAULT_THRESHOLD_PCT",
    "DEFAULT_FLOORS",
    "DEFAULT_MIN_BASELINE_COMMITS",
    "DEFAULT_WARMUP_K",
    "DEFAULT_BASELINE_N",
]

DEFAULT_ITERATIONS = 10
DEFAULT_DB_PATH = "perf.db"
DEFAULT_RESULTS_DIR = "results"
DEFAULT_MODE = "warm"
DEFAULT_TOOL_VERSION = "0.1.0"

# Compare tuning defaults (design Rev 2 "Tuning defaults", decision #58):
# conservative/low-noise so the tool doesn't cry wolf — all overridable
# via `perf.toml` / CLI flags.
DEFAULT_THRESHOLD_PCT = 5.0
DEFAULT_FLOORS: Mapping[str, float] = {"ms": 5.0, "mb": 5.0, "pct": 3.0, "fps": 2.0}
DEFAULT_MIN_BASELINE_COMMITS = 3
DEFAULT_WARMUP_K = 1
DEFAULT_BASELINE_N = 10

GLOBAL_CONFIG_PATH = Path.home() / ".config" / "perf" / "config.toml"
PROJECT_CONFIG_FILENAMES: tuple[str, ...] = ("perf.toml", ".perf.toml")


@dataclass(frozen=True)
class FlowConfig:
    """One config-known flow (spec: `flow_name` must be validated against
    this set BEFORE any driver invocation)."""

    name: str
    maestro_path: Optional[str] = None
    prompt: Optional[str] = None


@dataclass(frozen=True)
class PerfConfig:
    """Fully resolved, layered configuration for one CLI invocation."""

    db_path: str = DEFAULT_DB_PATH
    no_color: bool = False
    driver: str = "maestro"
    sampler: Optional[str] = "flashlight"
    marker_source: Optional[str] = "adb-logcat"
    bundle_id: Optional[str] = None
    default_iterations: int = DEFAULT_ITERATIONS
    default_mode: str = DEFAULT_MODE
    device: Optional[str] = None
    results_dir: str = DEFAULT_RESULTS_DIR
    build_variant: Optional[str] = None
    tool_version: str = DEFAULT_TOOL_VERSION
    replay_logcat: Optional[str] = None
    replay_flashlight: Optional[str] = None
    flows: Mapping[str, FlowConfig] = field(default_factory=dict)

    # ===== compare tuning knobs (design Rev 2/3, decision #58) =====
    threshold_pct: float = DEFAULT_THRESHOLD_PCT
    floors: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_FLOORS))
    min_baseline_commits: int = DEFAULT_MIN_BASELINE_COMMITS
    warmup_k: int = DEFAULT_WARMUP_K
    baseline_n: int = DEFAULT_BASELINE_N


def _read_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


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


def _find_project_config(start_dir: Path) -> Optional[Path]:
    for filename in PROJECT_CONFIG_FILENAMES:
        candidate = start_dir / filename
        if candidate.is_file():
            return candidate
    return None


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
    cli_db: Optional[str] = None,
    cli_config_path: Optional[str] = None,
    cli_no_color: Optional[bool] = None,
    cli_device: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    project_dir: Optional[Path] = None,
) -> PerfConfig:
    """Resolve the layered config (design §14). `env` and `project_dir` are
    injectable for tests — production callers omit both and get
    `os.environ` / `Path.cwd()`."""

    env = env if env is not None else os.environ
    project_dir = project_dir if project_dir is not None else Path.cwd()

    layers: dict = {}
    layers = _merge(layers, _read_toml(GLOBAL_CONFIG_PATH))

    project_path = (
        Path(cli_config_path) if cli_config_path is not None else _find_project_config(project_dir)
    )
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
    flows = _build_flows(flows_raw)

    # Partial `[floors]` overrides (e.g. only `fps = 1.5`) must merge ON
    # TOP OF the defaults, never replace the whole per-unit map — a
    # single-unit override must not silently drop the other units' floors.
    floors_raw = _merge(dict(DEFAULT_FLOORS), layers.get("floors") or {})
    floors = {unit: float(value) for unit, value in floors_raw.items()}

    return PerfConfig(
        db_path=str(layers.get("db_path", DEFAULT_DB_PATH)),
        no_color=bool(layers.get("no_color", False)),
        driver=str(layers.get("driver", "maestro")),
        sampler=layers.get("sampler", "flashlight"),
        marker_source=layers.get("marker_source", "adb-logcat"),
        bundle_id=layers.get("bundle_id"),
        default_iterations=int(layers.get("default_iterations", DEFAULT_ITERATIONS)),
        default_mode=str(layers.get("default_mode", DEFAULT_MODE)),
        device=layers.get("device"),
        results_dir=str(layers.get("results_dir", DEFAULT_RESULTS_DIR)),
        build_variant=layers.get("build_variant"),
        tool_version=str(layers.get("tool_version", DEFAULT_TOOL_VERSION)),
        replay_logcat=layers.get("replay_logcat"),
        replay_flashlight=layers.get("replay_flashlight"),
        flows=flows,
        threshold_pct=float(layers.get("threshold_pct", DEFAULT_THRESHOLD_PCT)),
        floors=floors,
        min_baseline_commits=int(layers.get("min_baseline_commits", DEFAULT_MIN_BASELINE_COMMITS)),
        warmup_k=int(layers.get("warmup_k", DEFAULT_WARMUP_K)),
        # FIX 3 (PR-B review): a 0/negative `baseline_n` would reach the
        # baseline query's `LIMIT ?`, where SQLite treats `LIMIT <= -1` as
        # UNBOUNDED — silently loading the entire history and defeating
        # the bounded-window guarantee. Clamp to a minimum of 1.
        baseline_n=max(1, int(layers.get("baseline_n", DEFAULT_BASELINE_N))),
    )
