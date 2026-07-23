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
from typing import TypeVar

from perf.adapters.analyzer_sql import SqlAnalyzer
from perf.adapters.clock_system import SystemClock
from perf.adapters.context_bash_perfmeta import BashRunContextProvider
from perf.adapters.driver_maestro import MaestroDriver
from perf.adapters.driver_manual import ManualDriver
from perf.adapters.driver_replay import ReplayDriver
from perf.adapters.markers_adb_logcat import AdbLogcatMarkerSource
from perf.adapters.process import SubprocessRunner
from perf.adapters.sampler_flashlight import FlashlightSampler
from perf.adapters.store_sqlite import SqliteStore
from perf.domain.ports import (
    Analyzer,
    Clock,
    FlowDriver,
    MarkerSource,
    RunContextProvider,
    SystemSampler,
)

_T = TypeVar("_T")


def _build_maestro_driver(
    *,
    known_flows: Mapping[str, str] | None = None,
    device: str | None = None,
    flow_prompts: Mapping[str, str] | None = None,
    runner: SubprocessRunner | None = None,
    replay_logcat: str | Path | None = None,
    replay_flashlight: str | Path | None = None,
) -> FlowDriver:
    del flow_prompts, replay_logcat, replay_flashlight  # maestro drives from known_flows + device
    return MaestroDriver(known_flows or {}, device=device, runner=runner)


def _build_manual_driver(
    *,
    known_flows: Mapping[str, str] | None = None,
    device: str | None = None,
    flow_prompts: Mapping[str, str] | None = None,
    runner: SubprocessRunner | None = None,
    replay_logcat: str | Path | None = None,
    replay_flashlight: str | Path | None = None,
) -> FlowDriver:
    # ManualDriver needs per-flow prompts, NOT known_flows/device. Every driver
    # builder accepts the same COMMON kwargs and ignores the irrelevant ones, so
    # the CLI can build ANY configured driver uniformly — this is what fixes the
    # `driver = "manual"` TypeError (it previously received known_flows/device).
    del known_flows, device, replay_logcat, replay_flashlight
    return ManualDriver(flow_prompts or {}, runner=runner)


def _build_replay_driver(
    *,
    known_flows: Mapping[str, str] | None = None,
    device: str | None = None,
    flow_prompts: Mapping[str, str] | None = None,
    runner: SubprocessRunner | None = None,
    replay_logcat: str | Path | None = None,
    replay_flashlight: str | Path | None = None,
) -> FlowDriver:
    # ReplayDriver needs the two recorded-capture fixture paths, NOT
    # known_flows/device/flow_prompts/runner — same uniform-kwargs shape as
    # every other driver builder (see `_build_manual_driver`).
    del known_flows, device, flow_prompts, runner
    # `replay_logcat` is required by `ReplayDriver` but optional here (same
    # uniform-kwargs shape as every other builder — see above). A missing
    # value is a pre-existing config error left to surface as `ReplayDriver`/
    # `Path`'s own TypeError at construction time; adding a guard here would
    # change that failure's control flow/message, which is out of scope for
    # this typing-only pass.
    return ReplayDriver(
        logcat_path=replay_logcat,  # type: ignore[arg-type]
        flashlight_path=replay_flashlight,
    )


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
    registry: Mapping[str, Callable[..., _T]],
    kind: str,
    name: str | None,
    **kwargs: object,
) -> _T | None:
    # Generic over `_T` so each caller (`build_sampler` -> `SystemSampler`,
    # `build_marker_source` -> `MarkerSource`) gets its own honest return
    # type back, instead of the common-denominator `object` a single
    # concrete return type would force.
    if name is None:
        return None
    try:
        factory = registry[name]
    except KeyError:
        raise ValueError(f"Unknown {kind} {name!r}; available: {sorted(registry)!r}") from None
    return factory(**kwargs)


def build_driver(name: str | None, **kwargs: object) -> FlowDriver:
    """Build the named `FlowDriver` from a COMMON set of build kwargs
    (`known_flows`, `device`, `flow_prompts`, `runner`); each driver's builder
    picks what it needs. A `FlowDriver` is always required (spec: a measurement
    source may be optional, the driver itself is not)."""

    if name is None:
        raise ValueError("driver name is required (got None)")
    try:
        builder = DRIVERS[name]
    except KeyError:
        raise ValueError(f"Unknown driver {name!r}; available: {sorted(DRIVERS)!r}") from None
    return builder(**kwargs)


def build_sampler(name: str | None, **kwargs: object) -> SystemSampler | None:
    """`None` -> no `SystemSampler` selected (spec: independently optional)."""

    return _build(SAMPLERS, "sampler", name, **kwargs)


def build_marker_source(name: str | None, **kwargs: object) -> MarkerSource | None:
    """`None` -> no `MarkerSource` selected (spec: independently optional)."""

    return _build(MARKER_SOURCES, "marker source", name, **kwargs)


def build_context_provider(
    *,
    build_variant: str | None = None,
    tool_version: str = "0.0.0",
    device: str | None = None,
    repo_path: str | None = None,
    runner: SubprocessRunner | None = None,
) -> RunContextProvider:
    """`RunContextProvider` has exactly one implementation — a single
    factory, no name-keyed map needed. Kwonly params mirror
    `BashRunContextProvider.__init__` exactly (honest pass-through, no
    `**kwargs`/`object` mismatch against its concretely-typed constructor)."""

    return BashRunContextProvider(
        build_variant=build_variant,
        tool_version=tool_version,
        device=device,
        repo_path=repo_path,
        runner=runner,
    )


def build_store(
    db_path: str | Path,
    *,
    clock: Clock | None = None,
    busy_timeout_ms: int = 5000,
) -> SqliteStore:
    """`Store` has exactly one implementation — a single factory, no
    name-keyed map needed. `db_path` opens a LOCAL SQLite file only. Kwonly
    params mirror `SqliteStore.__init__` (see `build_context_provider`).

    Returns the concrete `SqliteStore`, not the `Store` Protocol: this
    module is the composition root inside `adapters/`, so knowing concrete
    adapter classes is precisely its job (see `build_analyzer` below)."""

    return SqliteStore(db_path, clock=clock, busy_timeout_ms=busy_timeout_ms)


def build_analyzer(
    store: SqliteStore,
    *,
    threshold_pct: float,
    floors: Mapping[str, float],
    min_baseline_commits: int,
    warmup_k: int,
    baseline_n: int,
) -> Analyzer:
    """`Analyzer` has exactly one implementation — a single factory, no
    name-keyed map needed (design 'Analyzer factory' decision: rule of
    three). Kwonly params mirror `SqlAnalyzer.__init__` and thread the
    tuning knobs straight from `config/loader.py`'s `PerfConfig` (decision
    #58).

    Takes `SqliteStore`, not `Store`: `SqlAnalyzer` calls five read-model
    methods (`baseline_measure_points`, `baseline_system_sample_points`,
    `latest_measure_summary`, `latest_run`, `latest_system_sample_points`)
    that live only on `SqliteStore`. If a second `Store` implementation
    ever appears, those five are what a segregated read port would need."""

    return SqlAnalyzer(
        store,
        threshold_pct=threshold_pct,
        floors=floors,
        min_baseline_commits=min_baseline_commits,
        warmup_k=warmup_k,
        baseline_n=baseline_n,
    )


def build_clock() -> Clock:
    """`Clock` has exactly one production implementation — the real wall
    clock; tests inject their own `FrozenClock` fake directly, bypassing
    the registry entirely."""

    return SystemClock()
