"""`discover_flows`/`_is_subflows_segment` (design "Flow discovery"). Recursive
`*.yaml`/`*.yml` discovery under a flows dir, excluding any path segment
that equals `subflows` case-insensitively, at any depth. `init-command`
PR-B task 2.1.

Case-insensitivity of the segment match is unit-tested directly against
`_is_subflows_segment` with varied-case string literals — NOT via a real
on-disk case-variant directory. macOS/APFS collapses `subflows/` and
`Subflows/` into the SAME path, so a real differently-cased tree is not a
portable way to prove this (tasks.md's corrected task 1.6/2.1 note).
"""

from __future__ import annotations

from pathlib import Path

from perf.cli.commands.init import _is_subflows_segment, discover_flows

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
_FLOWS_DIR = _FIXTURES_DIR / "flows"
_FLOWS_EMPTY_DIR = _FIXTURES_DIR / "flows_empty"


def test_is_subflows_segment_matches_case_insensitively():
    assert _is_subflows_segment("subflows") is True
    assert _is_subflows_segment("SUBFLOWS") is True
    assert _is_subflows_segment("SubFlows") is True


def test_is_subflows_segment_rejects_other_names():
    assert _is_subflows_segment("checkout") is False
    assert _is_subflows_segment("subflows2") is False
    assert _is_subflows_segment("") is False


def test_discover_flows_finds_top_level_and_nested_real_flows():
    flows = discover_flows(_FLOWS_DIR)

    assert flows["login"] == _FLOWS_DIR / "login.yaml"
    assert flows["cold"] == _FLOWS_DIR / "checkout" / "cold.yaml"


def test_discover_flows_excludes_subflows_regardless_of_extension_case():
    flows = discover_flows(_FLOWS_DIR)

    assert "login-fragment" not in flows
    assert "util" not in flows


def test_discover_flows_includes_flows_with_missing_or_malformed_headers():
    flows = discover_flows(_FLOWS_DIR)

    # missing_header.yaml / no_separator.yaml are still discovered — a
    # missing/malformed appId header is not a discovery-level error.
    assert flows["missing_header"] == _FLOWS_DIR / "missing_header.yaml"
    assert flows["no_separator"] == _FLOWS_DIR / "no_separator.yaml"
    assert flows["templated_launch"] == _FLOWS_DIR / "templated_launch.yaml"


def test_discover_flows_yields_five_candidates_for_the_flows_fixture():
    flows = discover_flows(_FLOWS_DIR)

    assert sorted(flows) == sorted(
        ["login", "cold", "templated_launch", "missing_header", "no_separator"]
    )


def test_discover_flows_yields_zero_candidates_when_everything_is_under_subflows():
    flows = discover_flows(_FLOWS_EMPTY_DIR)

    assert flows == {}


def test_discover_flows_yields_zero_candidates_for_a_nonexistent_directory(tmp_path):
    flows = discover_flows(tmp_path / "does-not-exist")

    assert flows == {}


def test_discover_flows_yields_zero_candidates_for_an_empty_directory(tmp_path):
    flows = discover_flows(tmp_path)

    assert flows == {}
