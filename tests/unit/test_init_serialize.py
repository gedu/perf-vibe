"""`serialize_toml` (design "TOML write" / "String escaping"). Full
canonical re-serialize — never a blind text-append. `init-command` PR-B
task 2.7.
"""

from __future__ import annotations

import tomllib

from perf.cli.commands.init import serialize_toml


def test_root_scalars_come_before_tables_and_round_trip():
    data = {
        "bundle_id": "com.example.app",
        "driver": "maestro",
        "flows": {"login": {"maestro_path": "flows/login.yaml"}},
    }

    text = serialize_toml(data)
    parsed = tomllib.loads(text)

    assert parsed == data
    root_lines = text.splitlines()
    assert root_lines.index("bundle_id = 'com.example.app'") < root_lines.index("[flows.login]")


def test_maestro_path_defaults_to_a_literal_string():
    data = {"flows": {"login": {"maestro_path": "flows/login.yaml"}}}

    text = serialize_toml(data)

    assert "maestro_path = 'flows/login.yaml'" in text


def test_falls_back_to_a_basic_string_when_value_contains_a_single_quote():
    data = {"flows": {"login": {"maestro_path": "flows/o'brien.yaml"}}}

    text = serialize_toml(data)

    # Falls back to an escaped BASIC string (never a literal `'…'` wrap,
    # which cannot itself contain a `'`).
    assert 'maestro_path = "flows/o\'brien.yaml"' in text
    assert tomllib.loads(text)["flows"]["login"]["maestro_path"] == "flows/o'brien.yaml"


def test_falls_back_to_a_basic_string_when_value_contains_a_control_character():
    data = {"flows": {"login": {"maestro_path": "flows/login\nfile.yaml"}}}

    text = serialize_toml(data)
    parsed = tomllib.loads(text)

    assert parsed["flows"]["login"]["maestro_path"] == "flows/login\nfile.yaml"
    assert "'flows/login\nfile.yaml'" not in text


def test_multiple_flow_tables_all_round_trip():
    data = {
        "flows": {
            "login": {"maestro_path": "flows/login.yaml"},
            "checkout": {"maestro_path": "flows/checkout/cold.yaml"},
        }
    }

    text = serialize_toml(data)
    parsed = tomllib.loads(text)

    assert parsed == data


def test_empty_data_serializes_to_empty_text():
    assert serialize_toml({}) == ""
