"""Pure domain value objects for the `perf` tool — REVISION 2.

PURE MODULE — no adapter imports, no I/O. Every value object here is an
immutable (`frozen=True`) dataclass. See `.claude/skills/perf-cli-standards/
SKILL.md` rule 1 (hexagonal layering) and rule 2 (domain modeling).

Rev 2 (design `perf-cli/design/perf-run` #31): generalizes `Marker` to
arbitrary metric names (text + JSON logcat origin), expands `SystemSample`
to the full per-iteration Flashlight aggregate shape, adds direction
metadata (`higher_is_better`) to `Metric`, adds `raw_report_path` to `Run`,
and introduces the compose-time value objects (`DriverCommand`,
`SamplerCommand`, `CaptureSpec`, `ExecutionPlan`, `DriverResult`,
`MarkerParseResult`) that resolve the Flashlight-wraps-Maestro coupling as
pure data (design §1) — no composite adapter, no I/O here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Only for mypy's benefit — never imported at runtime (see the docstring
    # on `CompareResult.calibration` below for why the runtime import is
    # deliberately avoided).
    from perf.domain.calibration import CalibrationReport


class LoopMode(StrEnum):
    """Who owns the iteration loop for an assembled `ExecutionPlan`
    (design §1). `TOOL_MANAGED` when a `SystemSampler.wrap()` result
    declares `manages_iterations=True` (e.g. Flashlight `--iterationCount`);
    `DRIVER_MANAGED` otherwise (the driver itself loops or prompts `n`
    times over the inner command)."""

    TOOL_MANAGED = "tool_managed"
    DRIVER_MANAGED = "driver_managed"


# Metrics whose "good" direction is UP; every other metric name defaults to
# lower-is-better (decision #39: durations/RAM/CPU all improve by dropping).
_HIGHER_IS_BETTER_METRICS = frozenset({"fps_avg", "fps_min"})


def default_higher_is_better(metric_name: str) -> bool:
    """Direction default for a metric name (decision #39): FPS metrics are
    higher-is-better; every duration/RAM/CPU/marker metric — including
    arbitrary marker names — defaults to lower-is-better."""

    return metric_name in _HIGHER_IS_BETTER_METRICS


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
    description: str | None = None


@dataclass(frozen=True)
class Metric:
    """Dimension: a metric name (marker name or system-sample aggregate,
    `metric` table, §9.2). Rev 2 adds `higher_is_better` direction
    metadata (decision #39) — RUN persists it, COMPARE consumes it."""

    name: str  # arbitrary marker name, or a system-sample aggregate like 'fps_avg'
    unit: str = "ms"
    higher_is_better: bool = False


@dataclass(frozen=True)
class Marker:
    """A single in-app timing marker captured from logcat (design §4).

    Rev 2 generalizes this from a hardcoded route-template shape to an
    arbitrary `(name, value, unit)` triple — both the text form
    (`[PERF] <name>: <n>ms`) and the JSON form (`[PERF] {json}`) normalize
    to this same shape. No metric name is hardcoded in the domain."""

    name: str
    value: float
    unit: str = "ms"


@dataclass(frozen=True)
class SystemSample:
    """Per-iteration Flashlight system metrics, aggregated from the raw
    per-sample time-series (discovery #37, design §3). No network fields —
    that is Embrace's domain and MUST NEVER be modeled here.

    `total_time_ms`/`start_time_ms` come straight from the iteration's own
    `time`/`startTime` fields; the rest are aggregated (avg + min/peak)
    over `measures[]`. All metric fields are `Optional` — an iteration with
    an empty `measures[]` still yields `total_time_ms`/`start_time_ms`."""

    iteration_idx: int
    total_time_ms: float | None
    start_time_ms: float | None
    fps_avg: float | None
    fps_min: float | None
    ram_avg_mb: float | None
    ram_peak_mb: float | None
    cpu_avg_pct: float | None
    cpu_peak_pct: float | None


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
    git_commit: str | None
    git_branch: str | None
    app_version: str | None
    is_dev_bundle: bool | None
    bundle_source: str | None
    build_variant: str | None
    tool_version: str


@dataclass(frozen=True)
class Run:
    """A single persisted run (`run` fact table, §9.2). Constructed after
    ingestion assigns identity — `run_id` is None before the store persists
    it. Rev 2 adds `raw_report_path`: the on-disk Flashlight results JSON
    (one report per run), `None` when no `SystemSampler` was active."""

    flow_name: str
    device_key: str
    started_at: str  # ISO-8601 UTC
    iterations: int
    mode: str  # 'warm' | 'cold'
    context: RunContext
    raw_report_path: str | None = None
    run_id: int | None = None


@dataclass(frozen=True)
class Measure:
    """A single persisted duration measurement (`measure` fact table, §9.2).
    Measures hang off the run, never the iteration — see §8/§9.2: the
    logcat stream is flat and cannot be reliably bucketed into Flashlight
    iterations."""

    metric_name: str
    duration_ms: float
    run_id: int | None = None


@dataclass(frozen=True)
class Verdict:
    """The regression/compare verdict (§10, design Rev 3 "Verdict
    carrier"). `run` never produces or consumes this — it exists here for
    the shared domain contract `compare` (`domain/regression.py`) builds.

    Rev 3 extends `status` to the full 4-state classification and adds
    additive pure data fields so ONE lossless carrier feeds both the
    pretty renderer (sparkline, latest-vs-baseline line) and the `--json`
    contract — all new fields default safely so existing positional/
    keyword construction (e.g. `run`-era tests) keeps working unchanged.

    PR-C adds `floor` (the ACTIVE absolute floor `regression.classify` used
    to gate this verdict) so the `--json` contract can report it per-metric
    even on `insufficient-data` — floor is config-derived, not a symptom of
    history depth, so it threads through every classification path.
    """

    metric_name: str
    delta_pct: float
    threshold_pct: float
    status: str  # 'improvement' | 'stable' | 'regression' | 'insufficient-data'
    latest_value: float | None = None
    baseline_value: float | None = None
    unit: str = "ms"
    sample_n: int = 0
    baseline_commit_n: int = 0
    series: Sequence[float] = ()
    floor: float = 0.0


@dataclass(frozen=True)
class CompareResult:
    """Single additive carrier `Analyzer.compare_latest` returns (design
    "Verdict carrier" decision, resolved at tasks #59): every metric's
    `Verdict` plus the config-sanity `CalibrationReport` (design "One
    query, two consumers" — computed from the SAME windowed baseline rows
    the verdicts use, never a second query). `calibration`'s annotation
    stays a forward-reference STRING and is intentionally never imported
    here: `domain/calibration.py` imports `domain/regression.py`, which
    imports THIS module — importing `calibration` here would create an
    import cycle (model -> calibration -> regression -> model). Frozen +
    pure; `run` never builds or consumes this, so it stays additive."""

    verdicts: Sequence[Verdict]
    # Deliberate forward-reference string (see the docstring above). Importing
    # perf.domain.calibration here would create the cycle
    # model -> calibration -> regression -> model, so the name is intentionally
    # never bound in this module — hence the suppression below.
    calibration: CalibrationReport


@dataclass(frozen=True)
class RunPoint:
    """One per-run baseline observation (design Rev 3 "Bounded baseline
    query shape" — `baseline_points` returns rows batched across a whole
    metric-family). Pre-collapse: repeated same-commit runs are NOT yet
    merged — `domain/statistics.median_by_commit` does that. `metric_name`
    lets one query serve every metric of a family (measure/system_sample)
    at once, per the Rev 3 query-count budget."""

    git_commit: str
    metric_name: str
    value: float
    started_at: str  # ISO-8601 UTC, for chronological ordering


# ===== Rev 2 compose-time value objects (design §1) =====


@dataclass(frozen=True)
class DriverCommand:
    """A `FlowDriver`'s pure, compose-time contribution: the inner test
    command for one iteration (`argv`), or `None` when the driver is
    manual and has no automated command — in which case `prompt` carries
    the instruction text shown to the user."""

    argv: Sequence[str] | None
    automated: bool
    prompt: str | None = None


@dataclass(frozen=True)
class SamplerCommand:
    """A `SystemSampler`'s pure, compose-time contribution: how it wraps
    the inner command (e.g. Flashlight's `--testCommand`) and where the
    resulting artifact will be written. `manages_iterations=True` means
    this wrap OWNS the iteration loop (e.g. Flashlight `--iterationCount`),
    selecting `LoopMode.TOOL_MANAGED` for the assembled `ExecutionPlan`."""

    argv: Sequence[str]
    results_path: str
    manages_iterations: bool


@dataclass(frozen=True)
class CaptureSpec:
    """A `MarkerSource`'s pure, compose-time contribution: the logcat
    capture command run in parallel with the drive step."""

    argv: Sequence[str]


@dataclass(frozen=True)
class ExecutionPlan:
    """Pure composition of one run's execution (design §1, steps 5-7) —
    the single point where the Flashlight-wraps-Maestro coupling is
    resolved as DATA, never by one adapter importing another. `command` is
    what the driver actually spawns (the wrap argv if a sampler wrapped the
    inner command, else the inner argv itself, or `None` for a manual,
    unwrapped driver)."""

    command: Sequence[str] | None
    inner: DriverCommand
    loop_mode: LoopMode
    iterations: int
    capture: CaptureSpec | None
    results_path: str | None


@dataclass(frozen=True)
class DriverResult:
    """What `FlowDriver.drive(plan)` returns after executing an
    `ExecutionPlan`: per-iteration outcomes plus the captured logcat lines
    (empty when no `MarkerSource` is active).

    Fix (resilience review): `capture_failed` distinguishes a DEAD/failed
    parallel capture (e.g. `adb logcat` exiting non-zero because of a
    multi-device error) from a healthy capture that simply observed zero
    marker lines — without this flag both cases looked identical
    ("no markers"), silently masking the failure. `diagnostics` carries
    bounded, secret-scrubbed stderr/output text explaining WHICH
    tool/flow/device failed and WHY (`None` on a clean success)."""

    ok: bool
    iteration_outcomes: Sequence[str]
    logcat_lines: Sequence[str]
    capture_failed: bool = False
    diagnostics: str | None = None


@dataclass(frozen=True)
class MarkerParseResult:
    """Result of `MarkerSource.parse()`: the normalized markers plus
    whether coverage was partial — i.e. fewer complete
    `markStart`/`markEnd` occurrences were captured than `run.iterations`
    (design §4 / spec: markStart-without-markEnd)."""

    markers: Sequence[Marker]
    partial_coverage: bool


@dataclass(frozen=True)
class SystemSampleParseResult:
    """Result of `SystemSampler.parse()`: the parsed per-iteration samples
    plus whether coverage was partial.

    Fix (resilience review): Flashlight's `status` (top-level AND
    per-iteration) MUST be honored — a FAILURE/timed-out iteration must
    never be aggregated and persisted as if it succeeded. Failed iterations
    are excluded from `samples` and flagged via `partial_coverage=True`
    rather than silently vanishing or poisoning the regression history."""

    samples: Sequence[SystemSample]
    partial_coverage: bool


def compose_execution_plan(
    inner: DriverCommand,
    *,
    iterations: int,
    wrap: SamplerCommand | None = None,
    capture: CaptureSpec | None = None,
) -> ExecutionPlan:
    """Pure compose-time assembly of an `ExecutionPlan` (design §1, steps
    5-7). Resolves the Flashlight-wraps-Maestro coupling as data: if `wrap`
    declares `manages_iterations`, the OS-level loop is `TOOL_MANAGED`
    (single spawn of the wrap command, e.g. Flashlight `--iterationCount`);
    otherwise the driver itself loops/prompts `iterations` times
    (`DRIVER_MANAGED`) over `inner` (or the manual prompt when
    `inner.argv is None`). No I/O — this only shapes value objects already
    produced by each adapter's pure `command()`/`wrap()`/`capture_spec()`."""

    command = wrap.argv if wrap is not None else inner.argv
    loop_mode = (
        LoopMode.TOOL_MANAGED
        if wrap is not None and wrap.manages_iterations
        else LoopMode.DRIVER_MANAGED
    )
    results_path = wrap.results_path if wrap is not None else None
    return ExecutionPlan(
        command=command,
        inner=inner,
        loop_mode=loop_mode,
        iterations=iterations,
        capture=capture,
        results_path=results_path,
    )
