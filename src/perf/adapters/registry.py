"""Adapter registry — name-to-factory maps (design §6: "Adapter selection
by registry name"). Each source (driver/sampler/marker) is independently
selectable/optional (spec: "Hexagonal Boundary Enforcement... registry...
SHALL support any adapter being absent except where the minimum-
measurement guarantee applies"). An unknown name raises a clear
`ValueError` BEFORE any adapter is constructed.

PR3 additive extension: `build_context_provider`/`build_store`/
`build_clock` complete the `registry.build_{driver,sampler,marker,
context,store,clock}` composition surface design §1 describes for the CLI.
These three have exactly one implementation each (no name-based selection
needed), so they are plain factory functions rather than name-keyed maps.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from perf.adapters.analyzer_sql import SqlAnalyzer
from perf.adapters.clock_system import SystemClock
from perf.adapters.context_bash_perfmeta import BashRunContextProvider
from perf.adapters.driver_maestro import MaestroDriver
from perf.adapters.driver_manual import ManualDriver
from perf.adapters.driver_replay import ReplayDriver
from perf.adapters.markers_adb_logcat import AdbLogcatMarkerSource
from perf.adapters.sampler_flashlight import FlashlightSampler
from perf.adapters.store_sqlite import SqliteStore
from perf.domain.ports import (
    Analyzer,
    Clock,
    FlowDriver,
    MarkerSource,
    RunContextProvider,
    Store,
    SystemSampler,
)


def _build_maestro_driver(
    *,
    known_flows=None,
    device=None,
    flow_prompts=None,
    runner=None,
    replay_logcat=None,
    replay_flashlight=None,
) -> FlowDriver:
    del flow_prompts, replay_logcat, replay_flashlight  # maestro drives from known_flows + device
    return MaestroDriver(known_flows or {}, device=device, runner=runner)


def _build_manual_driver(
    *,
    known_flows=None,
    device=None,
    flow_prompts=None,
    runner=None,
    replay_logcat=None,
    replay_flashlight=None,
) -> FlowDriver:
    # ManualDriver needs per-flow prompts, NOT known_flows/device. Every driver
    # builder accepts the same COMMON kwargs and ignores the irrelevant ones, so
    # the CLI can build ANY configured driver uniformly — this is what fixes the
    # `driver = "manual"` TypeError (it previously received known_flows/device).
    del known_flows, device, replay_logcat, replay_flashlight
    return ManualDriver(flow_prompts or {}, runner=runner)


def _build_replay_driver(
    *,
    known_flows=None,
    device=None,
    flow_prompts=None,
    runner=None,
    replay_logcat=None,
    replay_flashlight=None,
) -> FlowDriver:
    # ReplayDriver needs the two recorded-capture fixture paths, NOT
    # known_flows/device/flow_prompts/runner — same uniform-kwargs shape as
    # every other driver builder (see `_build_manual_driver`).
    del known_flows, device, flow_prompts, runner
    return ReplayDriver(logcat_path=replay_logcat, flashlight_path=replay_flashlight)


DRIVERS: Mapping[str, Callable[..., FlowDriver]] = {
    "maestro": _build_maestro_driver,
    "manual": _build_manual_driver,
    "replay": _build_replay_driver,
}

SAMPLERS: Mapping[str, Callable[..., SystemSampler]] = {
    "flashlight": FlashlightSampler,
}

MARKER_SOURCES: Mapping[str, Callable[..., MarkerSource]] = {
    "adb-logcat": AdbLogcatMarkerSource,
}


def _build(
    registry: Mapping[str, Callable[..., object]],
    kind: str,
    name: str | None,
    **kwargs,
) -> object | None:
    if name is None:
        return None
    try:
        factory = registry[name]
    except KeyError:
        raise ValueError(
            f"Unknown {kind} {name!r}; available: {sorted(registry)!r}"
        ) from None
    return factory(**kwargs)


def build_driver(name: str | None, **kwargs) -> FlowDriver:
    """Build the named `FlowDriver` from a COMMON set of build kwargs
    (`known_flows`, `device`, `flow_prompts`, `runner`); each driver's builder
    picks what it needs. A `FlowDriver` is always required (spec: a measurement
    source may be optional, the driver itself is not)."""

    if name is None:
        raise ValueError("driver name is required (got None)")
    try:
        builder = DRIVERS[name]
    except KeyError:
        raise ValueError(
            f"Unknown driver {name!r}; available: {sorted(DRIVERS)!r}"
        ) from None
    return builder(**kwargs)


def build_sampler(name: str | None, **kwargs) -> SystemSampler | None:
    """`None` -> no `SystemSampler` selected (spec: independently optional)."""

    return _build(SAMPLERS, "sampler", name, **kwargs)


def build_marker_source(name: str | None, **kwargs) -> MarkerSource | None:
    """`None` -> no `MarkerSource` selected (spec: independently optional)."""

    return _build(MARKER_SOURCES, "marker source", name, **kwargs)


def build_context_provider(**kwargs) -> RunContextProvider:
    """`RunContextProvider` has exactly one implementation — a single
    factory, no name-keyed map needed."""

    return BashRunContextProvider(**kwargs)


def build_store(db_path: str | Path, **kwargs) -> Store:
    """`Store` has exactly one implementation — a single factory, no
    name-keyed map needed. `db_path` opens a LOCAL SQLite file only."""

    return SqliteStore(db_path, **kwargs)


def build_analyzer(store: Store, **params) -> Analyzer:
    """`Analyzer` has exactly one implementation — a single factory, no
    name-keyed map needed (design 'Analyzer factory' decision: rule of
    three), mirroring `build_store`. `params` threads the tuning knobs
    (`threshold_pct`, `floors`, `min_baseline_commits`, `warmup_k`,
    `baseline_n` — `config/loader.py` `PerfConfig` fields, decision #58)
    straight into `SqlAnalyzer`."""

    return SqlAnalyzer(store, **params)


def build_clock() -> Clock:
    """`Clock` has exactly one production implementation — the real wall
    clock; tests inject their own `FrozenClock` fake directly, bypassing
    the registry entirely."""

    return SystemClock()
