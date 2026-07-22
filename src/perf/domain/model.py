"""Pure domain value objects for the `perf` tool.

PURE MODULE — no adapter imports, no I/O. Every value object here is an
immutable (`frozen=True`) dataclass. See `.claude/skills/perf-cli-standards/
SKILL.md` rule 1 (hexagonal layering) and rule 2 (domain modeling).

Mirrors the master design §10 (verbatim value objects: Marker, SystemSample,
RunContext, Verdict) plus the star-schema dimension/fact shapes from §9.2
(Device, Flow, Metric, Run, Measure).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Device:
    """Dimension: a physical or emulated device (`device` table, §9.2)."""

    device_key: str  # 'Pixel 8 Pro|Android 14|physical'
    model: str
    os_version: str
    is_emulator: bool = False


@dataclass(frozen=True)
class Flow:
    """Dimension: a named Maestro flow (`flow` table, §9.2)."""

    name: str  # 'prestamos-warm'
    description: Optional[str] = None


@dataclass(frozen=True)
class Metric:
    """Dimension: a stable metric template name (`metric` table, §9.2)."""

    name: str  # '/loans/details/:id' — stable template, never a raw path with IDs
    unit: str = "ms"


@dataclass(frozen=True)
class Marker:
    """A single in-app timing marker captured from logcat (§10)."""

    metric_name: str  # stable template, e.g. "/loans/details/:id"
    duration_ms: float


@dataclass(frozen=True)
class SystemSample:
    """Per-iteration Flashlight system metrics (§10). No network fields —
    that is Embrace's domain and MUST NEVER be modeled here."""

    iteration_idx: int
    fps_avg: Optional[float]
    cpu_pct_avg: Optional[float]
    ram_mb_avg: Optional[float]


@dataclass(frozen=True)
class RunContext:
    """Run metadata assembled from bash-owned env facts + app-owned
    `[PERF-META]` (§10). `is_dev_bundle` originates ONLY from `[PERF-META]`
    — never inferred from any other signal."""

    device_key: str  # 'Pixel 8 Pro|Android 14|physical'
    model: str
    os_version: str
    is_emulator: bool
    source: str  # 'ci' | 'local:<user>'
    git_commit: Optional[str]
    git_branch: Optional[str]
    app_version: Optional[str]
    is_dev_bundle: Optional[bool]
    bundle_source: Optional[str]
    build_variant: Optional[str]
    tool_version: str


@dataclass(frozen=True)
class Run:
    """A single persisted run (`run` fact table, §9.2). Constructed after
    ingestion assigns identity — `run_id` is None before the store persists
    it."""

    flow_name: str
    device_key: str
    started_at: str  # ISO-8601 UTC
    iterations: int
    mode: str  # 'warm' | 'cold'
    context: RunContext
    run_id: Optional[int] = None


@dataclass(frozen=True)
class Measure:
    """A single persisted duration measurement (`measure` fact table, §9.2).
    Measures hang off the run, never the iteration — see §8/§9.2: the
    logcat stream is flat and cannot be reliably bucketed into Flashlight
    iterations."""

    metric_name: str
    duration_ms: float
    run_id: Optional[int] = None


@dataclass(frozen=True)
class Verdict:
    """The regression/compare verdict (§10). `run` never produces or
    consumes this — it exists here for the shared domain contract that
    `compare` (a later capability) will use."""

    metric_name: str
    delta_pct: float
    threshold_pct: float
    status: str  # 'improvement' | 'stable' | 'regression'
