"""Device-free end-to-end proof for `perf run` — REAL `typer` app + REAL
adapter registry (`ReplayDriver`/`FlashlightSampler`/`AdbLogcatMarkerSource`)
+ REAL `SqliteStore` (a real temp SQLite file) + REAL parsers. NOTHING in
the pipeline is monkeypatched (SKILL rule: "Every code path must be
exercised through the REAL wiring at least once") — only the recorded
fixture files stand in for a device.

RED-before-GREEN: written before `driver = "replay"` was wired into the
registry/config/CLI.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from perf.cli.main import app
from perf.config.loader import load_config

runner = CliRunner()

_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "examples" / "demo-run"


def _write_config(tmp_path: Path, db_path: Path) -> Path:
    config_path = tmp_path / "perf.toml"
    config_path.write_text(
        "\n".join(
            [
                'driver = "replay"',
                'sampler = "flashlight"',
                'marker_source = "adb-logcat"',
                "default_iterations = 2",
                f'db_path = "{db_path.as_posix()}"',
                f'results_dir = "{(tmp_path / "results").as_posix()}"',
                f'replay_logcat = "{(_FIXTURES_DIR / "logcat.txt").as_posix()}"',
                f'replay_flashlight = "{(_FIXTURES_DIR / "flashlight.json").as_posix()}"',
                "",
                "[flows.demo]",
            ]
        )
    )
    return config_path


def test_replay_config_loads_driver_and_fixture_paths(tmp_path: Path):
    config_path = _write_config(tmp_path, tmp_path / "perf.db")
    config = load_config(cli_config_path=str(config_path))

    assert config.driver == "replay"
    assert config.replay_logcat == str((_FIXTURES_DIR / "logcat.txt").as_posix())
    assert config.replay_flashlight == str((_FIXTURES_DIR / "flashlight.json").as_posix())


def test_perf_run_replay_end_to_end_json_exits_0_and_persists_a_run(tmp_path: Path):
    db_path = tmp_path / "perf.db"
    config_path = _write_config(tmp_path, db_path)

    result = runner.invoke(app, ["--json", "--config", str(config_path), "run", "demo"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["flow"] == "demo"
    assert payload["n"] == 2

    # At least one metric with real replayed values (marker "checkout" from
    # the recorded logcat fixture).
    assert "checkout" in payload["measures"]
    assert payload["measures"]["checkout"]["values"] == [812.0, 790.0]
    assert "ttfp" in payload["measures"]

    # Flashlight samples replayed verbatim (2 iterations, per fixture).
    assert len(payload["flashlight"]) == 2
    assert payload["flashlight"][0]["fps_avg"] is not None

    # A real run row was persisted in the real SqliteStore.
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT COUNT(*) FROM run").fetchone()
        assert rows[0] == 1
    finally:
        conn.close()


def test_perf_run_replay_end_to_end_pretty_exits_0(tmp_path: Path):
    db_path = tmp_path / "perf.db"
    config_path = _write_config(tmp_path, db_path)

    result = runner.invoke(app, ["--config", str(config_path), "run", "demo"])

    assert result.exit_code == 0, result.output
    assert "perf run complete" in result.stdout
