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
