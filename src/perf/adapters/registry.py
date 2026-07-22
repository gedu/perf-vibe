"""Adapter registry — name-to-factory maps (design §6: "Adapter selection
by registry name"). Each source (driver/sampler/marker) is independently
selectable/optional (spec: "Hexagonal Boundary Enforcement... registry...
SHALL support any adapter being absent except where the minimum-
measurement guarantee applies"). An unknown name raises a clear
`ValueError` BEFORE any adapter is constructed.
"""

from __future__ import annotations

from typing import Callable, Mapping, Optional

from perf.adapters.driver_maestro import MaestroDriver
from perf.adapters.driver_manual import ManualDriver
from perf.adapters.markers_adb_logcat import AdbLogcatMarkerSource
from perf.adapters.sampler_flashlight import FlashlightSampler
from perf.domain.ports import FlowDriver, MarkerSource, SystemSampler

DRIVERS: Mapping[str, Callable[..., FlowDriver]] = {
    "maestro": MaestroDriver,
    "manual": ManualDriver,
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
    name: Optional[str],
    **kwargs,
) -> Optional[object]:
    if name is None:
        return None
    try:
        factory = registry[name]
    except KeyError:
        raise ValueError(
            f"Unknown {kind} {name!r}; available: {sorted(registry)!r}"
        ) from None
    return factory(**kwargs)


def build_driver(name: str, **kwargs) -> FlowDriver:
    """A `FlowDriver` is always required (spec: at least one measurement
    source may be optional, but the driver itself is not)."""

    driver = _build(DRIVERS, "driver", name, **kwargs)
    if driver is None:
        raise ValueError("driver name is required (got None)")
    return driver


def build_sampler(name: Optional[str], **kwargs) -> Optional[SystemSampler]:
    """`None` -> no `SystemSampler` selected (spec: independently optional)."""

    return _build(SAMPLERS, "sampler", name, **kwargs)


def build_marker_source(name: Optional[str], **kwargs) -> Optional[MarkerSource]:
    """`None` -> no `MarkerSource` selected (spec: independently optional)."""

    return _build(MARKER_SOURCES, "marker source", name, **kwargs)
