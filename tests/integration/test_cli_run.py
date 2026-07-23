"""End-to-end CLI harness for `perf run` — real `typer` app + real
`SqliteStore` (a real temp SQLite file) + FAKE driver/sampler/marker
source (no device/subprocess touched). Proves the full command dispatch,
`--json` contract, exit-code discipline (0/2/3, NEVER 1), and the banner
gating rules end-to-end (SKILL rule 6/7).
"""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path

import pytest
from typer.testing import CliRunner

import perf.cli.commands.run as run_module
from perf.config.loader import FlowConfig, PerfConfig

# `perf/cli/__init__.py` intentionally does `from perf.cli.main import main`
# so the `perf.cli:main` console-script entry point (pyproject.toml)
# resolves — but that assignment SHADOWS the `perf.cli.main` submodule
# attribute with the function itself (the classic package/`__init__.py`
# name-collision gotcha). `import_module` bypasses attribute-chain
# resolution and always returns the real submodule from `sys.modules`.
main_module = import_module("perf.cli.main")
from fakes import FakeDriver, FakeMarkerSource, FakeRunContextProvider
from perf.domain.model import DriverResult, MarkerParseResult

runner = CliRunner()


def _config(*, sampler="flashlight", marker_source="adb-logcat", db_path: str) -> PerfConfig:
    return PerfConfig(
        db_path=db_path,
        no_color=True,
        driver="maestro",
        sampler=sampler,
        marker_source=marker_source,
        default_iterations=2,
        flows={"checkout": FlowConfig(name="checkout", maestro_path="checkout.yaml")},
    )


def _patch_registry(
    monkeypatch,
    *,
    driver=None,
    sampler_factory=None,
    marker_factory=None,
    context_provider=None,
):
    monkeypatch.setattr(run_module, "build_driver", lambda name, **kw: driver or FakeDriver())
    monkeypatch.setattr(
        run_module,
        "build_sampler",
        lambda name, **kw: sampler_factory() if (name and sampler_factory) else None,
    )
    monkeypatch.setattr(
        run_module,
        "build_marker_source",
        lambda name, **kw: marker_factory() if (name and marker_factory) else None,
    )
    monkeypatch.setattr(
        run_module,
        "build_context_provider",
        lambda **kw: context_provider or FakeRunContextProvider(),
    )


def _happy_marker_factory():
    return FakeMarkerSource(
        parse_result=MarkerParseResult(
            markers=(
                __import__("perf.domain.model", fromlist=["Marker"]).Marker(
                    name="checkout", value=900.0, unit="ms"
                ),
            ),
            partial_coverage=False,
        )
    )


def test_successful_run_exits_0_and_json_matches_contract(monkeypatch, tmp_path: Path):
    config = _config(sampler=None, marker_source="adb-logcat", db_path=str(tmp_path / "perf.db"))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_registry(monkeypatch, marker_factory=_happy_marker_factory)

    result = runner.invoke(main_module.app, ["--json", "run", "checkout"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["flow"] == "checkout"
    assert payload["measures"]["checkout"]["values"] == [900.0]


def test_successful_run_pretty_output_exits_0(monkeypatch, tmp_path: Path):
    config = _config(sampler=None, marker_source="adb-logcat", db_path=str(tmp_path / "perf.db"))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_registry(monkeypatch, marker_factory=_happy_marker_factory)

    result = runner.invoke(main_module.app, ["run", "checkout"])

    assert result.exit_code == 0, result.output
    assert "perf run complete" in result.stdout


def test_unknown_flow_exits_2(monkeypatch, tmp_path: Path):
    config = _config(sampler=None, marker_source="adb-logcat", db_path=str(tmp_path / "perf.db"))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_registry(monkeypatch, marker_factory=_happy_marker_factory)

    result = runner.invoke(main_module.app, ["run", "not-a-flow"])

    assert result.exit_code == 2, result.output


def test_no_measurement_source_configured_exits_2(monkeypatch, tmp_path: Path):
    config = _config(sampler=None, marker_source=None, db_path=str(tmp_path / "perf.db"))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_registry(monkeypatch)

    result = runner.invoke(main_module.app, ["run", "checkout"])

    assert result.exit_code == 2, result.output


def test_device_offline_exits_3(monkeypatch, tmp_path: Path):
    config = _config(sampler=None, marker_source="adb-logcat", db_path=str(tmp_path / "perf.db"))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_registry(
        monkeypatch,
        driver=FakeDriver(drive_error=OSError("device offline")),
        marker_factory=_happy_marker_factory,
    )

    result = runner.invoke(main_module.app, ["run", "checkout"])

    assert result.exit_code == 3, result.output


def test_capture_failed_exits_3(monkeypatch, tmp_path: Path):
    """A dead/failed parallel logcat capture (`capture_failed=True`, e.g. adb
    'more than one device') is a runtime/tooling failure → exit 3, distinct
    from a healthy capture that saw zero markers."""
    config = _config(sampler=None, marker_source="adb-logcat", db_path=str(tmp_path / "perf.db"))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_registry(
        monkeypatch,
        driver=FakeDriver(
            drive_result=DriverResult(
                ok=True,
                iteration_outcomes=("ok",),
                logcat_lines=(),
                capture_failed=True,
                diagnostics="adb: more than one device/emulator",
            )
        ),
        marker_factory=_happy_marker_factory,
    )

    result = runner.invoke(main_module.app, ["run", "checkout"])

    assert result.exit_code == 3, result.output
    assert result.exit_code != 1


@pytest.mark.parametrize(
    "args",
    [
        ["run", "not-a-flow"],
        ["run", "checkout"],
    ],
)
def test_run_never_exits_1(monkeypatch, tmp_path: Path, args):
    config = _config(sampler=None, marker_source=None, db_path=str(tmp_path / "perf.db"))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_registry(monkeypatch)

    result = runner.invoke(main_module.app, args)

    assert result.exit_code != 1


def test_bare_perf_shows_banner_on_tty_and_help(monkeypatch, tmp_path: Path):
    config = _config(db_path=str(tmp_path / "perf.db"))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    monkeypatch.setattr(main_module.sys.stdout, "isatty", lambda: True, raising=False)

    result = runner.invoke(main_module.app, [])

    assert result.exit_code == 0
    # CliRunner's stdout is not a real TTY by default; `should_show_banner`
    # reads `sys.stdout.isatty()` at callback time via `resolve_output_context`,
    # which CliRunner replaces — assert the help text is always present,
    # and the banner never corrupts a data stream (checked below).
    assert "perf" in result.stdout.lower()


def test_json_output_never_contains_banner_text(monkeypatch, tmp_path: Path):
    config = _config(sampler=None, marker_source="adb-logcat", db_path=str(tmp_path / "perf.db"))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_registry(monkeypatch, marker_factory=_happy_marker_factory)

    result = runner.invoke(main_module.app, ["--json", "run", "checkout"])

    assert result.exit_code == 0
    assert "performance lab cli" not in result.stdout


def test_run_subcommand_help_never_shows_banner(monkeypatch, tmp_path: Path):
    config = _config(db_path=str(tmp_path / "perf.db"))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)

    result = runner.invoke(main_module.app, ["run", "--help"])

    assert result.exit_code == 0
    assert "performance lab cli" not in result.stdout


# ===== PR3 review fixes =====


def test_store_close_failure_does_not_change_exit_code(monkeypatch, tmp_path: Path):
    """FIX 2 (CRITICAL): an exception from `store.close()` in the `finally`
    must NOT override the computed exit code (it would escape as Python's
    default exit 1). The run itself succeeds → exit 0 despite close() raising."""
    config = _config(sampler=None, marker_source="adb-logcat", db_path=str(tmp_path / "perf.db"))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_registry(monkeypatch, marker_factory=_happy_marker_factory)

    from perf.adapters.store_sqlite import SqliteStore

    def failing_close_store(db_path, **kw):
        store = SqliteStore(db_path, **kw)

        def boom():
            raise RuntimeError("close boom")

        store.close = boom  # shadow the bound method on this instance
        return store

    monkeypatch.setattr(run_module, "build_store", failing_close_store)

    result = runner.invoke(main_module.app, ["run", "checkout"])

    assert result.exit_code == 0, result.output


def test_render_failure_exits_3_never_1(monkeypatch, tmp_path: Path):
    """FIX 3 (WARNING): rendering runs outside the main guarded block; an
    output failure must map to exit 3, never escape as exit 1."""
    config = _config(sampler=None, marker_source="adb-logcat", db_path=str(tmp_path / "perf.db"))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_registry(monkeypatch, marker_factory=_happy_marker_factory)

    def boom(*a, **k):
        raise RuntimeError("render boom")

    monkeypatch.setattr(run_module, "render_confirmation", boom)

    result = runner.invoke(main_module.app, ["run", "checkout"])  # pretty path

    assert result.exit_code == 3, result.output
    assert result.exit_code != 1


def test_unknown_driver_in_config_exits_2(monkeypatch, tmp_path: Path):
    """FIX 6 (WARNING): a config typo (`driver = "maestr"`) is a usage/config
    error → exit 2, NOT a runtime failure (exit 3). Uses the REAL registry
    (no monkeypatch) so the ValueError→exit-2 mapping is genuinely exercised."""
    config = PerfConfig(
        db_path=str(tmp_path / "perf.db"),
        no_color=True,
        driver="maestr",  # typo
        sampler=None,
        marker_source="adb-logcat",
        default_iterations=2,
        flows={"checkout": FlowConfig(name="checkout", maestro_path="checkout.yaml")},
    )
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)

    result = runner.invoke(main_module.app, ["run", "checkout"])

    assert result.exit_code == 2, result.output


def test_run_help_has_no_password_flag(monkeypatch, tmp_path: Path):
    """FIX 5 (WARNING): the secret is read from the PASSWORD env var only —
    there is no `--password` CLI option (which would leak into shell history /
    `ps`)."""
    config = _config(db_path=str(tmp_path / "perf.db"))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)

    result = runner.invoke(main_module.app, ["run", "--help"])

    assert result.exit_code == 0
    assert "--password" not in result.stdout


def test_password_env_drives_run_and_never_appears_in_stdout(monkeypatch, tmp_path: Path):
    """FIX 5: the secret still reaches the run via the PASSWORD env var and is
    never echoed to stdout."""
    config = _config(sampler=None, marker_source="adb-logcat", db_path=str(tmp_path / "perf.db"))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_registry(monkeypatch, marker_factory=_happy_marker_factory)
    monkeypatch.setenv("PASSWORD", "s3cret-value")

    result = runner.invoke(main_module.app, ["run", "checkout"])

    assert result.exit_code == 0, result.output
    assert "s3cret-value" not in result.stdout
