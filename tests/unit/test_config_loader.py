"""Layered config resolution (design §14). Closes the coverage gap on
`load_config` — precedence (CLI > env > project > global > defaults), TOML
reading, and flow-table building — previously untested because callers
constructed `PerfConfig` directly or monkeypatched `load_config`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from perf.config import loader
from perf.config.loader import DEFAULT_ITERATIONS, ConfigError, load_config


@pytest.fixture(autouse=True)
def _isolate_global_config(monkeypatch, tmp_path):
    # Never read the developer's real ~/.config/perf/config.toml during tests.
    monkeypatch.setattr(loader, "GLOBAL_CONFIG_PATH", tmp_path / "nonexistent-global.toml")


def _write(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


def test_defaults_when_nothing_configured(tmp_path):
    cfg = load_config(env={}, project_dir=tmp_path)
    assert cfg.db_path == "perfvibe.db"
    assert cfg.driver == "maestro"
    assert cfg.sampler == "flashlight"
    assert cfg.marker_source == "adb-logcat"
    assert cfg.default_iterations == DEFAULT_ITERATIONS
    assert cfg.no_color is False
    assert cfg.flows == {}


@pytest.mark.parametrize("raw", ["none", "None", "NONE", "  none  ", ""])
def test_sampler_none_or_empty_resolves_to_none(tmp_path, raw):
    """Task 4: `sampler = "none"`/`""` (case-insensitive, whitespace-tolerant)
    resolves to `None` — no sampler selected — enabling marker-only runs."""
    _write(tmp_path / "perfvibe.toml", f'sampler = "{raw}"\n')
    cfg = load_config(env={}, project_dir=tmp_path)
    assert cfg.sampler is None
    # The other source is untouched by the sampler normalization.
    assert cfg.marker_source == "adb-logcat"


@pytest.mark.parametrize("raw", ["none", "NONE", ""])
def test_marker_source_none_or_empty_resolves_to_none(tmp_path, raw):
    """Task 4: `marker_source = "none"`/`""` resolves to `None` — enabling
    sampler-only runs."""
    _write(tmp_path / "perfvibe.toml", f'marker_source = "{raw}"\n')
    cfg = load_config(env={}, project_dir=tmp_path)
    assert cfg.marker_source is None
    assert cfg.sampler == "flashlight"


def test_real_source_names_pass_through_unchanged(tmp_path):
    _write(
        tmp_path / "perfvibe.toml",
        'sampler = "flashlight"\nmarker_source = "adb-logcat"\n',
    )
    cfg = load_config(env={}, project_dir=tmp_path)
    assert cfg.sampler == "flashlight"
    assert cfg.marker_source == "adb-logcat"


def test_base_dir_anchors_db_and_results_but_not_flows(tmp_path):
    _write(
        tmp_path / "perfvibe.toml",
        """
        base_dir = "e2e"
        db_path = "localdata/perfvibe.db"
        results_dir = "results"

        [flows]
        checkout = "flows/checkout.yaml"
        """,
    )
    cfg = load_config(env={}, project_dir=tmp_path)
    base = tmp_path / "e2e"
    # base_dir resolves relative to the config file's own directory (tmp_path);
    # the OUTPUT paths (db, results) anchor UNDER it...
    assert cfg.base_dir == str(base)
    assert cfg.db_path == str(base / "localdata" / "perfvibe.db")
    assert cfg.results_dir == str(base / "results")
    # ...but a flow's maestro_path is an INPUT and is left exactly as written
    # (never re-anchored — it would double-resolve what init already recorded).
    assert cfg.flows["checkout"].maestro_path == "flows/checkout.yaml"


def test_base_dir_leaves_absolute_paths_untouched(tmp_path):
    abs_db = tmp_path / "elsewhere" / "x.db"
    _write(
        tmp_path / "perfvibe.toml",
        f"""
        base_dir = "e2e"
        db_path = "{abs_db}"
        """,
    )
    cfg = load_config(env={}, project_dir=tmp_path)
    assert cfg.db_path == str(abs_db)  # already absolute -> never re-anchored


def test_no_base_dir_leaves_every_path_relative_and_unchanged(tmp_path):
    _write(
        tmp_path / "perfvibe.toml",
        """
        [flows]
        checkout = "flows/checkout.yaml"
        """,
    )
    cfg = load_config(env={}, project_dir=tmp_path)
    assert cfg.base_dir is None
    assert cfg.db_path == "perfvibe.db"
    assert cfg.results_dir == "results"
    assert cfg.flows["checkout"].maestro_path == "flows/checkout.yaml"


def test_project_toml_is_applied(tmp_path):
    _write(
        tmp_path / "perfvibe.toml",
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
    _write(tmp_path / "perfvibe.toml", 'db_path = "project.db"\n')
    cfg = load_config(
        env={"PERF_DB": "/env/path.db", "NO_COLOR": "1", "MAESTRO_DEVICE": "emulator-5554"},
        project_dir=tmp_path,
    )
    assert cfg.db_path == "/env/path.db"
    assert cfg.no_color is True
    assert cfg.device == "emulator-5554"


def test_cli_overrides_env_and_project(tmp_path):
    _write(tmp_path / "perfvibe.toml", 'db_path = "project.db"\n')
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
    _write(tmp_path / "perfvibe.toml", 'driver = "maestro"\n')
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
    assert cfg.adaptive_floor is True  # anti-false-positive batch: on by default


def test_perf_toml_overrides_threshold_and_partial_floor(tmp_path):
    _write(
        tmp_path / "perfvibe.toml",
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


def test_adaptive_floor_can_be_disabled_via_toml(tmp_path):
    """The `adaptive_floor` knob (anti-false-positive batch, Task 2) is on by
    default but must be switchable off, restoring the pre-batch static-floor
    behavior for teams that prefer a fixed threshold."""
    _write(tmp_path / "perfvibe.toml", "adaptive_floor = false\n")
    cfg = load_config(env={}, project_dir=tmp_path)
    assert cfg.adaptive_floor is False


def test_adaptive_floor_true_when_explicitly_enabled(tmp_path):
    _write(tmp_path / "perfvibe.toml", "adaptive_floor = true\n")
    cfg = load_config(env={}, project_dir=tmp_path)
    assert cfg.adaptive_floor is True


def test_baseline_n_zero_or_negative_clamps_to_one(tmp_path):
    """FIX 3 (SUGGESTION->fix, PR-B review): `baseline_n` is loaded via
    bare `int()`; a config value of 0 (or negative) would reach the
    baseline query's `LIMIT ?`, where SQLite treats `LIMIT <= -1` as
    UNBOUNDED — silently loading the ENTIRE history and defeating the
    bounded-window guarantee (spec 'Bounded Compare Performance'). A
    non-positive `baseline_n` must clamp to a minimum of 1."""
    _write(tmp_path / "perfvibe.toml", "baseline_n = 0\n")
    cfg = load_config(env={}, project_dir=tmp_path)
    assert cfg.baseline_n == 1

    _write(tmp_path / "perfvibe.toml", "baseline_n = -5\n")
    cfg = load_config(env={}, project_dir=tmp_path)
    assert cfg.baseline_n == 1


# ===== config problems surface as ConfigError, never a raw traceback =====
# A broken config previously escaped `load_config` as TOMLDecodeError /
# ValueError and crashed the typer callback with exit 1 — poisoning the
# exit-code contract (`run` must NEVER emit 1) for every command.


def test_malformed_toml_raises_config_error_naming_the_file(tmp_path):
    _write(tmp_path / "perfvibe.toml", 'driver = "maestro\n')  # unterminated string
    with pytest.raises(ConfigError) as excinfo:
        load_config(env={}, project_dir=tmp_path)
    assert "perfvibe.toml" in str(excinfo.value)
    assert excinfo.value.cause  # carries the parser's line/col detail


def test_ill_typed_value_raises_config_error(tmp_path):
    _write(tmp_path / "perfvibe.toml", 'default_iterations = "ten"\n')
    with pytest.raises(ConfigError) as excinfo:
        load_config(env={}, project_dir=tmp_path)
    assert "default_iterations" in str(excinfo.value)


def test_ill_typed_floor_value_raises_config_error(tmp_path):
    _write(tmp_path / "perfvibe.toml", '[floors]\nfps = "fast"\n')
    with pytest.raises(ConfigError):
        load_config(env={}, project_dir=tmp_path)


def test_explicit_config_path_missing_raises_config_error(tmp_path):
    """An EXPLICITLY passed `--config` that does not exist must fail loudly —
    silently falling back to defaults turns a typo'd path into a baffling
    'unknown flow' error that never mentions the real problem. Only the
    DISCOVERED project/global files may be silently absent."""
    missing = tmp_path / "typo.toml"
    with pytest.raises(ConfigError) as excinfo:
        load_config(cli_config_path=str(missing), env={}, project_dir=tmp_path)
    assert "typo.toml" in str(excinfo.value)


def test_absent_discovered_configs_still_fall_back_silently(tmp_path):
    # The guard above must NOT change implicit discovery: no config anywhere
    # (isolated global, empty project dir) keeps resolving to pure defaults.
    cfg = load_config(env={}, project_dir=tmp_path)
    assert cfg.driver == "maestro"


def test_full_floors_override_replaces_all_units(tmp_path):
    _write(
        tmp_path / "perfvibe.toml",
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
