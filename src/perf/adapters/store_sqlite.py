"""`Store` port adapter — SQLite via stdlib `sqlite3` (SKILL rule 3).

PR2 store-half. Owns:
  - connection setup + pragmas (`foreign_keys`, `journal_mode=WAL`,
    `busy_timeout`) per §9.2,
  - the migration runner (§9.5): reads `PRAGMA user_version`, applies
    ordered `db/migrations/*.sql` files whose numeric prefix is greater
    than the current version, then bumps `user_version` — all inside one
    transaction. Migration files are loaded ONLY from this package's own
    `db/migrations/` directory (resolved `__file__`-relative), NEVER from
    a user-supplied path,
  - the §9.6 ingestion transaction (`save_run`): upserts the device/flow/
    metric dimensions, inserts the run/iteration/measure/system_sample
    facts, all in one `BEGIN`/`COMMIT`; ANY exception rolls back the
    ENTIRE run — a crashed run leaves ZERO rows,
  - a minimal read (`get_run_summary`) for `run`'s own confirmation
    output.

PR-B (`compare` Phase 2, Rev 3) additionally owns the bounded `compare`
read models: `history` (the naive per-metric window), `latest_run` /
`latest_measure_summary` / `latest_system_sample_points` (the LATEST run
being evaluated), and `baseline_measure_points` /
`baseline_system_sample_points` (the windowed, batched-per-metric-family
baseline reads, backed by the additive `idx_run_baseline` index —
`db/migrations/0002_compare_baseline_index.sql`). All pure reads — no
new write path.

SQL-injection safety (SKILL rule 4): every value (device_key, flow name,
metric/marker name, paths, metadata) is bound via `?` placeholders. SQL
identifiers (table/column names) are static literals in this file only;
no identifier is ever built from a `?`-bound value. The one apparent
exception — `PRAGMA user_version = <int>`, which SQLite does not allow to
be parameterized — interpolates an integer that was already validated as
digits-only by `_parse_migration_version` before use, never raw text. The
`compare` read models' `UNION ALL` branches over `_SYSTEM_SAMPLE_METRIC_FIELDS`
also interpolate SQL identifiers (column names) — that tuple is FIXED at
import time from `SystemSample`'s own dataclass fields, never derived
from a runtime/user-supplied name.
"""

from __future__ import annotations

import sqlite3
from dataclasses import fields as dc_fields
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Optional, Sequence, Union

from perf.domain.model import Marker, RunContext, RunPoint, SystemSample, default_higher_is_better
from perf.domain.ports import Clock

# Resolved __file__-relative to THIS package's own db/ directory — never a
# user-supplied path. `adapters/store_sqlite.py` -> parent is `adapters/`,
# parent.parent is the `perf` package root.
_PACKAGE_DB_DIR = Path(__file__).resolve().parent.parent / "db"
_MIGRATIONS_DIR = _PACKAGE_DB_DIR / "migrations"

# The `system_sample` aggregate field names (excluding the join key) —
# derived from the domain model, not hardcoded twice, so the "metric"
# dimension direction-metadata upsert (spec: "Direction-Aware Metric
# Metadata") tracks `SystemSample` if it ever grows a field.
_SYSTEM_SAMPLE_METRIC_FIELDS: tuple[str, ...] = tuple(
    f.name for f in dc_fields(SystemSample) if f.name != "iteration_idx"
)


# ===== `compare` read-model row shapes (PR-B, design Rev 3 "Bounded
# baseline query shape") — adapter-local, NOT domain types (the domain
# `RunPoint` — `git_commit, metric_name, value, started_at` — is reused
# directly for the measure-family baseline rows; these carry the extra
# per-run/per-iteration fields the system_sample family and the latest-run
# reads need). =====


class LatestRun(NamedTuple):
    """The single most recent run for a flow+device+mode — the run
    `compare` evaluates. `git_commit` is bound as the excluded "current
    commit" in the baseline queries below."""

    run_id: int
    git_commit: Optional[str]
    started_at: str


class MeasureSummaryPoint(NamedTuple):
    """One measure-family metric's LATEST-run percentile summary —
    `run_metric_summary.p90_ms` joined with `metric` for direction/unit
    metadata (measure/marker units ARE correctly threaded at ingestion,
    unlike system_sample fields — see `_SYSTEM_SAMPLE_UNITS` in
    `adapters/analyzer_sql.py`)."""

    metric_name: str
    unit: str
    higher_is_better: bool
    p90_ms: float
    sample_n: int


class SystemSampleRawPoint(NamedTuple):
    """One raw per-ITERATION `system_sample` observation for the LATEST
    run, pre warm-up-drop and pre-percentile (the analyzer applies both —
    warm-up discard `K` is a `system_sample`-only concern, spec 'Warm-Up
    Discard Asymmetry')."""

    metric_name: str
    iteration_idx: int
    value: float


class BaselineSystemSamplePoint(NamedTuple):
    """One raw per-iteration `system_sample` BASELINE observation, batched
    across the whole family and windowed to `baseline_n` commits (Rev 3).
    Carries `run_id` (unlike the domain `RunPoint`) so the analyzer can
    group same-run iterations together BEFORE collapsing to a per-run
    percentile and then to a per-commit median."""

    run_id: int
    git_commit: str
    started_at: str
    metric_name: str
    iteration_idx: int
    value: float


class _RealClock:
    """Default `Clock` — used when no fake is injected (production path)."""

    def now_utc_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()


def _parse_migration_version(filename: str) -> int:
    """Extract and validate the numeric version prefix of a migration
    filename (e.g. `0001_init.sql` -> 1). Raises `ValueError` for anything
    non-numeric — this validation is what makes it safe to later
    string-format the value into `PRAGMA user_version = <n>` (PRAGMA does
    not support `?` bind parameters)."""

    prefix = filename.split("_", 1)[0]
    if not prefix.isdigit():
        raise ValueError(
            f"Migration filename {filename!r} must start with a numeric version prefix"
        )
    return int(prefix)


class SqliteStore:
    """`Store` Protocol (`domain/ports.py`) implementation. `db_path` opens
    a LOCAL SQLite file only — never executed or imported."""

    def __init__(
        self,
        db_path: Union[str, Path],
        *,
        clock: Optional[Clock] = None,
        busy_timeout_ms: int = 5000,
    ) -> None:
        self._db_path = Path(db_path)
        self._busy_timeout_ms = int(busy_timeout_ms)
        self._clock: Clock = clock if clock is not None else _RealClock()
        self._conn = self._connect()
        self._migrate()

    # ----- lifecycle -----

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqliteStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _connect(self) -> sqlite3.Connection:
        # isolation_level=None (autocommit) so this class owns transaction
        # boundaries explicitly via literal BEGIN/COMMIT/ROLLBACK — no
        # implicit sqlite3-module transaction management to reason about.
        conn = sqlite3.connect(str(self._db_path), isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        return conn

    # ----- migration runner (§9.5) -----

    def _pending_migrations(self, current_version: int) -> list[tuple[int, Path]]:
        pending = [
            (_parse_migration_version(path.name), path)
            for path in _MIGRATIONS_DIR.glob("*.sql")
        ]
        pending = [(version, path) for version, path in pending if version > current_version]
        pending.sort(key=lambda vp: vp[0])
        return pending

    def _migrate(self) -> None:
        conn = self._conn
        current_version = conn.execute("PRAGMA user_version").fetchone()[0]
        pending = self._pending_migrations(current_version)
        if not pending:
            return  # already at the latest version — no-op (idempotent)

        target_version = pending[-1][0]
        script_parts = ["BEGIN;"]
        script_parts.extend(path.read_text() for _, path in pending)
        # `target_version` came from `_parse_migration_version` (digits-only,
        # already validated) — PRAGMA cannot bind `?` params, so this is the
        # one sanctioned string-format, never raw/user-supplied text.
        script_parts.append(f"PRAGMA user_version = {target_version};")
        script_parts.append("COMMIT;")

        try:
            conn.executescript("\n".join(script_parts))
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # ----- §9.6 ingestion transaction -----

    def save_run(
        self,
        ctx: RunContext,
        flow_name: str,
        iterations: int,
        mode: str,
        source: str,
        markers: Sequence[Marker],
        samples: Sequence[SystemSample],
        raw_report_path: Optional[str],
    ) -> int:
        conn = self._conn
        conn.execute("BEGIN")
        try:
            device_id = self._upsert_device(conn, ctx)
            flow_id = self._upsert_flow(conn, flow_name)
            metric_ids = self._upsert_metrics(conn, markers, samples)

            run_id = self._insert_run(
                conn, ctx, flow_id, device_id, iterations, mode, source, raw_report_path
            )
            self._insert_measures(conn, run_id, markers, metric_ids)
            self._insert_iterations_and_samples(conn, run_id, samples)

            conn.execute("COMMIT")
            return run_id
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # ----- dimension upserts (device/flow/metric) -----

    @staticmethod
    def _upsert_device(conn: sqlite3.Connection, ctx: RunContext) -> int:
        conn.execute(
            """
            INSERT INTO device (device_key, model, os_version, is_emulator)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(device_key) DO NOTHING
            """,
            (ctx.device_key, ctx.model, ctx.os_version, int(ctx.is_emulator)),
        )
        row = conn.execute(
            "SELECT device_id FROM device WHERE device_key = ?", (ctx.device_key,)
        ).fetchone()
        return row[0]

    @staticmethod
    def _upsert_flow(conn: sqlite3.Connection, flow_name: str) -> int:
        conn.execute(
            "INSERT INTO flow (name) VALUES (?) ON CONFLICT(name) DO NOTHING",
            (flow_name,),
        )
        row = conn.execute("SELECT flow_id FROM flow WHERE name = ?", (flow_name,)).fetchone()
        return row[0]

    @staticmethod
    def _upsert_metric(
        conn: sqlite3.Connection, name: str, higher_is_better: bool, unit: str = "ms"
    ) -> int:
        conn.execute(
            """
            INSERT INTO metric (name, higher_is_better, unit)
            VALUES (?, ?, ?)
            ON CONFLICT(name) DO NOTHING
            """,
            (name, int(higher_is_better), unit),
        )
        row = conn.execute("SELECT metric_id FROM metric WHERE name = ?", (name,)).fetchone()
        return row[0]

    def _upsert_metrics(
        self,
        conn: sqlite3.Connection,
        markers: Sequence[Marker],
        samples: Sequence[SystemSample],
    ) -> dict:
        metric_ids: dict = {}
        for marker in markers:
            if marker.name not in metric_ids:
                metric_ids[marker.name] = self._upsert_metric(
                    conn,
                    marker.name,
                    default_higher_is_better(marker.name),
                    unit=marker.unit,
                )
        for name in self._captured_system_sample_metric_names(samples):
            if name not in metric_ids:
                metric_ids[name] = self._upsert_metric(conn, name, default_higher_is_better(name))
        return metric_ids

    @staticmethod
    def _captured_system_sample_metric_names(samples: Sequence[SystemSample]) -> set:
        names: set = set()
        for sample in samples:
            for field_name in _SYSTEM_SAMPLE_METRIC_FIELDS:
                if getattr(sample, field_name) is not None:
                    names.add(field_name)
        return names

    # ----- fact inserts (run/iteration/measure/system_sample) -----

    def _insert_run(
        self,
        conn: sqlite3.Connection,
        ctx: RunContext,
        flow_id: int,
        device_id: int,
        iterations: int,
        mode: str,
        source: str,
        raw_report_path: Optional[str],
    ) -> int:
        cur = conn.execute(
            """
            INSERT INTO run (
                flow_id, device_id, started_at, iterations, mode, source,
                git_commit, git_branch, app_version, is_dev_bundle,
                bundle_source, build_variant, tool_version, raw_report_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                flow_id,
                device_id,
                self._clock.now_utc_iso(),
                iterations,
                mode,
                source,
                ctx.git_commit,
                ctx.git_branch,
                ctx.app_version,
                None if ctx.is_dev_bundle is None else int(ctx.is_dev_bundle),
                ctx.bundle_source,
                ctx.build_variant,
                ctx.tool_version,
                raw_report_path,
            ),
        )
        return cur.lastrowid

    @staticmethod
    def _insert_measures(
        conn: sqlite3.Connection,
        run_id: int,
        markers: Sequence[Marker],
        metric_ids: dict,
    ) -> None:
        for marker in markers:
            conn.execute(
                "INSERT INTO measure (run_id, metric_id, duration_ms) VALUES (?, ?, ?)",
                (run_id, metric_ids[marker.name], marker.value),
            )

    @staticmethod
    def _insert_iterations_and_samples(
        conn: sqlite3.Connection, run_id: int, samples: Sequence[SystemSample]
    ) -> None:
        for sample in samples:
            cur = conn.execute(
                "INSERT INTO iteration (run_id, idx) VALUES (?, ?)",
                (run_id, sample.iteration_idx),
            )
            iteration_id = cur.lastrowid
            conn.execute(
                """
                INSERT INTO system_sample (
                    iteration_id, total_time_ms, start_time_ms,
                    fps_avg, fps_min, ram_avg_mb, ram_peak_mb,
                    cpu_avg_pct, cpu_peak_pct
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    iteration_id,
                    sample.total_time_ms,
                    sample.start_time_ms,
                    sample.fps_avg,
                    sample.fps_min,
                    sample.ram_avg_mb,
                    sample.ram_peak_mb,
                    sample.cpu_avg_pct,
                    sample.cpu_peak_pct,
                ),
            )

    # ----- minimal read for run's own confirmation output -----

    def get_run_summary(self, run_id: int) -> Optional[dict]:
        """Minimal read model for `run`'s confirmation output only. History/
        compare read models (`Store.history`) are NOT this PR."""

        row = self._conn.execute(
            """
            SELECT r.run_id, f.name, d.device_key, r.started_at, r.iterations,
                   r.mode, r.source, r.is_dev_bundle, r.raw_report_path
            FROM run r
            JOIN flow f ON f.flow_id = r.flow_id
            JOIN device d ON d.device_id = r.device_id
            WHERE r.run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return None

        (
            run_id_,
            flow_name,
            device_key,
            started_at,
            iterations,
            mode,
            source,
            is_dev_bundle,
            raw_report_path,
        ) = row

        measures_captured = self._conn.execute(
            "SELECT COUNT(*) FROM measure WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        iterations_captured = self._conn.execute(
            "SELECT COUNT(*) FROM iteration WHERE run_id = ?", (run_id,)
        ).fetchone()[0]

        return {
            "run_id": run_id_,
            "flow_name": flow_name,
            "device_key": device_key,
            "started_at": started_at,
            "iterations": iterations,
            "mode": mode,
            "source": source,
            "is_dev_bundle": None if is_dev_bundle is None else bool(is_dev_bundle),
            "raw_report_path": raw_report_path,
            "measures_captured": measures_captured,
            "iterations_captured": iterations_captured,
        }

    # ----- `compare` read models (PR-B, design Rev 3 "Bounded Performance") -----
    #
    # Every value below is `?`-bound; every SQL identifier (table/column
    # name) is a STATIC literal — either hardcoded in this file or drawn
    # from `_SYSTEM_SAMPLE_METRIC_FIELDS`, a FIXED tuple derived at import
    # time from `SystemSample`'s own dataclass fields, never from a
    # runtime/user-supplied name (SKILL rule 4).

    def history(
        self, flow_name: str, metric_name: str, device_key: str, limit: int
    ) -> Sequence[RunPoint]:
        """The naive "last N RUNS" window for ONE metric (`Store` Protocol,
        `domain/ports.py`) — every run in `started_at` order, uncollapsed.
        Deliberately NOT commit-aware: this is what makes it the WRONG
        baseline for `compare` (spec "Naive last-10-RUNS window gives a
        different, wrong baseline") — `baseline_measure_points` +
        `domain/statistics.median_by_commit` is the correct policy."""

        rows = self._conn.execute(
            """
            SELECT r.git_commit, r.started_at, s.p90_ms
            FROM run r
            JOIN flow f ON f.flow_id = r.flow_id
            JOIN device d ON d.device_id = r.device_id
            JOIN run_metric_summary s ON s.run_id = r.run_id
            JOIN metric m ON m.metric_id = s.metric_id
            WHERE f.name = ? AND d.device_key = ? AND m.name = ?
              AND r.git_commit IS NOT NULL
            ORDER BY r.started_at DESC
            LIMIT ?
            """,
            (flow_name, device_key, metric_name, limit),
        ).fetchall()
        return [
            RunPoint(git_commit=commit, metric_name=metric_name, value=value, started_at=started_at)
            for commit, started_at, value in rows
        ]

    def latest_run(self, flow_name: str, device_key: str, mode: str) -> Optional[LatestRun]:
        """The single most recent run `compare` evaluates — `None` when the
        flow/device/mode combination has no runs at all (corner case
        C2/C7; the CLI layer, PR-C, maps this to the usage-error exit)."""

        row = self._conn.execute(
            """
            SELECT r.run_id, r.git_commit, r.started_at
            FROM run r
            JOIN flow f ON f.flow_id = r.flow_id
            JOIN device d ON d.device_id = r.device_id
            WHERE f.name = ? AND d.device_key = ? AND r.mode = ?
            ORDER BY r.started_at DESC, r.run_id DESC
            LIMIT 1
            """,
            (flow_name, device_key, mode),
        ).fetchone()
        if row is None:
            return None
        return LatestRun(run_id=row[0], git_commit=row[1], started_at=row[2])

    def latest_measure_summary(self, run_id: int) -> Sequence[MeasureSummaryPoint]:
        """Every measure-family metric's p90/sample-count for ONE run, in a
        SINGLE query (batched across the whole family, mirroring
        `baseline_measure_points`)."""

        rows = self._conn.execute(
            """
            SELECT m.name, m.unit, m.higher_is_better, s.p90_ms, s.n
            FROM run_metric_summary s
            JOIN metric m ON m.metric_id = s.metric_id
            WHERE s.run_id = ?
            """,
            (run_id,),
        ).fetchall()
        return [
            MeasureSummaryPoint(
                metric_name=name, unit=unit, higher_is_better=bool(higher), p90_ms=p90, sample_n=n
            )
            for name, unit, higher, p90, n in rows
        ]

    def latest_system_sample_points(self, run_id: int) -> Sequence[SystemSampleRawPoint]:
        """Every `system_sample` metric's raw per-iteration values for ONE
        run, batched across the WHOLE family in a single `UNION ALL`
        query — no per-metric fan-out. The warm-up `idx < K` drop and the
        per-run percentile reduction happen in the analyzer, not here."""

        union_sql = " UNION ALL ".join(
            f"SELECT '{field}' AS metric_name, i.idx AS iteration_idx, s.{field} AS value "
            "FROM iteration i JOIN system_sample s ON s.iteration_id = i.iteration_id "
            f"WHERE i.run_id = ? AND s.{field} IS NOT NULL"
            for field in _SYSTEM_SAMPLE_METRIC_FIELDS
        )
        params = (run_id,) * len(_SYSTEM_SAMPLE_METRIC_FIELDS)
        rows = self._conn.execute(union_sql, params).fetchall()
        return [
            SystemSampleRawPoint(metric_name=name, iteration_idx=idx, value=value)
            for name, idx, value in rows
        ]

    def baseline_measure_points(
        self,
        flow_name: str,
        device_key: str,
        mode: str,
        current_commit: Optional[str],
        baseline_n: int,
    ) -> Sequence[RunPoint]:
        """Rev 3 bounded, batched baseline read for the WHOLE measure
        family (design 'Bounded baseline query shape'): ONE query, no
        per-metric filter — a `metric_name` column lets it serve every
        measure-family metric at once. Windowed to the most recent
        `baseline_n` COMMITS (not runs); excludes dev-bundle runs and
        `current_commit`; seeks via `idx_run_baseline`
        `(flow_id, device_id, mode, started_at)`. Pre-collapse: repeated
        same-commit runs are returned as separate rows — the caller
        (`SqlAnalyzer`) applies `domain/statistics.median_by_commit`.

        FIX 1 (BLOCKER, PR-B review): `run_metric_summary.p90_ms` is NULL
        for an n=1 run (`CAST(0.9*1 AS INT)` truncates to 0, nothing
        qualifies) — reachable via `perf run --iterations 1`. Such a run
        has no meaningful tail percentile, so it contributes NO baseline
        point at all (`s.p90_ms IS NOT NULL`, mirroring
        `baseline_system_sample_points`'s `s.{field} IS NOT NULL` filter)
        rather than leaking a `None` into `median_by_commit`."""

        current_commit_clause = ""
        params: list = [flow_name, device_key, mode]
        if current_commit is not None:
            current_commit_clause = "AND r.git_commit <> ?"
            params.append(current_commit)
        params.append(baseline_n)

        rows = self._conn.execute(
            f"""
            WITH eligible AS (
              -- FIX 4 (PR-B review, empirical): `eligible` technically
              -- scans every (flow, device, mode) row before `recent`
              -- limits by commit — the SCAN cost is bounded by
              -- `idx_run_baseline`, while `baseline_n` bounds the RESULT
              -- set; `tests/integration/test_compare_perf.py` empirically
              -- proves this stays fast at ~5000 seeded runs.
              SELECT r.run_id, r.git_commit, r.started_at
              FROM run r
              JOIN flow f ON f.flow_id = r.flow_id
              JOIN device d ON d.device_id = r.device_id
              WHERE f.name = ? AND d.device_key = ? AND r.mode = ?
                AND COALESCE(r.is_dev_bundle, 0) = 0
                AND r.git_commit IS NOT NULL
                {current_commit_clause}
            ),
            recent AS (
              SELECT git_commit FROM eligible
              GROUP BY git_commit
              ORDER BY MAX(started_at) DESC
              LIMIT ?
            ),
            per_run AS (
              SELECT e.git_commit, e.started_at, m.name AS metric_name, s.p90_ms AS value
              FROM eligible e
              JOIN recent rc ON rc.git_commit = e.git_commit
              JOIN run_metric_summary s ON s.run_id = e.run_id
              JOIN metric m ON m.metric_id = s.metric_id
              WHERE s.p90_ms IS NOT NULL
            )
            SELECT git_commit, metric_name, value, started_at FROM per_run
            """,
            params,
        ).fetchall()
        return [
            RunPoint(git_commit=commit, metric_name=metric_name, value=value, started_at=started_at)
            for commit, metric_name, value, started_at in rows
        ]

    def baseline_system_sample_points(
        self,
        flow_name: str,
        device_key: str,
        mode: str,
        current_commit: Optional[str],
        baseline_n: int,
    ) -> Sequence[BaselineSystemSamplePoint]:
        """Rev 3 bounded, batched baseline read for the WHOLE
        `system_sample` family — same `eligible`/`recent` windowing as
        `baseline_measure_points`, but returns raw per-ITERATION rows
        (`run_id` + `iteration_idx`) so the analyzer can apply the
        `system_sample`-only warm-up `idx < K` drop before reducing to a
        per-run percentile. ONE `UNION ALL` statement — still a single
        `execute()` call regardless of how many system_sample fields
        exist (no per-metric fan-out)."""

        current_commit_clause = ""
        params: list = [flow_name, device_key, mode]
        if current_commit is not None:
            current_commit_clause = "AND r.git_commit <> ?"
            params.append(current_commit)
        params.append(baseline_n)

        per_iter_union = " UNION ALL ".join(
            "SELECT e.run_id, e.git_commit, e.started_at, "
            f"'{field}' AS metric_name, i.idx AS iteration_idx, s.{field} AS value "
            "FROM eligible e "
            "JOIN recent rc ON rc.git_commit = e.git_commit "
            "JOIN iteration i ON i.run_id = e.run_id "
            "JOIN system_sample s ON s.iteration_id = i.iteration_id "
            f"WHERE s.{field} IS NOT NULL"
            for field in _SYSTEM_SAMPLE_METRIC_FIELDS
        )

        rows = self._conn.execute(
            f"""
            WITH eligible AS (
              SELECT r.run_id, r.git_commit, r.started_at
              FROM run r
              JOIN flow f ON f.flow_id = r.flow_id
              JOIN device d ON d.device_id = r.device_id
              WHERE f.name = ? AND d.device_key = ? AND r.mode = ?
                AND COALESCE(r.is_dev_bundle, 0) = 0
                AND r.git_commit IS NOT NULL
                {current_commit_clause}
            ),
            recent AS (
              SELECT git_commit FROM eligible
              GROUP BY git_commit
              ORDER BY MAX(started_at) DESC
              LIMIT ?
            )
            {per_iter_union}
            """,
            params,
        ).fetchall()
        return [
            BaselineSystemSamplePoint(
                run_id=run_id,
                git_commit=git_commit,
                started_at=started_at,
                metric_name=metric_name,
                iteration_idx=iteration_idx,
                value=value,
            )
            for run_id, git_commit, started_at, metric_name, iteration_idx, value in rows
        ]
