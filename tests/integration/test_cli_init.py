"""End-to-end CLI harness for `perf init` — REAL `typer` app + REAL fs I/O
against `tmp_path`, driven through the fixture trees committed in PR-A
(`tests/fixtures/flows`, `flows_mismatch`, `flows_empty`). Mirrors
`test_cli_budget_check.py`/`test_cli_run.py`'s "never monkeypatch the
thing under test" discipline: `load_config` is faked (it would otherwise
read the real `~/.config/perf/config.toml`), but `init`'s own command
body — discovery, appId reconciliation, TOML merge/serialize, and the
comment-loss guard — always runs for real against real files.

**PR-B coverage-gap batch** (see `docs/specs/init-command/tasks.md`'s
"Correction (CI coverage gate...)" note): covers tasks 3.1-3.7 only. Tasks
3.8 (wizard TTY simulation), 3.10 (golden pretty output), 3.12 (README),
and 3.13 (design.md cross-check) remain PR-C.

Every scenario below is a NON-interactive invocation (no TTY). Confirmed
empirically that `typer.testing.CliRunner.invoke()` wires stdin to a
non-TTY stream by default (`sys.stdin.isatty()` is `False` inside the
invoked command) — so `init`'s TTY auto-detect naturally takes the
non-interactive branch here without patching `isatty`. That patch is
specifically PR-C's task 3.8 concern (the *interactive* wizard path).
"""

from __future__ import annotations

import json
import os
import tomllib
from importlib import import_module
from pathlib import Path

import pytest
from typer.testing import CliRunner

from perf.adapters.driver_maestro import MaestroDriver
from perf.config.loader import PerfConfig, load_config

main_module = import_module("perf.cli.main")

runner = CliRunner()

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
FLOWS_DIR = _FIXTURES_DIR / "flows"
FLOWS_MISMATCH_DIR = _FIXTURES_DIR / "flows_mismatch"
FLOWS_EMPTY_DIR = _FIXTURES_DIR / "flows_empty"

# `flows/` minus everything under `subflows/` (excluded regardless of case).
_EXPECTED_FLOW_NAMES = {"login", "cold", "templated_launch", "missing_header", "no_separator"}


def _patch_load_config(monkeypatch, **overrides) -> PerfConfig:
    """`init` never reads `perf_config` itself (only the raw `--config`
    string threaded separately into `ctx.obj['config_path']`), so faking
    `load_config` here only avoids touching the real
    `~/.config/perf/config.toml` on the test machine — it does not change
    what `init` actually does."""

    defaults: dict = {"no_color": True}
    defaults.update(overrides)
    config = PerfConfig(**defaults)
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    return config


# ===== resilience batch Task 6: duplicate flow-stem collision =====


def test_duplicate_flow_stem_exits_2_listing_each_stem_and_paths(monkeypatch, tmp_path):
    """Task 6: two flow files sharing a stem (android/login.yaml vs
    ios/login.yaml) make `init` a usage error (exit 2) listing the stem,
    both relative paths, and a rename/restructure hint — never a silent
    drop, and never Python's default exit 1."""
    _patch_load_config(monkeypatch)
    flows_dir = tmp_path / "flows"
    (flows_dir / "android").mkdir(parents=True)
    (flows_dir / "ios").mkdir(parents=True)
    (flows_dir / "android" / "login.yaml").write_text("appId: com.a\n---\n")
    (flows_dir / "ios" / "login.yaml").write_text("appId: com.b\n---\n")
    (flows_dir / "checkout.yaml").write_text("appId: com.c\n---\n")
    config_path = tmp_path / "perfvibe.toml"

    result = runner.invoke(
        main_module.app,
        ["--config", str(config_path), "init", str(flows_dir)],
    )

    assert result.exit_code == 2, result.output
    assert result.exit_code != 1
    assert "login" in result.output
    assert str(Path("android") / "login.yaml") in result.output
    assert str(Path("ios") / "login.yaml") in result.output
    assert "rename" in result.output.lower()
    # Nothing was written — the collision is refused before any merge/write.
    assert not config_path.exists()


# ===== 3.1: fresh create + round-trip + --json payload shape =====


def test_fresh_config_created_and_round_trips_through_load_config_and_driver(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"

    result = runner.invoke(
        main_module.app,
        [
            "--json",
            "--config",
            str(config_path),
            "init",
            str(FLOWS_DIR),
            "--bundle-id",
            "com.example.app",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 2
    assert payload["config_path"] == str(config_path)
    assert payload["bundle_id"] == "com.example.app"
    assert payload["bundle_id_source"] == "flag"
    assert set(payload["flows_added"]) == _EXPECTED_FLOW_NAMES
    assert payload["flows_total"] == len(_EXPECTED_FLOW_NAMES)
    assert config_path.is_file()

    # Round-trip guarantee (spec I16): the written file parses via the
    # REAL `load_config`/`MaestroDriver` — every scaffolded flow is
    # config-known and accepted by the driver.
    loaded = load_config(cli_config_path=str(config_path))
    assert loaded.bundle_id == "com.example.app"
    assert set(loaded.flows) >= _EXPECTED_FLOW_NAMES

    known_flows = {
        name: flow.maestro_path for name, flow in loaded.flows.items() if flow.maestro_path
    }
    driver = MaestroDriver(known_flows)
    for name in _EXPECTED_FLOW_NAMES:
        command = driver.command(name, mode="warm", restart=False)
        assert command.argv[0] == "maestro"  # never raises ValueError(unknown flow)


def test_fresh_config_pretty_output_exits_0(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"

    result = runner.invoke(
        main_module.app,
        ["--config", str(config_path), "init", str(FLOWS_DIR), "--bundle-id", "com.example.app"],
    )

    assert result.exit_code == 0, result.output
    assert config_path.is_file()
    assert "perf init wrote" in result.output


# ===== 3.2: zero-flows dir + mismatch resolution =====


def test_zero_flows_dir_exits_2_and_writes_nothing(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"

    result = runner.invoke(
        main_module.app, ["--config", str(config_path), "init", str(FLOWS_EMPTY_DIR)]
    )

    assert result.exit_code == 2, result.output
    assert not config_path.exists()


def test_mismatch_non_interactive_without_bundle_id_exits_2(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"

    result = runner.invoke(
        main_module.app, ["--config", str(config_path), "init", str(FLOWS_MISMATCH_DIR)]
    )

    assert result.exit_code == 2, result.output
    assert "appid" in result.output.lower() or "bundle-id" in result.output.lower()
    assert not config_path.exists()


def test_mismatch_non_interactive_with_bundle_id_resolves_exit_0(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"

    result = runner.invoke(
        main_module.app,
        [
            "--json",
            "--config",
            str(config_path),
            "init",
            str(FLOWS_MISMATCH_DIR),
            "--bundle-id",
            "com.example.app",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["bundle_id"] == "com.example.app"
    assert payload["bundle_id_source"] == "flag"
    assert payload["appid_conflict"] == ["com.example.app", "com.other.app"]
    assert config_path.is_file()


# ===== 3.3: merge into an existing perf.toml + collision handling =====


def test_merge_new_flow_names_leaves_existing_entries_untouched(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"
    config_path.write_text(
        "bundle_id = 'com.existing.app'\n\n[flows.existing]\nmaestro_path = 'existing.yaml'\n"
    )

    result = runner.invoke(
        main_module.app,
        [
            "--json",
            "--config",
            str(config_path),
            "init",
            str(FLOWS_DIR),
            "--bundle-id",
            "com.example.app",
        ],
    )

    assert result.exit_code == 0, result.output
    merged = tomllib.loads(config_path.read_text())
    assert merged["flows"]["existing"]["maestro_path"] == "existing.yaml"
    assert set(merged["flows"]) >= _EXPECTED_FLOW_NAMES


def test_colliding_flow_name_without_force_exits_2_file_untouched(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"
    original_text = "[flows.login]\nmaestro_path = 'old/login.yaml'\n"
    config_path.write_text(original_text)

    result = runner.invoke(
        main_module.app,
        [
            "--config",
            str(config_path),
            "init",
            str(FLOWS_DIR),
            "--bundle-id",
            "com.example.app",
        ],
    )

    assert result.exit_code == 2, result.output
    assert config_path.read_text() == original_text


def test_colliding_flow_name_with_force_overwrites_exit_0(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"
    config_path.write_text("[flows.login]\nmaestro_path = 'old/login.yaml'\n")

    result = runner.invoke(
        main_module.app,
        [
            "--json",
            "--config",
            str(config_path),
            "init",
            str(FLOWS_DIR),
            "--bundle-id",
            "com.example.app",
            "--force",
        ],
    )

    assert result.exit_code == 0, result.output
    merged = tomllib.loads(config_path.read_text())
    assert merged["flows"]["login"]["maestro_path"] != "old/login.yaml"


# ===== 3.4: comment-preservation guard (tasks.md decision #3) =====


def test_comment_guard_requires_force_non_interactively(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"
    original_text = "# hand-written note, please keep me\nbundle_id = 'com.existing.app'\n"
    config_path.write_text(original_text)

    result = runner.invoke(
        main_module.app,
        [
            "--config",
            str(config_path),
            "init",
            str(FLOWS_DIR),
            "--bundle-id",
            "com.example.app",
        ],
    )

    assert result.exit_code == 2, result.output
    assert config_path.read_text() == original_text
    assert "comment" in result.output.lower()
    assert "--force" in result.output


def test_comment_guard_force_proceeds_and_overwrites(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"
    config_path.write_text("# hand-written note, please keep me\nbundle_id = 'com.existing.app'\n")

    result = runner.invoke(
        main_module.app,
        [
            "--json",
            "--config",
            str(config_path),
            "init",
            str(FLOWS_DIR),
            "--bundle-id",
            "com.example.app",
            "--force",
        ],
    )

    assert result.exit_code == 0, result.output
    merged = tomllib.loads(config_path.read_text())
    assert merged["bundle_id"] == "com.example.app"


# ===== 3.5: --driver/--db verbatim pass-through =====


def test_driver_and_db_written_verbatim_only_when_supplied(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"

    result = runner.invoke(
        main_module.app,
        [
            "--config",
            str(config_path),
            "init",
            str(FLOWS_DIR),
            "--bundle-id",
            "com.example.app",
            "--driver",
            "maestro",
            "--db",
            "custom.db",
        ],
    )

    assert result.exit_code == 0, result.output
    merged = tomllib.loads(config_path.read_text())
    assert merged["driver"] == "maestro"
    assert merged["db_path"] == "custom.db"


def test_driver_and_db_omitted_entirely_when_not_supplied(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"

    result = runner.invoke(
        main_module.app,
        [
            "--config",
            str(config_path),
            "init",
            str(FLOWS_DIR),
            "--bundle-id",
            "com.example.app",
        ],
    )

    assert result.exit_code == 0, result.output
    merged = tomllib.loads(config_path.read_text())
    assert "driver" not in merged
    assert "db_path" not in merged


# ===== 3.6: output path resolution (tasks.md decision #2) =====


def test_output_path_defaults_to_cwd_perfvibe_toml_when_config_omitted(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        main_module.app,
        ["--json", "init", str(FLOWS_DIR), "--bundle-id", "com.example.app"],
    )

    assert result.exit_code == 0, result.output
    default_path = tmp_path / "perfvibe.toml"
    assert default_path.is_file()
    payload = json.loads(result.stdout)
    assert payload["config_path"] == str(default_path)


def test_explicit_config_path_used_verbatim(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    explicit_path = tmp_path / "nested" / "custom.toml"
    explicit_path.parent.mkdir()

    result = runner.invoke(
        main_module.app,
        [
            "--json",
            "--config",
            str(explicit_path),
            "init",
            str(FLOWS_DIR),
            "--bundle-id",
            "com.example.app",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["config_path"] == str(explicit_path)
    assert explicit_path.is_file()
    # Default CWD path must NOT have been touched.
    assert not (tmp_path / "perfvibe.toml").exists()


# ===== 3.7: exit-code sweep — never 1; unwritable target exits 3 =====


@pytest.mark.parametrize(
    "flows_dir",
    [FLOWS_DIR, FLOWS_EMPTY_DIR, FLOWS_MISMATCH_DIR],
    ids=["flows", "flows_empty", "flows_mismatch"],
)
def test_init_never_exits_1(monkeypatch, tmp_path, flows_dir):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"

    result = runner.invoke(main_module.app, ["--config", str(config_path), "init", str(flows_dir)])

    assert result.exit_code != 1


@pytest.mark.parametrize(
    "extra_args",
    [
        [],
        ["--bundle-id", "com.example.app"],
        ["--bundle-id", "com.example.app", "--force"],
    ],
)
def test_init_never_exits_1_on_flows_dir_with_flags(monkeypatch, tmp_path, extra_args):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"

    result = runner.invoke(
        main_module.app,
        ["--config", str(config_path), "init", str(FLOWS_DIR), *extra_args],
    )

    assert result.exit_code != 1


@pytest.mark.skipif(
    os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="permission bits do not restrict a root-run test process",
)
def test_unwritable_target_dir_exits_3(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    config_path = readonly_dir / "perfvibe.toml"
    readonly_dir.chmod(0o500)  # read + execute only — no write permission
    try:
        result = runner.invoke(
            main_module.app,
            [
                "--config",
                str(config_path),
                "init",
                str(FLOWS_DIR),
                "--bundle-id",
                "com.example.app",
            ],
        )

        assert result.exit_code == 3, result.output
        assert result.exit_code != 1
    finally:
        readonly_dir.chmod(0o700)


# ===== 3.9+: --prune-missing (spec "Stale Flow Pruning") — every scenario
# below is non-interactive; the interactive confirm accept/decline and the
# comment-loss composition (tasks.md 3.1/3.2/3.8) live in
# `test_cli_init_wizard.py`, which already owns the TTY-simulation helper.
# =====

_STALE_ENTRY_TOML = (
    "bundle_id = 'com.existing.app'\n\n[flows.stale]\nmaestro_path = 'gone/stale.yaml'\n"
)


def test_non_interactive_yes_prunes_immediately_exit_0(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"
    config_path.write_text(_STALE_ENTRY_TOML)

    result = runner.invoke(
        main_module.app,
        [
            "--json",
            "--config",
            str(config_path),
            "init",
            str(FLOWS_DIR),
            "--bundle-id",
            "com.example.app",
            "--prune-missing",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 2
    assert payload["flows_pruned"] == ["stale"]
    merged = tomllib.loads(config_path.read_text())
    assert "stale" not in merged["flows"]


def test_non_interactive_no_yes_json_previews_full_state_and_exits_2(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"
    original_text = _STALE_ENTRY_TOML
    config_path.write_text(original_text)

    result = runner.invoke(
        main_module.app,
        [
            "--json",
            "--config",
            str(config_path),
            "init",
            str(FLOWS_DIR),
            "--bundle-id",
            "com.example.app",
            "--prune-missing",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 2
    assert payload["flows_pruned"] == ["stale"]
    assert set(payload["flows_added"]) == _EXPECTED_FLOW_NAMES
    assert config_path.read_text() == original_text  # NOT written


def test_non_interactive_no_yes_no_json_previews_to_stderr_and_exits_2(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"
    original_text = _STALE_ENTRY_TOML
    config_path.write_text(original_text)

    result = runner.invoke(
        main_module.app,
        [
            "--config",
            str(config_path),
            "init",
            str(FLOWS_DIR),
            "--bundle-id",
            "com.example.app",
            "--prune-missing",
        ],
    )

    assert result.exit_code == 2, result.output
    assert "stale" in result.output
    assert config_path.read_text() == original_text


def test_zero_missing_set_is_a_no_op_exit_0(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"
    config_path.write_text("bundle_id = 'com.existing.app'\n")

    result = runner.invoke(
        main_module.app,
        [
            "--json",
            "--config",
            str(config_path),
            "init",
            str(FLOWS_DIR),
            "--bundle-id",
            "com.example.app",
            "--prune-missing",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["flows_pruned"] == []


def test_zero_missing_set_is_a_no_op_with_yes_too(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"
    config_path.write_text("bundle_id = 'com.existing.app'\n")

    result = runner.invoke(
        main_module.app,
        [
            "--json",
            "--config",
            str(config_path),
            "init",
            str(FLOWS_DIR),
            "--bundle-id",
            "com.example.app",
            "--prune-missing",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["flows_pruned"] == []


def test_plain_init_without_prune_missing_leaves_stale_entry_untouched(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"
    config_path.write_text(_STALE_ENTRY_TOML)

    result = runner.invoke(
        main_module.app,
        [
            "--json",
            "--config",
            str(config_path),
            "init",
            str(FLOWS_DIR),
            "--bundle-id",
            "com.example.app",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["flows_pruned"] == []
    merged = tomllib.loads(config_path.read_text())
    assert merged["flows"]["stale"] == {"maestro_path": "gone/stale.yaml"}


def test_composes_with_force_collision_and_prune_in_one_write(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"
    config_path.write_text(
        "bundle_id = 'com.existing.app'\n\n"
        "[flows.login]\nmaestro_path = 'old/login.yaml'\n\n"
        "[flows.stale]\nmaestro_path = 'gone/stale.yaml'\n"
    )

    result = runner.invoke(
        main_module.app,
        [
            "--json",
            "--config",
            str(config_path),
            "init",
            str(FLOWS_DIR),
            "--bundle-id",
            "com.example.app",
            "--force",
            "--prune-missing",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    merged = tomllib.loads(config_path.read_text())
    assert merged["flows"]["login"]["maestro_path"] != "old/login.yaml"
    assert "stale" not in merged["flows"]


# ===== P9-P12 (pre-apply review corner cases) =====


def test_p9_zero_discovered_flows_wins_over_prune_missing(monkeypatch, tmp_path):
    """P9: `--flows-dir` discovers ZERO flows — the pre-existing zero-flows
    usage-error guard wins over `--prune-missing`: exit 2, existing
    `perf.toml` untouched, nothing pruned. `--prune-missing` can never be
    used to empty a flows table via an empty/wrong glob."""

    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"
    original_text = _STALE_ENTRY_TOML
    config_path.write_text(original_text)

    result = runner.invoke(
        main_module.app,
        [
            "--config",
            str(config_path),
            "init",
            str(FLOWS_EMPTY_DIR),
            "--prune-missing",
            "--yes",
        ],
    )

    assert result.exit_code == 2, result.output
    assert config_path.read_text() == original_text


def test_p10_comment_loss_guard_fires_first_with_only_yes(monkeypatch, tmp_path):
    """P10: commented `perf.toml` + missing flows, non-interactive, only
    `--yes` (no `--force`) — the comment-loss guard runs FIRST and is
    waived only by `--force`, so it fires its own message and exits 2
    before the prune gate is ever reached; nothing is pruned."""

    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"
    original_text = "# keep me\n" + _STALE_ENTRY_TOML
    config_path.write_text(original_text)

    result = runner.invoke(
        main_module.app,
        [
            "--config",
            str(config_path),
            "init",
            str(FLOWS_DIR),
            "--bundle-id",
            "com.example.app",
            "--prune-missing",
            "--yes",
        ],
    )

    assert result.exit_code == 2, result.output
    assert "comment" in result.output.lower()
    assert config_path.read_text() == original_text


def test_p11_force_and_yes_together_drop_comments_and_prune_in_one_write(monkeypatch, tmp_path):
    """P11: both `--force` AND `--yes` waive both guards non-interactively —
    comments dropped AND the missing entry pruned in the SAME write, exit 0."""

    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"
    config_path.write_text("# keep me\n" + _STALE_ENTRY_TOML)

    result = runner.invoke(
        main_module.app,
        [
            "--json",
            "--config",
            str(config_path),
            "init",
            str(FLOWS_DIR),
            "--bundle-id",
            "com.example.app",
            "--force",
            "--prune-missing",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    rewritten = config_path.read_text()
    assert "# keep me" not in rewritten
    merged = tomllib.loads(rewritten)
    assert "stale" not in merged["flows"]


def test_p12_preview_shows_both_flows_added_and_flows_pruned_populated(monkeypatch, tmp_path):
    """P12: non-interactive preview (no `--yes`) where discovery adds NEW
    flows while an existing entry is missing — the v2 payload must show
    BOTH `flows_added` and `flows_pruned` populated (the full would-be
    state), plus `bundle_id`/`flows_total`, with NO write."""

    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perfvibe.toml"
    original_text = _STALE_ENTRY_TOML
    config_path.write_text(original_text)

    result = runner.invoke(
        main_module.app,
        [
            "--json",
            "--config",
            str(config_path),
            "init",
            str(FLOWS_DIR),
            "--bundle-id",
            "com.example.app",
            "--prune-missing",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert set(payload["flows_added"]) == _EXPECTED_FLOW_NAMES  # populated
    assert payload["flows_pruned"] == ["stale"]  # populated
    assert payload["bundle_id"] == "com.example.app"
    assert payload["flows_total"] == len(_EXPECTED_FLOW_NAMES)
    assert config_path.read_text() == original_text  # no write happened
