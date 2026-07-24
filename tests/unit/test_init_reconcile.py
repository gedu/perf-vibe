"""`reconcile_bundle_id` (spec "Bundle ID Reconciliation"). Pure —
interactive/`--bundle-id` flag logic lives in the `init` command, not
here. `init-command` PR-B task 2.5.
"""

from __future__ import annotations

from perf.cli.commands.init import TEMPLATE, BundleReconciliation, reconcile_bundle_id


def test_single_concrete_value_becomes_the_candidate():
    result = reconcile_bundle_id({"login": "com.example.app", "checkout": "com.example.app"})

    assert result == BundleReconciliation(candidate="com.example.app", conflict=None)


def test_zero_concrete_values_leaves_no_candidate():
    result = reconcile_bundle_id({"login": None, "checkout": TEMPLATE})

    assert result == BundleReconciliation(candidate=None, conflict=None)


def test_differing_concrete_values_surface_as_a_conflict():
    result = reconcile_bundle_id({"app_a": "com.example.app", "app_b": "com.other.app"})

    assert result.candidate is None
    assert result.conflict == ("com.example.app", "com.other.app")


def test_template_and_none_values_are_treated_as_absent_alongside_a_single_concrete_value():
    result = reconcile_bundle_id(
        {"login": "com.example.app", "templated": TEMPLATE, "missing": None}
    )

    assert result == BundleReconciliation(candidate="com.example.app", conflict=None)


def test_empty_mapping_leaves_no_candidate():
    result = reconcile_bundle_id({})

    assert result == BundleReconciliation(candidate=None, conflict=None)
