"""Unit tests for the ASCII banner gating rules (roadmap #45)."""

from __future__ import annotations

from perf.cli.banner import render_banner, should_show_banner


def test_banner_shown_on_tty_without_json():
    assert should_show_banner(json_mode=False, stdout_is_tty=True) is True


def test_banner_hidden_when_json_mode_even_on_tty():
    assert should_show_banner(json_mode=True, stdout_is_tty=True) is False


def test_banner_hidden_on_non_tty_pipe():
    assert should_show_banner(json_mode=False, stdout_is_tty=False) is False


def test_banner_hidden_on_non_tty_pipe_even_with_json():
    assert should_show_banner(json_mode=True, stdout_is_tty=False) is False


def test_banner_has_no_ansi_codes_when_color_disabled():
    banner = render_banner(color=False)
    assert "\x1b[" not in banner


def test_banner_has_ansi_codes_when_color_enabled():
    banner = render_banner(color=True)
    assert "\x1b[" in banner


def test_banner_is_small_and_tasteful():
    banner = render_banner(color=False)
    lines = banner.splitlines()
    assert 1 <= len(lines) <= 10
    assert all(len(line) <= 40 for line in lines)
