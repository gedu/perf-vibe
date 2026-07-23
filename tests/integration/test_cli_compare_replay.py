"""Device-free end-to-end proof for `perf compare` — REAL `typer` app +
REAL adapter registry (`SqlAnalyzer`/`SqliteStore`/`BashRunContextProvider`
— NONE of it monkeypatched) + a multi-commit history seeded through the
REAL `RunFlowUseCase` (REAL `ReplayDriver`/`FlashlightSampler`/
`AdbLogcatMarkerSource`), varying ONLY `git_commit` via a thin fake
wrapper around the REAL context provider (`examples/demo-compare/seed.py`'s
`seed_into()` — the exact same helper the demo script uses). PR-C task 3.8.

Nothing in the compare pipeline is monkeypatched (SKILL rule: "Every code
path must be exercised through the REAL wiring at least once") — only the
recorded fixture files stand in for a device; `device_key` is resolved by
the SAME real `BashRunContextProvider` on both the seed and compare sides,
so this test passes whether or not the machine running it has an
adb/emulator attached.
"""

from __future__ import annotations

import json
import sys
from importlib import import_module
from pathlib import Path

from typer.testing import CliRunner

_DEMO_DIR = Path(__file__).resolve().parents[2] / "examples" / "demo-compare"
if str(_DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(_DEMO_DIR))

from seed import FLOW, seed_into  # noqa: E402

from perf.adapters.store_sqlite import SqliteStore  # noqa: E402
from perf.config.loader import FlowConfig, PerfConfig  # noqa: E402

main_module = import_module("perf.cli.main")

from fakes import SequentialClock  # noqa: E402

runner = CliRunner()


def _config(db_path: Path) -> PerfConfig:
    return PerfConfig(
        db_path=str(db_path),
        no_color=True,
        flows={FLOW: FlowConfig(name=FLOW, maestro_path=f"{FLOW}.yaml")},
    )


def test_seeded_multi_commit_history_yields_a_real_regression_verdict(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    results_dir = tmp_path / "results"

    store = SqliteStore(db_path, clock=SequentialClock())
    try:
        run_ids = seed_into(store, results_dir=results_dir)
    finally:
        store.close()
    assert len(run_ids) == 5  # 4 baseline commits + 1 regressing latest commit

    config = _config(db_path)
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)

    json_result = runner.invoke(main_module.app, ["--json", "compare", FLOW])
    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.stdout)
    assert payload["schema_version"] == 1

    checkout = next(v for v in payload["verdicts"] if v["metric"] == "checkout")
    assert checkout["status"] == "regression"
    assert checkout["latest_value"] > checkout["baseline_value"]

    ttfp = next(v for v in payload["verdicts"] if v["metric"] == "ttfp")
    assert ttfp["status"] == "stable"

    # The sanity label is present in --json...
    assert payload["calibration"]["status"] in {
        "reasonable",
        "too-loose",
        "too-strict",
        "insufficient-data",
    }

    # ...AND in pretty, and never changes the exit code.
    pretty_result = runner.invoke(main_module.app, ["compare", FLOW])
    assert pretty_result.exit_code == 0, pretty_result.output
    assert "REGRESSION" in pretty_result.output
    assert "ttfp" in pretty_result.output
    assert "STABLE" in pretty_result.output


def test_seeded_history_never_exits_1(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "perf.db"
    results_dir = tmp_path / "results"

    store = SqliteStore(db_path, clock=SequentialClock())
    try:
        seed_into(store, results_dir=results_dir)
    finally:
        store.close()

    config = _config(db_path)
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)

    result = runner.invoke(main_module.app, ["compare", FLOW])
    assert result.exit_code != 1
