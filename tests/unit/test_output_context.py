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


class _FakePipe:
    def isatty(self) -> bool:
        return False


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
        stderr=_FakeTTY(),
        env={"NO_COLOR": "1"},
    )
    assert out.color_enabled is False
    # NO_COLOR disables BOTH streams, not just stdout.
    assert out.error_color_enabled is False


# ===== stderr color is resolved independently of stdout (the piping case) =====


def test_error_color_follows_stderr_tty_not_stdout():
    # `perfvibe run … > out.txt` — stdout piped, stderr still a terminal:
    # errors must stay colored even though stdout color is off.
    out = resolve_output_context(
        json_mode=False,
        no_color_cli=False,
        no_color_config=False,
        stdout=_FakePipe(),
        stderr=_FakeTTY(),
        env={},
    )
    assert out.color_enabled is False
    assert out.error_color_enabled is True


def test_error_color_off_when_stderr_not_a_tty():
    out = resolve_output_context(
        json_mode=False,
        no_color_cli=False,
        no_color_config=False,
        stdout=_FakeTTY(),
        stderr=_FakePipe(),
        env={},
    )
    assert out.color_enabled is True
    assert out.error_color_enabled is False


def test_cli_flag_disables_error_color_too():
    out = resolve_output_context(
        json_mode=False,
        no_color_cli=True,
        no_color_config=False,
        stdout=_FakeTTY(),
        stderr=_FakeTTY(),
        env={},
    )
    assert out.error_color_enabled is False
