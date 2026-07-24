"""`parse_app_id` (design "appId line-scan (exact algorithm)"). Tolerant
line-scan of the pre-`---` header block — no YAML dependency.
`init-command` PR-B task 2.3.
"""

from __future__ import annotations

from perf.cli.commands.init import TEMPLATE, parse_app_id


def test_parses_a_concrete_appid():
    text = "appId: com.example.app\n---\n- launchApp\n"

    assert parse_app_id(text) == "com.example.app"


def test_parses_a_quoted_appid_and_strips_matching_quotes():
    text = 'appId: "com.example.app"\n---\n- launchApp\n'

    assert parse_app_id(text) == "com.example.app"


def test_parses_a_single_quoted_appid_and_strips_matching_quotes():
    text = "appId: 'com.example.app'\n---\n- launchApp\n"

    assert parse_app_id(text) == "com.example.app"


def test_templated_appid_returns_the_template_sentinel():
    text = "appId: ${APP_ID}\n---\n- launchApp\n"

    assert parse_app_id(text) == TEMPLATE


def test_missing_appid_returns_none():
    text = "env:\n  FOO: bar\n---\n- launchApp\n"

    assert parse_app_id(text) is None


def test_stops_scanning_at_the_separator_line():
    # An `appId:` line AFTER `---` is part of the commands section, not the
    # header — it must NOT be picked up.
    text = "---\nappId: com.example.app\n- launchApp\n"

    assert parse_app_id(text) is None


def test_unterminated_file_with_no_separator_is_still_bounded():
    text = "- launchApp\n- tapOn: 'Login'\n"

    assert parse_app_id(text) is None


def test_no_separator_but_concrete_appid_present():
    text = "appId: com.example.app\n- launchApp\n"

    assert parse_app_id(text) == "com.example.app"
