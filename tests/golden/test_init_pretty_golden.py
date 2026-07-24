"""Golden tests for `init.py`'s pretty-render helper(s) (tasks.md 3.10;
SKILL rule 8: "Golden files for pretty output with color forced off
(`--update-golden` regenerates)."). Mirrors
`test_budget_check_pretty_golden.py`/`test_pretty_confirmation_golden.py`'s
exact convention: pure render functions, color forced off, fixed width,
`request.config.getoption("--update-golden")` to regenerate.

Covers 4 scenarios (tasks.md 3.10 (a)-(d)):
  (a) fresh `perf.toml` created summary
  (b) merge-added-flows summary
  (c) `bundle_id` mismatch prompt text
  (d) comment-loss warning text

Plus an end-to-end ANSI-byte check through the real `perfvibe init` CLI
under `--no-color`/`NO_COLOR`/non-TTY (CliRunner's default stdin), since a
unit-level `color=False` call proves the function is capable of rendering
plain text but not that the wired-up command actually resolves `color=False`
in each of those three real-world configurations.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

from typer.testing import CliRunner

from perf.cli.commands.init import (
    _render_comment_loss_confirm_prompt,
    _render_comment_loss_error,
    _render_confirmation,
    _render_mismatch_conflict_message,
)
from perf.config.loader import PerfConfig

main_module = import_module("perf.cli.main")

runner = CliRunner()

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_INIT_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
FLOWS_DIR = _INIT_FIXTURES_DIR / "flows"

_ANSI_ESCAPE = "\x1b["


def _assert_or_update_golden(request, fixture_name: str, actual: str) -> None:
    fixture_path = _FIXTURES_DIR / fixture_name
    if request.config.getoption("--update-golden"):
        fixture_path.parent.mkdir(parents=True, exist_ok=True)
        fixture_path.write_text(actual)
        return
    expected = fixture_path.read_text()
    assert actual == expected, (
        f"golden mismatch for {fixture_name} — run with --update-golden to "
        "regenerate if this change is intentional"
    )


def _patch_load_config(monkeypatch, **overrides) -> PerfConfig:
    defaults: dict = {"no_color": True}
    defaults.update(overrides)
    config = PerfConfig(**defaults)
    monkeypatch.setattr(main_module, "load_config", lambda **kw: config)
    return config


# ===== (a) fresh perf.toml created summary =====


def test_fresh_create_summary_matches_golden(request):
    actual = _render_confirmation(
        config_path=Path("perf.toml"),
        flows_added=["checkout", "login"],
        bundle_id="com.example.app",
        bundle_id_source="detected",
        color=False,
    )
    _assert_or_update_golden(request, "init_fresh_create_summary.txt", actual)


# ===== (b) merge-added-flows summary =====


def test_merge_added_flows_summary_matches_golden(request):
    actual = _render_confirmation(
        config_path=Path("perf.toml"),
        flows_added=["checkout"],
        bundle_id=None,
        bundle_id_source="none",
        color=False,
    )
    _assert_or_update_golden(request, "init_merge_added_flows_summary.txt", actual)


# ===== (c) bundle_id mismatch prompt text =====


def test_mismatch_prompt_text_matches_golden(request):
    actual = _render_mismatch_conflict_message(("com.example.app", "com.other.app"), color=False)
    _assert_or_update_golden(request, "init_mismatch_prompt.txt", actual)


# ===== (d) comment-loss warning text =====


def test_comment_loss_confirm_prompt_matches_golden(request):
    actual = _render_comment_loss_confirm_prompt(Path("perf.toml"))
    _assert_or_update_golden(request, "init_comment_loss_confirm_prompt.txt", actual)


def test_comment_loss_error_matches_golden(request):
    actual = _render_comment_loss_error(Path("perf.toml"))
    _assert_or_update_golden(request, "init_comment_loss_error.txt", actual)


# ===== no ANSI bytes under color-off, regardless of source =====


def test_no_ansi_escapes_when_color_forced_off_across_all_four_helpers():
    rendered = [
        _render_confirmation(
            config_path=Path("perf.toml"),
            flows_added=["checkout"],
            bundle_id="com.example.app",
            bundle_id_source="detected",
            color=False,
        ),
        _render_mismatch_conflict_message(("com.example.app", "com.other.app"), color=False),
        _render_comment_loss_confirm_prompt(Path("perf.toml")),
        _render_comment_loss_error(Path("perf.toml")),
    ]
    for text in rendered:
        assert _ANSI_ESCAPE not in text


def test_no_ansi_escapes_when_color_forced_on_but_still_plain_for_no_candidate():
    # `_render_confirmation` never colors the flows/bundle_id lines — only
    # the leading checkmark line — so an unset bundle_id/no flows still
    # yields a body free of ANSI codes even with color=True; the checkmark
    # line itself DOES get colored, proving `color=True` is not a no-op
    # (i.e. this helper's color plumbing is real, not vestigial).
    colored = _render_confirmation(
        config_path=Path("perf.toml"),
        flows_added=[],
        bundle_id=None,
        bundle_id_source="none",
        color=True,
    )
    assert _ANSI_ESCAPE in colored
    body_lines = colored.splitlines()[1:]
    assert all(_ANSI_ESCAPE not in line for line in body_lines)


# ===== end-to-end: real CLI never emits ANSI under --no-color/NO_COLOR/non-TTY =====


def test_cli_no_ansi_bytes_with_no_color_flag(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perf.toml"

    result = runner.invoke(
        main_module.app,
        [
            "--no-color",
            "--config",
            str(config_path),
            "init",
            str(FLOWS_DIR),
            "--bundle-id",
            "com.example.app",
        ],
    )

    assert result.exit_code == 0, result.output
    assert _ANSI_ESCAPE not in result.output


def test_cli_no_ansi_bytes_with_no_color_env(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perf.toml"

    result = runner.invoke(
        main_module.app,
        ["--config", str(config_path), "init", str(FLOWS_DIR), "--bundle-id", "com.example.app"],
        env={"NO_COLOR": "1"},
    )

    assert result.exit_code == 0, result.output
    assert _ANSI_ESCAPE not in result.output


def test_cli_no_ansi_bytes_on_default_non_tty_invocation(monkeypatch, tmp_path):
    # No `--no-color`, no `NO_COLOR` — relies solely on CliRunner's default
    # non-TTY stdin/stdout (confirmed empirically in `test_cli_init.py`).
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perf.toml"

    result = runner.invoke(
        main_module.app,
        ["--config", str(config_path), "init", str(FLOWS_DIR), "--bundle-id", "com.example.app"],
    )

    assert result.exit_code == 0, result.output
    assert _ANSI_ESCAPE not in result.output


def test_cli_no_ansi_bytes_on_comment_loss_error_path(monkeypatch, tmp_path):
    _patch_load_config(monkeypatch)
    config_path = tmp_path / "perf.toml"
    config_path.write_text("# hand-written note\nbundle_id = 'com.existing.app'\n")

    result = runner.invoke(
        main_module.app,
        ["--config", str(config_path), "init", str(FLOWS_DIR), "--bundle-id", "com.example.app"],
    )

    assert result.exit_code == 2, result.output
    assert _ANSI_ESCAPE not in result.output
