"""Device-free multi-commit seed for the `perf compare` demo (PR-C task
3.9). Runs the REAL `RunFlowUseCase` (a REAL `ReplayDriver` +
`FlashlightSampler` + `AdbLogcatMarkerSource`) against a REAL `SqliteStore`
several times, varying ONLY `git_commit` (and which recorded fixture pair
is replayed) via a thin FAKE wrapper around the REAL `RunContextProvider`
— the analyzer is NEVER touched here (`tests/integration/
test_cli_compare_replay.py` reuses this exact `seed_into()` function).

`device_key` is resolved through the SAME REAL `BashRunContextProvider`
`perf compare` itself builds at invocation time (`adapters/registry.
build_context_provider`) — NOT a hardcoded fake value — so this demo stays
correct whether it runs on a machine with no device/adb at all (degrades
to `unknown|unknown|physical`, per `adapters/context_bash_perfmeta.py`) or
one with a real device/emulator attached (uses that device's real key):
seed-time and compare-time always agree, whatever the machine reports.

Produces:
  - 4 baseline commits (`c1`..`c4`) replaying `fixtures/baseline-*` — a
    low-noise, stable history for `checkout` (marker), `ttfp` (marker),
    and `fps_avg`/`ram_avg_mb` (Flashlight aggregates).
  - 1 latest commit (`head`) replaying `fixtures/regression-*` — a CLEAR
    `checkout` duration regression (~800ms -> ~1300ms, well past the
    default 5ms floor + 5% threshold), while `ttfp`/`fps_avg` stay stable.

Run directly: `python examples/demo-compare/seed.py` (from anywhere —
every path is resolved relative to THIS file, never the current working
directory).
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

_DEMO_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _DEMO_DIR.parents[1]
_TESTS_DIR = _REPO_ROOT / "tests"
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from fakes import SequentialClock  # noqa: E402
from perf.adapters.clock_system import SystemClock  # noqa: E402
from perf.adapters.driver_replay import ReplayDriver  # noqa: E402
from perf.adapters.markers_adb_logcat import AdbLogcatMarkerSource  # noqa: E402
from perf.adapters.registry import build_context_provider  # noqa: E402
from perf.adapters.sampler_flashlight import FlashlightSampler  # noqa: E402
from perf.adapters.store_sqlite import SqliteStore  # noqa: E402
from perf.application.run_flow import RunFlowRequest, RunFlowUseCase  # noqa: E402
from perf.domain.model import RunContext  # noqa: E402

FIXTURES_DIR = _DEMO_DIR / "fixtures"
DEFAULT_DB_PATH = _DEMO_DIR / "perf.db"
DEFAULT_RESULTS_DIR = _DEMO_DIR / ".demo-results"

FLOW = "demo"

BASELINE_COMMITS = ("c1", "c2", "c3", "c4")
LATEST_COMMIT = "head"


class _CommitOverrideContextProvider:
    """Wraps the REAL `RunContextProvider` and overrides ONLY
    `git_commit` per seeded run — every other field (`device_key`
    included) comes from the REAL provider, so this seed's `device_key`
    always matches whatever `perf compare` itself resolves at invocation
    time, on ANY machine."""

    def __init__(self, real_provider, git_commit: str) -> None:
        self._real_provider = real_provider
        self._git_commit = git_commit

    def context(self, logcat_lines: Sequence[str] = ()) -> RunContext:
        try:
            ctx = self._real_provider.context(logcat_lines)
        except TypeError:
            ctx = self._real_provider.context()
        return replace(ctx, git_commit=self._git_commit)


def _run_once(
    store: SqliteStore,
    *,
    git_commit: str,
    logcat_path: Path,
    flashlight_path: Path,
    results_dir: Path,
) -> int:
    driver = ReplayDriver(logcat_path=logcat_path, flashlight_path=flashlight_path)
    sampler = FlashlightSampler(bundle_id="com.example.demo")
    marker_source = AdbLogcatMarkerSource()
    real_context_provider = build_context_provider(
        build_variant=None, tool_version="0.0.0-demo", device=None
    )
    context_provider = _CommitOverrideContextProvider(real_context_provider, git_commit)

    use_case = RunFlowUseCase(
        driver=driver,
        sampler=sampler,
        marker_source=marker_source,
        context_provider=context_provider,
        store=store,
        clock=SystemClock(),  # only feeds the results-artifact filename slug
    )
    request = RunFlowRequest(
        flow_name=FLOW,
        # 3 iterations/markers per run so `sample_n >= min_baseline_commits`
        # (default 3) — otherwise every metric classifies `insufficient-data`
        # regardless of the actual delta (spec "Insufficient-Data
        # Classification"), which would hide the demo's regression.
        iterations=3,
        restart=False,
        results_dir=str(results_dir),
    )
    result = use_case.execute(request)
    return result.run_id


def seed_into(store: SqliteStore, *, results_dir: Path) -> list[int]:
    """Seeds the baseline commits + the regressing latest commit into an
    already-open `SqliteStore`. Returns the persisted `run_id`s in seed
    order — reused by both the standalone script and the replay e2e test
    so the two never drift apart."""

    results_dir.mkdir(parents=True, exist_ok=True)
    run_ids = [
        _run_once(
            store,
            git_commit=commit,
            logcat_path=FIXTURES_DIR / "baseline-logcat.txt",
            flashlight_path=FIXTURES_DIR / "baseline-flashlight.json",
            results_dir=results_dir,
        )
        for commit in BASELINE_COMMITS
    ]
    run_ids.append(
        _run_once(
            store,
            git_commit=LATEST_COMMIT,
            logcat_path=FIXTURES_DIR / "regression-logcat.txt",
            flashlight_path=FIXTURES_DIR / "regression-flashlight.json",
            results_dir=results_dir,
        )
    )
    return run_ids


def seed(db_path: Path = DEFAULT_DB_PATH, results_dir: Path = DEFAULT_RESULTS_DIR) -> None:
    """CLI entry point: (re)creates `db_path` from scratch and seeds it —
    `SequentialClock` drives `SqliteStore`'s `started_at` so the 5 seeded
    runs sort chronologically in baseline -> latest order, matching the
    commit sequence above (the `RunFlowUseCase`'s OWN clock, above, is a
    separate `SystemClock` — it only feeds filename slugs, never
    `started_at`, so it does not need to be deterministic)."""

    if db_path.exists():
        db_path.unlink()

    store = SqliteStore(db_path, clock=SequentialClock())
    try:
        run_ids = seed_into(store, results_dir=results_dir)
    finally:
        store.close()

    print(f"Seeded {len(run_ids)} runs into {db_path} (flow={FLOW!r}).")
    print(f"Baseline commits: {BASELINE_COMMITS} | latest commit: {LATEST_COMMIT!r}")


if __name__ == "__main__":
    seed()
