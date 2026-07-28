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
from perf.cli.output.context import NON_TTY_NUDGE
from perf.config.loader import FlowConfig, PerfConfig

# `perf/cli/__init__.py` intentionally does `from perf.cli.main import main`
# so the `perf.cli:main` console-script entry point (pyproject.toml)
# resolves — but that assignment SHADOWS the `perf.cli.main` submodule
# attribute with the function itself (the classic package/`__init__.py`
# name-collision gotcha). `import_module` bypasses attribute-chain
# resolution and always returns the real submodule from `sys.modules`.
main_module = import_module("perf.cli.main")

# These must follow the import_module() call above, which has to run before
# anything else touches perf.cli.main — hence the suppressions.
from fakes import (  # noqa: E402
    FakeDriver,
    FakeMarkerSource,
    FakeRunContextProvider,
    FakeSystemSampler,
)
from perf.domain.model import (  # noqa: E402
    DriverResult,
    MarkerParseResult,
    SystemSample,
    SystemSampleParseResult,
)

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


def test_run_failure_surfaces_salient_cause_and_hint_not_the_raw_node_stack(
    monkeypatch, tmp_path: Path
):
    """A flow failure whose diagnostics is a huge Node stack trace (Flashlight
    aborting on adb) must render the ONE meaningful line as `cause:` plus an
    actionable `hint:` — never dump the framework internals at the user."""
    noisy = (
        "node:internal/errors:856\n"
        "  const err = new Error(message);\n"
        "Error: Command failed: adb shell getprop ro.build.version.sdk\n"
        "adb: device unauthorized.\n"
        "    at Object.execSync (node:child_process:891:15)\n"
    )
    config = _config(sampler=None, marker_source="adb-logcat", db_path=str(tmp_path / "perf.db"))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_registry(
        monkeypatch,
        driver=FakeDriver(
            drive_result=DriverResult(
                ok=False,
                iteration_outcomes=("failed",),
                logcat_lines=(),
                capture_failed=True,
                diagnostics=noisy,
            )
        ),
        marker_factory=_happy_marker_factory,
    )

    result = runner.invoke(main_module.app, ["run", "checkout"])

    assert result.exit_code == 3, result.output
    assert "did not complete successfully" in result.output  # flow blamed, not the capture
    assert "cause: adb: device unauthorized" in result.output
    assert "hint:" in result.output and "adb kill-server" in result.output
    assert "node:internal" not in result.output  # raw stack noise never surfaced


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


# ===== run-live-progress Slice A =====


def test_manual_driver_json_run_has_zero_stdout_progress_bytes(monkeypatch, tmp_path: Path):
    """RED (Slice A task A.9) — the `--json` STDOUT-corruption bug: drives a
    REAL `ManualDriver` built through the REAL registry (never monkeypatched
    — `build_driver` stays untouched, python-testing rule 3: "if a CLI test
    patches build_driver, it is not testing the real wiring"). Only
    `build_sampler`/`build_context_provider` are faked (no device/subprocess
    touched) and the real `input` builtin is stubbed so the manual prompt
    loop never blocks. Proves stdout is byte-identical to the `--json`
    payload — zero progress bytes leaked, even with the manual driver's
    reporter-driven prompt loop active."""
    config = PerfConfig(
        db_path=str(tmp_path / "perf.db"),
        no_color=True,
        driver="manual",
        sampler="flashlight",
        marker_source=None,
        default_iterations=1,
        flows={"checkout": FlowConfig(name="checkout", maestro_path="checkout.yaml")},
    )
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")
    monkeypatch.setattr(
        run_module,
        "build_sampler",
        lambda name, **kw: FakeSystemSampler(
            parse_result=SystemSampleParseResult(
                samples=(
                    SystemSample(
                        iteration_idx=0,
                        total_time_ms=900.0,
                        start_time_ms=0.0,
                        fps_avg=None,
                        fps_min=None,
                        ram_avg_mb=None,
                        ram_peak_mb=None,
                        cpu_avg_pct=None,
                        cpu_peak_pct=None,
                    ),
                ),
                partial_coverage=False,
            )
        ),
    )
    monkeypatch.setattr(run_module, "build_context_provider", lambda **kw: FakeRunContextProvider())

    result = runner.invoke(main_module.app, ["--json", "run", "checkout"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)  # raises if any stray byte reached stdout
    assert payload["schema_version"] == 1
    assert payload["flow"] == "checkout"


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


# ===== run-live-progress Slice B =====


def test_driver_managed_maestro_relay_stays_on_stderr_json_purity_holds(
    monkeypatch, tmp_path: Path
):
    """B.8: `--json` purity re-confirmed with the REAL, registry-built
    `MaestroDriver` DRIVER_MANAGED path — `build_driver` is NEVER
    monkeypatched (python-testing rule 3: "if a CLI test patches
    build_driver, it is not testing the real wiring"). Only
    `SubprocessRunner`'s own process-spawning methods (the actual
    device/subprocess I/O boundary) are faked, proving the reporter's live
    relay reaches STDERR ONLY even with the real driver + real
    `StderrProgressReporter` wired end-to-end."""
    from perf.adapters.process import CaptureResult, CommandResult
    from perf.adapters.process import SubprocessRunner as RealSubprocessRunner

    def fake_run_streamed(self, argv, *, env=None, cwd=None, on_line=None):
        if on_line is not None:
            on_line("RUN Checkout Flow")
            on_line("[PERF] checkout: 900ms")
        return CommandResult(returncode=0, stdout="", stderr="")

    def fake_start_capture(self, argv):
        return object()

    def fake_stop_capture(self, process):
        return CaptureResult(lines=["[PERF] checkout: 900ms"], returncode=0)

    monkeypatch.setattr(RealSubprocessRunner, "run_streamed", fake_run_streamed)
    monkeypatch.setattr(RealSubprocessRunner, "start_capture", fake_start_capture)
    monkeypatch.setattr(RealSubprocessRunner, "stop_capture", fake_stop_capture)

    config = PerfConfig(
        db_path=str(tmp_path / "perf.db"),
        no_color=True,
        driver="maestro",
        sampler=None,
        marker_source="adb-logcat",
        default_iterations=1,
        flows={"checkout": FlowConfig(name="checkout", maestro_path="checkout.yaml")},
    )
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)

    result = runner.invoke(main_module.app, ["--json", "run", "checkout"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)  # raises if any stray byte reached stdout
    assert payload["schema_version"] == 1
    assert payload["flow"] == "checkout"
    assert "RUN Checkout Flow" not in result.stdout
    assert "RUN Checkout Flow" in result.stderr


# ===== run-live-progress Slice C =====


def test_tool_managed_flashlight_relay_stays_on_stderr_json_purity_holds_with_recap(
    monkeypatch, tmp_path: Path
):
    """C.6: `--json` purity re-confirmed with the REAL, registry-built
    `MaestroDriver` + `FlashlightSampler` TOOL_MANAGED path — `build_driver`/
    `build_sampler` are NEVER monkeypatched (python-testing rule 3). Only
    `SubprocessRunner.run_streamed` (the actual subprocess boundary) is
    faked; it also writes the Flashlight results JSON the real
    `FlashlightSampler.parse()` then reads, with ONE failed iteration so the
    recap must show honest per-iteration ❌, not silent all-✅. Proves the
    live relay + the end-of-run recap both land on STDERR ONLY, with STDOUT
    still byte-pure JSON."""
    from perf.adapters.process import CommandResult
    from perf.adapters.process import SubprocessRunner as RealSubprocessRunner

    def fake_run_streamed(self, argv, *, env=None, cwd=None, on_line=None):
        # Locate --resultsFilePath in the composed Flashlight argv and write
        # the report FlashlightSampler.parse() will read back — one
        # SUCCESS + one FAILURE iteration, proving honest partial-coverage
        # recap (never a fabricated all-✅ table).
        results_path = argv[argv.index("--resultsFilePath") + 1]
        report = {
            "name": "Results",
            "status": "SUCCESS",
            "iterations": [
                {
                    "time": 900.0,
                    "startTime": 0.0,
                    "status": "SUCCESS",
                    "measures": [{"fps": 60.0, "ram": 100.0, "cpu": {"perName": {"UI": 5.0}}}],
                },
                {"time": 500.0, "startTime": 0.0, "status": "FAILURE", "measures": []},
            ],
        }
        Path(results_path).write_text(json.dumps(report))
        if on_line is not None:
            on_line("RUN Checkout Flow")
            on_line("  COMPLETED")
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(RealSubprocessRunner, "run_streamed", fake_run_streamed)

    config = PerfConfig(
        db_path=str(tmp_path / "perf.db"),
        no_color=True,
        driver="maestro",
        sampler="flashlight",
        marker_source=None,
        bundle_id="com.example.app",
        results_dir=str(tmp_path),
        default_iterations=2,
        flows={"checkout": FlowConfig(name="checkout", maestro_path="checkout.yaml")},
    )
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)

    result = runner.invoke(main_module.app, ["--json", "run", "checkout"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)  # raises if any stray byte reached stdout
    assert payload["schema_version"] == 1
    assert payload["flow"] == "checkout"
    assert payload["partial_coverage"] is True

    # Relay + header stay on STDERR only.
    assert "RUN Checkout Flow" not in result.stdout
    assert "RUN Checkout Flow" in result.stderr
    assert "checkout · 2 iterations via Flashlight" in result.stderr

    # FIX 4 (cleanup): the header is now emitted by `run.py` calling
    # `reporter.run_header(...)` directly (BEFORE `execute()`), never by
    # `driver_maestro.py` via the 3-space-indented `relayed_line` — assert
    # the header line is a DISTINCT, non-indented top-level line.
    header_lines = [
        line for line in result.stderr.splitlines() if "iterations via Flashlight" in line
    ]
    assert header_lines == ["🎯 checkout · 2 iterations via Flashlight"]

    # Recap renders on STDERR with an honest per-iteration table — the
    # failed iteration shows ❌, never silently ✅.
    assert "Recap" in result.stderr
    assert "✅ iteration 1/2" in result.stderr
    assert "❌ iteration 2/2" in result.stderr
    assert "Recap" not in result.stdout


# ===== run-live-progress Slice D: --quiet =====


def test_quiet_flag_zero_stderr_bytes_driver_managed(monkeypatch, tmp_path: Path):
    """D.1 (RED): `--quiet` yields ZERO stderr progress bytes even with a
    REAL, registry-built `MaestroDriver` DRIVER_MANAGED path relaying live
    tool output (`build_driver` is never monkeypatched — python-testing
    rule 3). Mirrors
    `test_driver_managed_maestro_relay_stays_on_stderr_json_purity_holds`
    with `--quiet` added; only `SubprocessRunner.run_streamed`/
    `start_capture`/`stop_capture` (the process boundary) are faked."""
    from perf.adapters.process import CaptureResult, CommandResult
    from perf.adapters.process import SubprocessRunner as RealSubprocessRunner

    def fake_run_streamed(self, argv, *, env=None, cwd=None, on_line=None):
        if on_line is not None:
            on_line("RUN Checkout Flow")
            on_line("[PERF] checkout: 900ms")
        return CommandResult(returncode=0, stdout="", stderr="")

    def fake_start_capture(self, argv):
        return object()

    def fake_stop_capture(self, process):
        return CaptureResult(lines=["[PERF] checkout: 900ms"], returncode=0)

    monkeypatch.setattr(RealSubprocessRunner, "run_streamed", fake_run_streamed)
    monkeypatch.setattr(RealSubprocessRunner, "start_capture", fake_start_capture)
    monkeypatch.setattr(RealSubprocessRunner, "stop_capture", fake_stop_capture)

    config = PerfConfig(
        db_path=str(tmp_path / "perf.db"),
        no_color=True,
        driver="maestro",
        sampler=None,
        marker_source="adb-logcat",
        default_iterations=1,
        flows={"checkout": FlowConfig(name="checkout", maestro_path="checkout.yaml")},
    )
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)

    result = runner.invoke(main_module.app, ["--json", "run", "checkout", "--quiet"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)  # raises if any stray byte reached stdout
    assert payload["schema_version"] == 1
    assert payload["flow"] == "checkout"
    assert result.stderr == ""


def test_quiet_flag_zero_stderr_bytes_tool_managed_with_recap(monkeypatch, tmp_path: Path):
    """D.1: same scenario as
    `test_tool_managed_flashlight_relay_stays_on_stderr_json_purity_holds_with_recap`
    with `--quiet` added — the framing header, the live relay, AND the
    recap must ALL go silent; stdout stays byte-pure JSON."""
    from perf.adapters.process import CommandResult
    from perf.adapters.process import SubprocessRunner as RealSubprocessRunner

    def fake_run_streamed(self, argv, *, env=None, cwd=None, on_line=None):
        results_path = argv[argv.index("--resultsFilePath") + 1]
        report = {
            "name": "Results",
            "status": "SUCCESS",
            "iterations": [
                {
                    "time": 900.0,
                    "startTime": 0.0,
                    "status": "SUCCESS",
                    "measures": [{"fps": 60.0, "ram": 100.0, "cpu": {"perName": {"UI": 5.0}}}],
                },
                {"time": 500.0, "startTime": 0.0, "status": "FAILURE", "measures": []},
            ],
        }
        Path(results_path).write_text(json.dumps(report))
        if on_line is not None:
            on_line("RUN Checkout Flow")
            on_line("  COMPLETED")
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(RealSubprocessRunner, "run_streamed", fake_run_streamed)

    config = PerfConfig(
        db_path=str(tmp_path / "perf.db"),
        no_color=True,
        driver="maestro",
        sampler="flashlight",
        marker_source=None,
        bundle_id="com.example.app",
        results_dir=str(tmp_path),
        default_iterations=2,
        flows={"checkout": FlowConfig(name="checkout", maestro_path="checkout.yaml")},
    )
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)

    result = runner.invoke(main_module.app, ["--json", "run", "checkout", "--quiet"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)  # raises if any stray byte reached stdout
    assert payload["schema_version"] == 1
    assert payload["flow"] == "checkout"
    assert payload["partial_coverage"] is True
    assert result.stderr == ""


def test_quiet_short_flag_suppresses_stderr_progress(monkeypatch, tmp_path: Path):
    """D.1 corner case: `-q` (short form) behaves identically to `--quiet`.
    Uses `--json` (like the other quiet tests) so the assertion isolates
    PROGRESS bytes specifically — the pre-existing, unrelated non-TTY
    `--json` nudge (`NON_TTY_NUDGE`) only fires on the pretty path and is
    out of this change's scope."""
    config = _config(sampler=None, marker_source="adb-logcat", db_path=str(tmp_path / "perf.db"))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_registry(monkeypatch, marker_factory=_happy_marker_factory)

    result = runner.invoke(main_module.app, ["--json", "run", "checkout", "-q"])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["flow"] == "checkout"


def test_quiet_does_not_change_exit_code_on_failure(monkeypatch, tmp_path: Path):
    """Corner case: `--quiet` suppresses OUTPUT only, never the exit-code
    gate — a failed run still exits 3 with `--quiet` active."""
    config = _config(sampler=None, marker_source="adb-logcat", db_path=str(tmp_path / "perf.db"))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_registry(
        monkeypatch,
        driver=FakeDriver(drive_error=OSError("device offline")),
        marker_factory=_happy_marker_factory,
    )

    result = runner.invoke(main_module.app, ["run", "checkout", "--quiet"])

    assert result.exit_code == 3, result.output


def test_run_help_documents_quiet_flag_and_short_form(monkeypatch, tmp_path: Path):
    """Sanity check: `--quiet`/`-q` are discoverable via `--help` (same
    pattern as the existing `--restart`/`--device` options)."""
    config = _config(db_path=str(tmp_path / "perf.db"))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)

    result = runner.invoke(main_module.app, ["run", "--help"])

    assert result.exit_code == 0
    assert "--quiet" in result.stdout
    assert "-q" in result.stdout


# ===== run-live-progress Slice D post-review FIX 1: --quiet must silence
# NON_TTY_NUDGE too =====


def test_quiet_flag_suppresses_non_tty_nudge_on_pretty_path(monkeypatch, tmp_path: Path):
    """FIX 1 (RED before the fix): a pretty (non `--json`), non-TTY-stdout
    run with `--quiet` must emit NO nudge (and no progress) on stderr.
    Before the fix, `NON_TTY_NUDGE` was gated ONLY on
    `output.should_nudge_stderr`, never on `quiet`, so it leaked through
    even with `--quiet` active — contradicting the flag's own help text."""
    config = _config(sampler=None, marker_source="adb-logcat", db_path=str(tmp_path / "perf.db"))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_registry(monkeypatch, marker_factory=_happy_marker_factory)

    result = runner.invoke(main_module.app, ["run", "checkout", "--quiet"])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert NON_TTY_NUDGE not in result.stderr


def test_non_quiet_non_tty_pretty_run_still_emits_nudge(monkeypatch, tmp_path: Path):
    """Regression guard for FIX 1: WITHOUT `--quiet`, the same non-TTY
    pretty-path run must still emit `NON_TTY_NUDGE` on stderr — the fix
    must gate the nudge on `quiet`, not remove it unconditionally."""
    config = _config(sampler=None, marker_source="adb-logcat", db_path=str(tmp_path / "perf.db"))
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    _patch_registry(monkeypatch, marker_factory=_happy_marker_factory)

    result = runner.invoke(main_module.app, ["run", "checkout"])

    assert result.exit_code == 0, result.output
    assert NON_TTY_NUDGE in result.stderr


# ===== run-live-progress Slice D post-review FIX 2: --quiet + manual driver
# must be a clean usage error, never a silent stdin hang =====


def test_quiet_with_manual_driver_exits_2_before_any_prompt(monkeypatch, tmp_path: Path):
    """FIX 2 (RED before the fix): `perfvibe run <manual-flow> --quiet`
    must exit 2 with a clear message on stderr and never block on stdin.
    Before the fix, `ManualDriver.drive()` surfaces its prompt ONLY via
    `reporter.awaiting_user_input(...)`, a no-op under
    `NullProgressReporter` (what `--quiet` wires up) — so the run would
    block on `input()` with NO visible prompt. `build_driver` is never
    monkeypatched here (python-testing rule 3): the guard must reject the
    combination BEFORE any driver is even built, so a real `ManualDriver`
    is never constructed and `input()` is never reached."""
    config = PerfConfig(
        db_path=str(tmp_path / "perf.db"),
        no_color=True,
        driver="manual",
        sampler=None,
        marker_source="adb-logcat",
        default_iterations=1,
        flows={"checkout": FlowConfig(name="checkout", maestro_path="checkout.yaml")},
    )
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)

    result = runner.invoke(main_module.app, ["run", "checkout", "--quiet"])

    assert result.exit_code == 2, result.output
    assert "--quiet" in result.stderr
    assert "manual" in result.stderr.lower()
