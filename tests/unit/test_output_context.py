"""`resolve_output_context` color precedence (SKILL rule 6).

Regression (PR3 review, WARNING): a project/global `no_color = true` config
setting was previously ignored — color was resolved from only the CLI flag +
NO_COLOR env. The precedence chain is: CLI flag > NO_COLOR env > config > TTY.
"""

from __future__ import annotations

from perf.cli.output.context import resolve_output_context


class _FakeTTY:
    def isatty(self) -> bool:
        return True


def test_config_no_color_disables_color_on_tty():
    out = resolve_output_context(
        json_mode=False, no_color_cli=False, no_color_config=True, stdout=_FakeTTY(), env={}
    )
    assert out.color_enabled is False


def test_color_enabled_on_tty_when_nothing_disables_it():
    out = resolve_output_context(
        json_mode=False, no_color_cli=False, no_color_config=False, stdout=_FakeTTY(), env={}
    )
    assert out.color_enabled is True


def test_cli_flag_disables_color():
    out = resolve_output_context(
        json_mode=False, no_color_cli=True, no_color_config=False, stdout=_FakeTTY(), env={}
    )
    assert out.color_enabled is False


def test_no_color_env_disables_color():
    out = resolve_output_context(
        json_mode=False,
        no_color_cli=False,
        no_color_config=False,
        stdout=_FakeTTY(),
        env={"NO_COLOR": "1"},
    )
    assert out.color_enabled is False
