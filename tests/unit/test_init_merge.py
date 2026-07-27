"""`merge_config`/`has_comments` (spec "perf.toml Writing and Merge
Semantics"; tasks.md decision #3 comment-loss guard). `init-command` PR-B
task 2.9.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from perf.cli.commands.init import (
    FlowCollisionError,
    compute_pruned_flows,
    has_comments,
    merge_config,
)


def test_no_existing_config_creates_flows_from_scratch():
    merged = merge_config({}, {"login": Path("flows/login.yaml")}, "com.example.app", force=False)

    assert merged["flows"] == {"login": {"maestro_path": "flows/login.yaml"}}
    assert merged["bundle_id"] == "com.example.app"


def test_new_flow_names_merge_in_and_existing_entries_are_untouched():
    existing = {
        "driver": "maestro",
        "flows": {"login": {"maestro_path": "flows/login.yaml", "prompt": "Do the login"}},
    }

    merged = merge_config(
        existing, {"checkout": Path("flows/checkout.yaml")}, bundle_id=None, force=False
    )

    assert merged["flows"]["login"] == {
        "maestro_path": "flows/login.yaml",
        "prompt": "Do the login",
    }
    assert merged["flows"]["checkout"] == {"maestro_path": "flows/checkout.yaml"}
    assert merged["driver"] == "maestro"  # untouched top-level key


def test_colliding_flow_name_without_force_raises():
    existing = {"flows": {"login": {"maestro_path": "flows/login.yaml"}}}

    with pytest.raises(FlowCollisionError) as exc_info:
        merge_config(existing, {"login": Path("flows/new_login.yaml")}, bundle_id=None, force=False)

    assert exc_info.value.colliding_names == ("login",)


def test_colliding_flow_name_with_force_overwrites():
    existing = {"flows": {"login": {"maestro_path": "flows/login.yaml"}}}

    merged = merge_config(
        existing, {"login": Path("flows/new_login.yaml")}, bundle_id=None, force=True
    )

    assert merged["flows"]["login"] == {"maestro_path": "flows/new_login.yaml"}


def test_bundle_id_none_leaves_existing_bundle_id_untouched():
    existing = {"bundle_id": "com.example.app", "flows": {}}

    merged = merge_config(existing, {}, bundle_id=None, force=False)

    assert merged["bundle_id"] == "com.example.app"


def test_has_comments_detects_a_hash_outside_string_literals():
    assert has_comments("# a top-of-file comment\nbundle_id = 'com.example.app'\n") is True
    assert has_comments("bundle_id = 'com.example.app'  # trailing note\n") is True


def test_has_comments_ignores_a_hash_inside_a_string_value():
    assert has_comments("maestro_path = 'flows/#weird.yaml'\n") is False
    assert has_comments('maestro_path = "flows/#weird.yaml"\n') is False


def test_has_comments_false_when_no_comment_present():
    assert has_comments("bundle_id = 'com.example.app'\n\n[flows.login]\n") is False


# ===== compute_pruned_flows (spec "Stale Flow Pruning") =====


def test_compute_pruned_flows_some_missing_returns_sorted_names():
    existing = {"flows": {"login": {}, "checkout": {}, "stale": {}}}
    new_flows = {
        "login": Path("flows/login.yaml"),
        "checkout": Path("flows/checkout.yaml"),
    }

    assert compute_pruned_flows(existing, new_flows) == ["stale"]


def test_compute_pruned_flows_none_missing_is_empty():
    existing = {"flows": {"login": {}}}
    new_flows = {"login": Path("flows/login.yaml")}

    assert compute_pruned_flows(existing, new_flows) == []


def test_compute_pruned_flows_all_missing():
    existing = {"flows": {"checkout": {}, "login": {}}}
    new_flows: dict[str, Path] = {}

    assert compute_pruned_flows(existing, new_flows) == ["checkout", "login"]


def test_compute_pruned_flows_empty_existing_flows_table_is_empty():
    existing: dict[str, object] = {}
    new_flows = {"login": Path("flows/login.yaml")}

    assert compute_pruned_flows(existing, new_flows) == []


# ===== merge_config(prune=...) (spec "Stale Flow Pruning") =====


def test_merge_config_prune_false_is_identical_to_today():
    existing = {
        "flows": {
            "login": {"maestro_path": "flows/login.yaml"},
            "stale": {"maestro_path": "old.yaml"},
        }
    }

    merged = merge_config(
        existing,
        {"checkout": Path("flows/checkout.yaml")},
        bundle_id=None,
        force=False,
        prune=False,
    )

    assert merged["flows"]["stale"] == {"maestro_path": "old.yaml"}
    assert merged["flows"]["checkout"] == {"maestro_path": "flows/checkout.yaml"}


def test_merge_config_prune_true_drops_only_the_missing_entries():
    existing = {
        "flows": {
            "login": {"maestro_path": "old/login.yaml"},
            "stale": {"maestro_path": "old.yaml"},
        }
    }

    merged = merge_config(
        existing,
        {"login": Path("flows/login.yaml")},
        bundle_id=None,
        force=True,
        prune=True,
    )

    assert "stale" not in merged["flows"]
    assert merged["flows"]["login"] == {"maestro_path": "flows/login.yaml"}


def test_merge_config_prune_true_still_raises_flow_collision_without_force():
    """`prune` never bypasses the pre-existing collision refusal (design
    "Where the diff lives": prune and collision/force are orthogonal
    concerns; leaves colliding/force logic untouched)."""

    existing = {
        "flows": {
            "login": {"maestro_path": "old/login.yaml"},
            "stale": {"maestro_path": "old.yaml"},
        }
    }

    with pytest.raises(FlowCollisionError) as exc_info:
        merge_config(
            existing,
            {"login": Path("flows/new_login.yaml")},
            bundle_id=None,
            force=False,
            prune=True,
        )

    assert exc_info.value.colliding_names == ("login",)
