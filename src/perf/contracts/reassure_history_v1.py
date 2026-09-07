"""`--json` machine contract for `perfvibe reassure history <name>` (SKILL
rule 6: "the machine contract is `--json` (carries `schema_version`); the
pretty view is lossy and MUST NEVER be parsed"; SKILL rule 8: "A contract
test MUST fail on any `--json` shape change without a `schema_version`
bump.").

`schema_version=1`. `{schema_version, name, points: [...]}` — ONE point per
IMPORT containing `name` (design D2: one import = one point, always), in
`store.reassure_series`'s own OLDEST→NEWEST order — the SAME chart-order
direction `history_v1`'s `runs` already uses, and the opposite direction
from `reassure_list_v1`'s roster.

Each point carries exactly SIX keys: `import_id`, `ordered_at`
(`COALESCE(created_date, imported_at)`, already resolved by the store),
`ordering_key` (`'created_date'`|`'imported_at'`), `commit_hash` (a LABEL
only — invariant I2, nothing here groups, filters, or joins on it; two
points may legitimately share it, per 0006's real baseline/current pair,
and MUST still surface as two distinct entries), and the two
independently-reduced `duration`/`count` `HistoryMetric` summaries
(invariant I1 — two separately-named series, never one zipped pair).

`branch` is deliberately absent, unlike `reassure_list_v1`'s per-import
dict: nothing in this command's spec (spec "reassure history <name> — Full
Series") or its renderer (`cli/output/reassure_history_pretty.py`) ever
reads a point's branch.

Mirrors `contracts/history_v1.py`'s pure-builder pattern: this function
accepts an already-fetched `Sequence[ReassureSeriesPoint]` — it never calls
`Store.reassure_series` itself, and never sorts, filters, or decides which
points belong (the store's job, per its own coverage-gap guarantee)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from perf.domain.model import HistoryMetric, ReassureSeriesPoint

__all__ = ["SCHEMA_VERSION", "build_reassure_history_payload"]

SCHEMA_VERSION = 1


def _metric_payload(metric: HistoryMetric | None) -> dict[str, Any] | None:
    if metric is None:
        return None
    return {
        "p50": metric.p50,
        "p90": metric.p90,
        "n": metric.n,
        "unit": metric.unit,
    }


def _point_payload(point: ReassureSeriesPoint) -> dict[str, Any]:
    return {
        "import_id": point.import_id,
        "ordered_at": point.ordered_at,
        "ordering_key": point.ordering_key,
        "commit_hash": point.commit_hash,
        "duration": _metric_payload(point.entry.duration),
        "count": _metric_payload(point.entry.count),
    }


def build_reassure_history_payload(
    *, name: str, points: Sequence[ReassureSeriesPoint]
) -> dict[str, Any]:
    """Builds the stable `--json` per-name historical series payload for
    `reassure history <name>`. `points` is already OLDEST→NEWEST and
    already restricted to imports containing `name` (the store's job, per
    `reassure_series`'s own coverage-gap guarantee) — this builder only
    shapes, it never filters or reorders."""

    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "points": [_point_payload(point) for point in points],
    }
