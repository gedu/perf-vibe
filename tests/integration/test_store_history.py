"""`history` read-model integration tests (the charting-export seam):
`SqliteStore.history_runs` against a seeded multi-run temp SQLite history.

RED-before-GREEN: written before `history_runs` existed. Proves:
  - runs come back OLDEST→NEWEST (natural chart order),
  - `--limit` keeps the most recent N (still oldest→newest),
  - BOTH metric families are summarized per run — marker measures via the
    `run_metric_summary` view, system_sample aggregates reduced from raw
    per-iteration rows — with correct {p50, p90, n, unit},
  - system_sample units are corrected (fps/mb/pct), not the ingestion 'ms'
    default,
  - it is a RAW per-run summary: NO warm-up discard (n == iteration count),
  - warm/cold mode and `device_key` never mix,
  - an unseen flow/device/mode yields an empty result (never a crash).
"""

from __future__ import annotations

from pathlib import Path

from fakes import SequentialClock
from perf.adapters.store_sqlite import SqliteStore
from perf.domain.model import HistoryRun, Marker, RunContext, SystemSample

FLOW = "checkout"
DEVICE_A = "Pixel 8 Pro|Android 14|physical"
DEVICE_B = "Pixel 6|Android 13|physical"


def _ctx(**overrides) -> RunContext:
    defaults = {
        "device_key": DEVICE_A,
        "model": "Pixel 8 Pro",
        "os_version": "Android 14",
        "is_emulator": False,
        "source": "local:eduardo",
        "git_commit": "c0",
        "git_branch": "main",
        "app_version": "1.0.0",
        "is_dev_bundle": False,
        "bundle_source": "embedded",
        "build_variant": "release",
        "tool_version": "0.1.0",
    }
    defaults.update(overrides)
    return RunContext(**defaults)


def _seed_run(
    store: SqliteStore,
    *,
    git_commit: str,
    checkout_values=(100.0, 200.0, 300.0),
    fps_values=(50.0, 60.0, 70.0),
    ram_values=(100.0, 200.0, 300.0),
    mode: str = "warm",
    device_key: str = DEVICE_A,
    source: str = "local:test",
) -> int:
    ctx = _ctx(git_commit=git_commit, device_key=device_key, source=source)
    markers = [Marker(name="checkout", value=value, unit="ms") for value in checkout_values]
    samples = [
        SystemSample(
            iteration_idx=idx,
            total_time_ms=None,
            start_time_ms=None,
            fps_avg=fps_values[idx],
            fps_min=None,
            ram_avg_mb=ram_values[idx] if idx < len(ram_values) else None,
            ram_peak_mb=None,
            cpu_avg_pct=None,
            cpu_peak_pct=None,
        )
        for idx in range(len(fps_values))
    ]
    return store.save_run(ctx, FLOW, len(fps_values), mode, source, markers, samples, None)


def _metric(run: HistoryRun, name: str):
    return next((m for m in run.metrics if m.metric_name == name), None)


def _store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(tmp_path / "perf.db", clock=SequentialClock())


def test_runs_returned_oldest_to_newest(tmp_path: Path):
    store = _store(tmp_path)
    try:
        for commit in ("c1", "c2", "c3"):
            _seed_run(store, git_commit=commit)
        runs = store.history_runs(FLOW, DEVICE_A, "warm", 50)
    finally:
        store.close()

    assert [r.git_commit for r in runs] == ["c1", "c2", "c3"]
    started = [r.started_at for r in runs]
    assert started == sorted(started)  # chronological


def test_limit_keeps_most_recent_n_still_oldest_to_newest(tmp_path: Path):
    store = _store(tmp_path)
    try:
        for commit in ("c1", "c2", "c3", "c4", "c5"):
            _seed_run(store, git_commit=commit)
        runs = store.history_runs(FLOW, DEVICE_A, "warm", 2)
    finally:
        store.close()

    assert [r.git_commit for r in runs] == ["c4", "c5"]


def test_measure_family_summary_is_correct(tmp_path: Path):
    store = _store(tmp_path)
    try:
        _seed_run(store, git_commit="c1", checkout_values=(100.0, 200.0, 300.0))
        runs = store.history_runs(FLOW, DEVICE_A, "warm", 50)
    finally:
        store.close()

    checkout = _metric(runs[0], "checkout")
    assert checkout is not None
    assert checkout.unit == "ms"
    assert checkout.n == 3
    assert checkout.p50 == 200.0  # median of 3 distinct values
    assert checkout.p90 == 300.0  # ceil nearest-rank


def test_system_sample_family_summary_and_units_are_correct(tmp_path: Path):
    store = _store(tmp_path)
    try:
        _seed_run(
            store,
            git_commit="c1",
            fps_values=(50.0, 60.0, 70.0),
            ram_values=(100.0, 200.0, 300.0),
        )
        runs = store.history_runs(FLOW, DEVICE_A, "warm", 50)
    finally:
        store.close()

    fps = _metric(runs[0], "fps_avg")
    ram = _metric(runs[0], "ram_avg_mb")
    assert fps is not None and ram is not None
    # Units corrected away from the ingestion 'ms' default.
    assert fps.unit == "fps"
    assert ram.unit == "mb"
    assert fps.p50 == 60.0 and fps.p90 == 70.0 and fps.n == 3
    assert ram.p50 == 200.0 and ram.p90 == 300.0 and ram.n == 3


def test_no_warmup_discard_counts_every_iteration(tmp_path: Path):
    """Unlike `compare`'s baseline math, `history` is a RAW per-run summary —
    the sample count equals the full iteration count (no warm-up drop)."""
    store = _store(tmp_path)
    try:
        _seed_run(store, git_commit="c1", fps_values=(10.0, 20.0, 30.0, 40.0))
        runs = store.history_runs(FLOW, DEVICE_A, "warm", 50)
    finally:
        store.close()

    fps = _metric(runs[0], "fps_avg")
    assert fps is not None
    assert fps.n == 4


def test_mode_and_device_never_mix(tmp_path: Path):
    store = _store(tmp_path)
    try:
        _seed_run(store, git_commit="c1", mode="warm", device_key=DEVICE_A)
        _seed_run(store, git_commit="c2", mode="cold", device_key=DEVICE_A)
        _seed_run(store, git_commit="c3", mode="warm", device_key=DEVICE_B)
        warm_a = store.history_runs(FLOW, DEVICE_A, "warm", 50)
        cold_a = store.history_runs(FLOW, DEVICE_A, "cold", 50)
        warm_b = store.history_runs(FLOW, DEVICE_B, "warm", 50)
    finally:
        store.close()

    assert [r.git_commit for r in warm_a] == ["c1"]
    assert [r.git_commit for r in cold_a] == ["c2"]
    assert [r.git_commit for r in warm_b] == ["c3"]


def test_unseen_flow_device_mode_yields_empty(tmp_path: Path):
    store = _store(tmp_path)
    try:
        _seed_run(store, git_commit="c1")
        assert store.history_runs("nope", DEVICE_A, "warm", 50) == ()
        assert store.history_runs(FLOW, "Unknown|0|physical", "warm", 50) == ()
        assert store.history_runs(FLOW, DEVICE_A, "cold", 50) == ()
    finally:
        store.close()


def test_run_metadata_is_carried(tmp_path: Path):
    store = _store(tmp_path)
    try:
        run_id = _seed_run(store, git_commit="c1", source="ci")
        runs = store.history_runs(FLOW, DEVICE_A, "warm", 50)
    finally:
        store.close()

    assert len(runs) == 1
    assert runs[0].run_id == run_id
    assert runs[0].source == "ci"
    assert runs[0].git_commit == "c1"
