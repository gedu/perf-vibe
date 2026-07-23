"""Layered config resolution (design §14). Closes the coverage gap on
`load_config` — precedence (CLI > env > project > global > defaults), TOML
reading, and flow-table building — previously untested because callers
constructed `PerfConfig` directly or monkeypatched `load_config`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from perf.config import loader
from perf.config.loader import DEFAULT_ITERATIONS, load_config


@pytest.fixture(autouse=True)
def _isolate_global_config(monkeypatch, tmp_path):
    # Never read the developer's real ~/.config/perf/config.toml during tests.
    monkeypatch.setattr(loader, "GLOBAL_CONFIG_PATH", tmp_path / "nonexistent-global.toml")


def _write(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


def test_defaults_when_nothing_configured(tmp_path):
    cfg = load_config(env={}, project_dir=tmp_path)
    assert cfg.db_path == "perf.db"
    assert cfg.driver == "maestro"
    assert cfg.sampler == "flashlight"
    assert cfg.marker_source == "adb-logcat"
    assert cfg.default_iterations == DEFAULT_ITERATIONS
    assert cfg.no_color is False
    assert cfg.flows == {}


def test_project_toml_is_applied(tmp_path):
    _write(
        tmp_path / "perf.toml",
        """
        driver = "manual"
        sampler = "flashlight"
        default_iterations = 5
        bundle_id = "com.example.app"

        [flows.checkout]
        maestro_path = "flows/checkout.yaml"
        prompt = "Do the checkout"

        [flows]
        login = "flows/login.yaml"
        """,
    )
    cfg = load_config(env={}, project_dir=tmp_path)
    assert cfg.driver == "manual"
    assert cfg.default_iterations == 5
    assert cfg.bundle_id == "com.example.app"
    assert cfg.flows["checkout"].maestro_path == "flows/checkout.yaml"
    assert cfg.flows["checkout"].prompt == "Do the checkout"
    # shorthand `name = "path"` form
    assert cfg.flows["login"].maestro_path == "flows/login.yaml"


def test_env_overrides_project(tmp_path):
    _write(tmp_path / "perf.toml", 'db_path = "project.db"\n')
    cfg = load_config(
        env={"PERF_DB": "/env/path.db", "NO_COLOR": "1", "MAESTRO_DEVICE": "emulator-5554"},
        project_dir=tmp_path,
    )
    assert cfg.db_path == "/env/path.db"
    assert cfg.no_color is True
    assert cfg.device == "emulator-5554"


def test_cli_overrides_env_and_project(tmp_path):
    _write(tmp_path / "perf.toml", 'db_path = "project.db"\n')
    cfg = load_config(
        cli_db="/cli/path.db",
        cli_no_color=True,
        cli_device="cli-device",
        env={"PERF_DB": "/env/path.db", "MAESTRO_DEVICE": "env-device"},
        project_dir=tmp_path,
    )
    assert cfg.db_path == "/cli/path.db"
    assert cfg.no_color is True
    assert cfg.device == "cli-device"


def test_explicit_config_path_wins_over_directory_scan(tmp_path):
    _write(tmp_path / "perf.toml", 'driver = "maestro"\n')
    explicit = _write(tmp_path / "custom.toml", 'driver = "manual"\n')
    cfg = load_config(cli_config_path=str(explicit), env={}, project_dir=tmp_path)
    assert cfg.driver == "manual"


def test_missing_toml_files_fall_back_to_defaults(tmp_path):
    # No perf.toml in project_dir, isolated (nonexistent) global → pure defaults.
    cfg = load_config(env={}, project_dir=tmp_path)
    assert cfg.driver == "maestro"
    assert cfg.flows == {}


# ===== compare tuning defaults (design Rev 2 §"Tuning defaults", decision #58) =====


def test_compare_tuning_defaults_when_nothing_configured(tmp_path):
    cfg = load_config(env={}, project_dir=tmp_path)
    assert cfg.threshold_pct == 5.0
    assert cfg.floors == {"ms": 5.0, "mb": 5.0, "pct": 3.0, "fps": 2.0}
    assert cfg.min_baseline_commits == 3
    assert cfg.warmup_k == 1
    assert cfg.baseline_n == 10


def test_perf_toml_overrides_threshold_and_partial_floor(tmp_path):
    _write(
        tmp_path / "perf.toml",
        """
        threshold_pct = 8.0
        min_baseline_commits = 5
        warmup_k = 2
        baseline_n = 20

        [floors]
        fps = 1.5
        """,
    )
    cfg = load_config(env={}, project_dir=tmp_path)
    assert cfg.threshold_pct == 8.0
    assert cfg.min_baseline_commits == 5
    assert cfg.warmup_k == 2
    assert cfg.baseline_n == 20
    # Partial floor override keeps the OTHER unit defaults intact — a
    # single-unit override must never drop the rest of the floor map.
    assert cfg.floors == {"ms": 5.0, "mb": 5.0, "pct": 3.0, "fps": 1.5}


def test_baseline_n_zero_or_negative_clamps_to_one(tmp_path):
    """FIX 3 (SUGGESTION->fix, PR-B review): `baseline_n` is loaded via
    bare `int()`; a config value of 0 (or negative) would reach the
    baseline query's `LIMIT ?`, where SQLite treats `LIMIT <= -1` as
    UNBOUNDED — silently loading the ENTIRE history and defeating the
    bounded-window guarantee (spec 'Bounded Compare Performance'). A
    non-positive `baseline_n` must clamp to a minimum of 1."""
    _write(tmp_path / "perf.toml", "baseline_n = 0\n")
    cfg = load_config(env={}, project_dir=tmp_path)
    assert cfg.baseline_n == 1

    _write(tmp_path / "perf.toml", "baseline_n = -5\n")
    cfg = load_config(env={}, project_dir=tmp_path)
    assert cfg.baseline_n == 1


def test_full_floors_override_replaces_all_units(tmp_path):
    _write(
        tmp_path / "perf.toml",
        """
        [floors]
        ms = 10.0
        mb = 10.0
        pct = 5.0
        fps = 3.0
        """,
    )
    cfg = load_config(env={}, project_dir=tmp_path)
    assert cfg.floors == {"ms": 10.0, "mb": 10.0, "pct": 5.0, "fps": 3.0}
