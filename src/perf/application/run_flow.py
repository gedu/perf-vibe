"""`RunFlowUseCase` — application layer orchestration for `perf run`
(design §1, §5; spec "Composable Optional Sources", "Exit Code
Discipline"). PURE orchestration: no I/O of its own, no adapter imports
(SKILL rule 1) — every side effect happens behind the six ports
(`domain/ports.py`), injected by the caller (the CLI, via the config
loader + adapter registry). This module MUST NOT import `Analyzer` or any
regression/compare logic — `run` is PERSIST-ONLY (spec "Scope Note: RUN vs
COMPARE").

Error mapping (this is where PR2's resilience hardening pays off):
  - No measurement source configured (guard, before any device touch),
    or a bad/unknown flow name -> `UsageError` (CLI maps to exit 2).
  - Device offline / driver failure / `DriverResult.capture_failed` /
    a `SystemSampler.parse()` failure (e.g. Flashlight's
    `FlashlightParseError`) / zero markers AND zero samples captured ->
    `RunFailedError` (CLI maps to exit 3), carrying bounded diagnostics
    for the CLI to surface. This use-case NEVER raises anything the CLI
    would let escape as exit code 1 (SKILL rule 7).
  - Success -> `RunFlowResult`, persisted in exactly ONE `Store.save_run`
    transaction (§9.6 — any exception there rolls back to zero rows).

A future auto-`compare` is explicitly NOT this use-case's concern: the CLI
command layer (`cli/commands/run.py`) may chain a future `CompareUseCase`
after a successful `execute()` call returns, without this class ever
changing (design §5: "future auto-compare is a CLI-layer seam... not a
use-case dep").
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from perf.domain.model import (
    ExecutionPlan,
    Marker,
    RunContext,
    SystemSample,
    compose_execution_plan,
)
from perf.domain.ports import (
    Clock,
    FlowDriver,
    MarkerSource,
    RunContextProvider,
    Store,
    SystemSampler,
)

__all__ = [
    "RunFailedError",
    "RunFlowRequest",
    "RunFlowResult",
    "RunFlowUseCase",
    "UsageError",
]


class UsageError(Exception):
    """Bad invocation, resolved BEFORE any device/tool interaction —
    the CLI maps this to exit code 2 (spec "Exit Code Discipline")."""


class RunFailedError(Exception):
    """Runtime/tooling failure during or after driving the flow — the CLI
    maps this to exit code 3. `diagnostics` carries bounded, secret-scrubbed
    detail (already scrubbed by the adapter that produced it, e.g.
    `DriverResult.diagnostics`) for the CLI to surface to the user."""

    def __init__(self, message: str, *, diagnostics: str | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


@dataclass(frozen=True)
class RunFlowRequest:
    """Everything `RunFlowUseCase.execute()` needs for one invocation.
    Adapter SELECTION already happened at composition time (config loader
    + registry, §14/§6) — this request only carries per-invocation
    parameters, never adapter instances."""

    flow_name: str
    iterations: int
    restart: bool  # True forces cold; warm is the default (spec "Flow Execution Loop")
    env: Mapping[str, str] | None = None  # secret forwarding (e.g. PASSWORD), never logged
    results_dir: str | None = None  # required only when a SystemSampler is active

    @property
    def mode(self) -> str:
        return "cold" if self.restart else "warm"


@dataclass(frozen=True)
class RunFlowResult:
    """What a successful `execute()` returns — enough for the CLI to build
    BOTH the pretty confirmation and the `--json` (`contracts/json_v1`)
    payload without re-querying the store (the use-case already holds the
    just-parsed markers/samples in memory; re-reading them back from SQL
    would be redundant I/O split across two layers)."""

    run_id: int
    flow_name: str
    device_key: str
    git_commit: str | None
    is_dev_bundle: bool | None
    source: str  # 'ci' | 'local:<user>' (RunContext.source — same value persisted on `run.source`)
    mode: str
    iterations: int
    markers: Sequence[Marker]
    samples: Sequence[SystemSample]
    raw_report_path: str | None
    partial_coverage: bool


class RunFlowUseCase:
    """Orchestrates one `perf run` invocation (design §1 steps 1-13).
    Depends ONLY on the six ports — never an adapter module (SKILL rule 1;
    enforced by `tests/unit/test_domain_boundary.py`-style static checks
    extended to `application/` in this PR)."""

    def __init__(
        self,
        *,
        driver: FlowDriver,
        sampler: SystemSampler | None,
        marker_source: MarkerSource | None,
        context_provider: RunContextProvider,
        store: Store,
        clock: Clock,
    ) -> None:
        self._driver = driver
        self._sampler = sampler
        self._marker_source = marker_source
        self._context_provider = context_provider
        self._store = store
        self._clock = clock

    def execute(self, request: RunFlowRequest) -> RunFlowResult:
        # Step 1 (design §1): minimum-measurement guard — BEFORE any device
        # interaction (spec "No measurement source configured").
        if self._sampler is None and self._marker_source is None:
            raise UsageError(
                "At least one measurement source (a system sampler or a "
                "marker source) must be configured before running a flow; "
                "none is active."
            )
        if self._sampler is not None and not request.results_dir:
            raise UsageError(
                "A results directory is required when a system sampler is "
                "configured (nowhere to write/read its results artifact)."
            )

        mode = request.mode

        # Step 2: inner command (pure). A bad/unknown flow name is a usage
        # error, not a runtime one — the driver validates BEFORE any spawn
        # (SKILL rule 5) and raises `ValueError`, remapped here.
        try:
            inner = self._driver.command(
                request.flow_name,
                mode=mode,
                restart=request.restart,
                env=request.env,
            )
        except ValueError as exc:
            raise UsageError(str(exc)) from exc

        # Step 3-4: candidate results path + sampler wrap (pure). `wrap` may
        # be `None` even with a sampler configured (e.g. manual driver +
        # Flashlight is a documented, unbuilt seam — `FlashlightSampler.
        # wrap()` returns `None` when `inner.argv is None`).
        candidate_results_path: str | None = None
        wrap = None
        if self._sampler is not None:
            candidate_results_path = self._build_results_path(request)
            wrap = self._sampler.wrap(
                inner,
                iterations=request.iterations,
                restart=request.restart,
                results_path=candidate_results_path,
            )

        # Step 6: marker capture spec (pure).
        capture = self._marker_source.capture_spec() if self._marker_source is not None else None

        # Step 7: compose the plan (pure) — the ONLY place the
        # Flashlight-wraps-Maestro coupling is resolved, as data.
        plan: ExecutionPlan = compose_execution_plan(
            inner,
            iterations=request.iterations,
            wrap=wrap,
            capture=capture,
        )

        # Step 8: drive (I/O) — device offline / tool failure surfaces via
        # `DriverResult.ok=False` or a raised `OSError` (missing binary).
        try:
            driver_result = self._driver.drive(plan)
        except OSError as exc:
            raise RunFailedError(
                f"Failed to execute flow {request.flow_name!r}: {exc}",
                diagnostics=str(exc),
            ) from exc

        if driver_result.capture_failed:
            raise RunFailedError(
                f"Marker capture failed while running {request.flow_name!r} "
                "(dead/failed logcat capture) — aborting before persist.",
                diagnostics=driver_result.diagnostics,
            )
        if not driver_result.ok:
            raise RunFailedError(
                f"Flow {request.flow_name!r} did not complete successfully "
                f"(iteration outcomes: {list(driver_result.iteration_outcomes)!r}).",
                diagnostics=driver_result.diagnostics,
            )

        # Step 9: parse samples (only if the sampler actually produced an
        # artifact — `wrap is not None`) and markers.
        samples: Sequence[SystemSample] = ()
        samples_partial = False
        if wrap is not None:
            try:
                sample_result = self._sampler.parse(wrap.results_path)  # type: ignore[union-attr]
            except Exception as exc:
                # imports no adapter (SKILL rule 1), so it cannot catch a
                # specific adapter exception type (e.g. FlashlightParseError)
                # by name; ANY parse failure of the sampler's own artifact is
                # a runtime/tooling failure (spec: "FlashlightParseError...
                # runtime/tooling error (exit 3)").
                raise RunFailedError(
                    f"Failed to parse system sampler results for {request.flow_name!r}: {exc}",
                    diagnostics=str(exc),
                ) from exc
            samples = sample_result.samples
            samples_partial = sample_result.partial_coverage

        markers: Sequence[Marker] = ()
        markers_partial = False
        if self._marker_source is not None:
            marker_result = self._marker_source.parse(
                driver_result.logcat_lines, iterations=request.iterations
            )
            markers = marker_result.markers
            markers_partial = marker_result.partial_coverage

        # Step 10: no data captured -> runtime/tooling failure, no run row.
        if not samples and not markers:
            raise RunFailedError(
                f"No measurements captured for flow {request.flow_name!r} — "
                "both configured sources yielded zero data.",
                diagnostics=driver_result.diagnostics,
            )

        # Step 11: run context (bash-owned facts + app-owned [PERF-META]).
        # `logcat_lines` is passed through when the concrete provider
        # accepts it (documented optional extension on
        # `BashRunContextProvider.context()`); the Protocol itself declares
        # a zero-arg `context()`, so a conformant fake without that
        # extension must keep working too.
        ctx = self._get_context(driver_result.logcat_lines)

        raw_report_path = wrap.results_path if wrap is not None else None

        # Step 12: persist exactly ONE run in the Store's single
        # transaction (§9.6) — any exception there rolls back to zero rows;
        # this use-case does not (and must not) catch it, so a store
        # failure propagates as a runtime error and store.save_run's own
        # rollback guarantees no partial write, keeping `run` never
        # emitting exit code 1 as long as the CLI maps any unexpected
        # exception to exit 3 rather than letting it fall through to
        # Python's default.
        run_id = self._store.save_run(
            ctx,
            request.flow_name,
            request.iterations,
            mode,
            ctx.source,
            markers,
            samples,
            raw_report_path,
        )

        return RunFlowResult(
            run_id=run_id,
            flow_name=request.flow_name,
            device_key=ctx.device_key,
            git_commit=ctx.git_commit,
            is_dev_bundle=ctx.is_dev_bundle,
            source=ctx.source,
            mode=mode,
            iterations=request.iterations,
            markers=markers,
            samples=samples,
            raw_report_path=raw_report_path,
            partial_coverage=bool(samples_partial or markers_partial),
        )

    def _get_context(self, logcat_lines: Sequence[str]) -> RunContext:
        try:
            return self._context_provider.context(logcat_lines)  # type: ignore[call-arg]
        except TypeError:
            # The bare `RunContextProvider` Protocol declares a zero-arg
            # `context()`; only fall back when the concrete implementation
            # does not accept the optional `logcat_lines` extension.
            return self._context_provider.context()

    def _build_results_path(self, request: RunFlowRequest) -> str:
        # Pure string composition — no filesystem access here (the sampler/
        # driver own writing/reading the actual artifact). A timestamp slug
        # (from the injected `Clock`, not wall-clock I/O) keeps successive
        # runs of the same flow/mode from clobbering each other's report
        # (design §1 step 3: `results_dir/f"{flow}-{mode}-{ts}.json"`).
        assert request.results_dir is not None
        slug = request.flow_name.replace("/", "-")
        ts = _filename_slug(self._clock.now_utc_iso())
        return f"{request.results_dir.rstrip('/')}/{slug}-{request.mode}-{ts}.json"


def _filename_slug(iso_timestamp: str) -> str:
    """Turns an ISO-8601 timestamp into a filesystem-safe slug (no `:`,
    `.`, `+` — all invalid or awkward in filenames on common filesystems)."""

    return iso_timestamp.replace(":", "").replace(".", "").replace("+", "-")
