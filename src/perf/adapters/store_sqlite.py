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

`reassure-ingest` PR3 additionally owns `save_reassure_import` — the
one-transaction ingest of a parsed reassure `.perf` file into the four
additive `reassure_*` tables (PR1's `0005_add_reassure_tables.sql`).
`durations[]` and `counts[]` are NOT index-aligned (design "Load-Bearing
Invariant"), so they are persisted via TWO INDEPENDENT insert loops into
their own sibling sample tables — never one zipped loop. Duplicate
detection is `cur.rowcount == 0` after `ON CONFLICT(content_hash) DO
NOTHING`, returning `None`; ANY failure rolls back the whole import,
leaving zero rows in all four tables.

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
from collections.abc import Sequence
from dataclasses import fields as dc_fields
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import NamedTuple

from perf.domain import statistics
from perf.domain.model import (
    HistoryMetric,
    HistoryRun,
    Marker,
    ReassureEntry,
    ReassureParseResult,
    RunContext,
    RunPoint,
    SystemSample,
    default_higher_is_better,
)
from perf.domain.ports import Clock

# Resolved __file__-relative to THIS package's own db/ directory — never a
# user-supplied path. `adapters/store_sqlite.py` -> parent is `adapters/`,
# parent.parent is the `perf` package root.
_PACKAGE_DB_DIR = Path(__file__).resolve().parent.parent / "db"
_MIGRATIONS_DIR = _PACKAGE_DB_DIR / "migrations"

# `reassure_import.kind` (`0006_add_reassure_import_kind.sql`) carries NO
# `CHECK` constraint by design (house style: matches `run.mode`/
# `reassure_entry.entry_type`) — validation lives here, at the adapter
# boundary, instead.
_VALID_REASSURE_KINDS = frozenset({"current", "baseline", "unknown"})

# The `system_sample` aggregate field names (excluding the join key) —
# derived from the domain model, not hardcoded twice, so the "metric"
# dimension direction-metadata upsert (spec: "Direction-Aware Metric
# Metadata") tracks `SystemSample` if it ever grows a field.
_SYSTEM_SAMPLE_METRIC_FIELDS: tuple[str, ...] = tuple(
    f.name for f in dc_fields(SystemSample) if f.name != "iteration_idx"
)

# Correct per-unit metadata for the closed set of `system_sample` aggregate
# fields — the SAME mapping `adapters/analyzer_sql._SYSTEM_SAMPLE_UNITS` uses.
# Ingestion (`_upsert_metrics`) defaults these metrics to 'ms' in the `metric`
# table, so the read side must supply the true unit itself (analyzer does the
# same for `compare`; `history` does it here for its export). Kept a local
# literal rather than imported from `analyzer_sql` — that module imports THIS
# one, so importing back would be a cycle.
_SYSTEM_SAMPLE_UNITS: dict[str, str] = {
    "total_time_ms": "ms",
    "start_time_ms": "ms",
    "fps_avg": "fps",
    "fps_min": "fps",
    "ram_avg_mb": "mb",
    "ram_peak_mb": "mb",
    "cpu_avg_pct": "pct",
    "cpu_peak_pct": "pct",
}

# The p90 percentile `history` reports for the system_sample family — the
# SAME `_PERCENTILE` the analyzer applies (ceil nearest-rank, matching the
# `run_metric_summary` view's p90 for the measure family).
_HISTORY_P90 = 90.0


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
    git_commit: str | None
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
        return datetime.now(UTC).isoformat()


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
        db_path: str | Path,
        *,
        clock: Clock | None = None,
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

    def __enter__(self) -> SqliteStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
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
            (_parse_migration_version(path.name), path) for path in _MIGRATIONS_DIR.glob("*.sql")
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
        raw_report_path: str | None,
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

    # ----- reassure ingest transaction (PR3, design "Transaction Boundary")
    # -----
    #
    # Follows `save_run`'s shape exactly: literal `BEGIN`, private insert
    # helpers, `COMMIT`, `except Exception: ROLLBACK; raise`. `durations`
    # and `counts` are NOT index-aligned (design "Load-Bearing Invariant"),
    # so they are persisted via TWO INDEPENDENT loops into their own sample
    # tables — never one zipped loop.

    def save_reassure_import(
        self, result: ReassureParseResult, source_path: str, kind: str
    ) -> int | None:
        # Validated HERE, at the adapter boundary, before `BEGIN` — the
        # `reassure_import.kind` column deliberately carries no `CHECK`
        # constraint (house style: matches `run.mode`/
        # `reassure_entry.entry_type`), so this is the ONE place a bad value
        # is rejected, and it is rejected before any row is written (not a
        # rolled-back partial write).
        if kind not in _VALID_REASSURE_KINDS:
            raise ValueError(
                f"invalid reassure import kind {kind!r}; "
                f"must be one of {sorted(_VALID_REASSURE_KINDS)!r}"
            )
        conn = self._conn
        conn.execute("BEGIN")
        try:
            import_id = self._insert_reassure_import(conn, result, source_path, kind)
            if import_id is None:
                # Byte-identical re-import: `rowcount == 0` after
                # `ON CONFLICT(content_hash) DO NOTHING` — zero rows
                # written anywhere, `COMMIT` closes the (empty) transaction.
                conn.execute("COMMIT")
                return None

            for entry in result.entries:
                entry_id = self._insert_reassure_entry(conn, import_id, entry)
                # TWO independent loops over TWO independent series — never
                # one zipped loop (design "Load-Bearing Invariant").
                self._insert_reassure_duration_samples(conn, entry_id, entry.durations)
                self._insert_reassure_count_samples(conn, entry_id, entry.counts)

            conn.execute("COMMIT")
            return import_id
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def _insert_reassure_import(
        self, conn: sqlite3.Connection, result: ReassureParseResult, source_path: str, kind: str
    ) -> int | None:
        header = result.header
        branch = header.branch if header is not None else None
        commit_hash = header.commit_hash if header is not None else None
        created_date = header.created_date if header is not None else None

        cur = conn.execute(
            """
            INSERT INTO reassure_import (
                content_hash, imported_at, source_path, branch, commit_hash, created_date, kind
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(content_hash) DO NOTHING
            """,
            (
                result.content_hash,
                self._clock.now_utc_iso(),
                source_path,
                branch,
                commit_hash,
                created_date,
                kind,
            ),
        )
        # Duplicate detection is `rowcount == 0` (design "Duplicate
        # detection"): a pre-`SELECT` is a second round trip and a TOCTOU
        # window inside this same transaction; `lastrowid` retains a STALE
        # value after a no-op insert, so reading it here would report a
        # bogus `import_id`.
        if cur.rowcount == 0:
            return None

        import_id = cur.lastrowid
        if import_id is None:
            raise RuntimeError(
                "INSERT INTO reassure_import did not return a row id (lastrowid is None)"
            )
        return import_id

    @staticmethod
    def _insert_reassure_entry(
        conn: sqlite3.Connection, import_id: int, entry: ReassureEntry
    ) -> int:
        cur = conn.execute(
            """
            INSERT INTO reassure_entry (
                import_id, name, entry_type, runs, warmup_durations, outlier_durations,
                issues_initial_update_count, issues_redundant_updates
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                import_id,
                entry.name,
                entry.entry_type,
                entry.runs,
                entry.warmup_durations_json,
                entry.outlier_durations_json,
                # `None` binds to SQL NULL, which MEANS "the file carried no
                # `issues` object" — never coerced to 0 or '[]', because a
                # present zero and a present empty array are different facts
                # (`0007_add_reassure_entry_issues.sql`).
                entry.initial_update_count,
                entry.redundant_updates_json,
            ),
        )
        entry_id = cur.lastrowid
        if entry_id is None:
            raise RuntimeError(
                "INSERT INTO reassure_entry did not return a row id (lastrowid is None)"
            )
        return entry_id

    @staticmethod
    def _insert_reassure_duration_samples(
        conn: sqlite3.Connection, entry_id: int, durations: Sequence[float]
    ) -> None:
        for idx, duration in enumerate(durations):
            conn.execute(
                "INSERT INTO reassure_duration_sample (entry_id, idx, duration_ms) "
                "VALUES (?, ?, ?)",
                (entry_id, idx, duration),
            )

    @staticmethod
    def _insert_reassure_count_samples(
        conn: sqlite3.Connection, entry_id: int, counts: Sequence[float]
    ) -> None:
        for idx, count in enumerate(counts):
            conn.execute(
                "INSERT INTO reassure_count_sample (entry_id, idx, render_count) VALUES (?, ?, ?)",
                (entry_id, idx, count),
            )

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
                # Persist the CORRECT unit for system-sample metrics going
                # forward (resilience batch, Task 5): fps_avg/fps_min -> 'fps',
                # ram_* -> 'mb', cpu_* -> 'pct' — not the blanket 'ms' this
                # loop used to write, which any external DB reader then saw for
                # fps. Marker metrics keep their parsed unit above; first-write-
                # wins as today (ON CONFLICT DO NOTHING). Migration 0004 fixes
                # rows already written with the wrong unit.
                metric_ids[name] = self._upsert_metric(
                    conn,
                    name,
                    default_higher_is_better(name),
                    unit=_SYSTEM_SAMPLE_UNITS.get(name, "ms"),
                )
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
        raw_report_path: str | None,
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
        # `sqlite3.Cursor.lastrowid` is typed `int | None` in the stubs
        # (it is `None` for statements that are not INSERT). This IS an
        # INSERT, so `None` here means the driver failed to report a row id
        # — an unexpected runtime/tooling failure (SKILL rule 7 exit 3), not
        # a value to silently coerce away.
        run_id = cur.lastrowid
        if run_id is None:
            raise RuntimeError("INSERT INTO run did not return a row id (lastrowid is None)")
        return run_id

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

    def get_run_summary(self, run_id: int) -> dict | None:
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

    def latest_device_key(self, flow_name: str, mode: str) -> str | None:
        """The `device_key` of the most recently persisted run for this
        flow+mode, regardless of device (resilience batch, Task 2). Lets
        `compare`/`budget-check` fall back to the LAST recorded device when
        the live-derived key (which degrades to `unknown|unknown|physical`
        with no device attached) matches no history — so the CI gate does
        not die with exit 2 merely because no device is plugged in. `None`
        when the flow+mode has no runs at all. Every value is `?`-bound and
        all identifiers are static literals (SKILL rule 4)."""

        row = self._conn.execute(
            """
            SELECT d.device_key
            FROM run r
            JOIN flow f ON f.flow_id = r.flow_id
            JOIN device d ON d.device_id = r.device_id
            WHERE f.name = ? AND r.mode = ?
            ORDER BY r.started_at DESC, r.run_id DESC
            LIMIT 1
            """,
            (flow_name, mode),
        ).fetchone()
        return row[0] if row is not None else None

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

    def history_runs(
        self, flow_name: str, device_key: str, mode: str, limit: int
    ) -> Sequence[HistoryRun]:
        """The `history` command's per-flow read model (the charting-export
        seam): the most recent `limit` persisted runs for
        `flow+device_key+mode`, returned OLDEST→NEWEST (natural chart order),
        each carrying every metric's per-run {p50, p90, n, unit} summary
        across BOTH metric families.

        Measure-family metrics are summarized by the `run_metric_summary`
        view (p50/p90 already computed in SQL — median for p50, ceil
        nearest-rank for p90); system_sample aggregates are reduced from
        their raw per-iteration rows via `domain/statistics` with the SAME
        conventions, so the two families agree. Deliberately a RAW per-run
        summary — NO warm-up discard (unlike `compare`'s baseline math) — so
        it reflects exactly what each run recorded. system_sample units come
        from `_SYSTEM_SAMPLE_UNITS` (ingestion defaults them to 'ms').

        Every value is `?`-bound; the only interpolation is `?`-placeholder
        text for the `IN (...)` window and the FIXED
        `_SYSTEM_SAMPLE_METRIC_FIELDS` identifiers — never a bound value
        (SKILL rule 4)."""

        window = self._conn.execute(
            """
            SELECT r.run_id, r.started_at, r.git_commit, r.source
            FROM run r
            JOIN flow f ON f.flow_id = r.flow_id
            JOIN device d ON d.device_id = r.device_id
            WHERE f.name = ? AND d.device_key = ? AND r.mode = ?
            ORDER BY r.started_at DESC, r.run_id DESC
            LIMIT ?
            """,
            (flow_name, device_key, mode, limit),
        ).fetchall()
        if not window:
            return ()

        # The query fetched the most-recent window DESC (so LIMIT keeps the
        # latest N); reverse to oldest→newest for the natural series order.
        ordered_window = list(reversed(window))
        run_ids = [row[0] for row in ordered_window]

        measures_by_run = self._history_measure_summaries(run_ids)
        system_by_run = self._history_system_summaries(run_ids)

        runs: list[HistoryRun] = []
        for run_id, started_at, git_commit, source in ordered_window:
            metrics = [*measures_by_run.get(run_id, ()), *system_by_run.get(run_id, ())]
            metrics.sort(key=lambda metric: metric.metric_name)
            runs.append(
                HistoryRun(
                    run_id=run_id,
                    started_at=started_at,
                    git_commit=git_commit,
                    source=source,
                    metrics=tuple(metrics),
                )
            )
        return tuple(runs)

    def _history_measure_summaries(self, run_ids: Sequence[int]) -> dict[int, list[HistoryMetric]]:
        """Measure-family per-run summaries for the whole window in ONE
        query — the `run_metric_summary` view already carries p50/p90/n; the
        unit is the metric's own (measures thread it at ingestion). The
        `?`-placeholder string for the `IN (...)` list is NOT a bound value
        (SKILL rule 4) — every actual value stays `?`-bound."""

        placeholders = ",".join("?" for _ in run_ids)
        rows = self._conn.execute(
            f"""
            SELECT s.run_id, m.name, m.unit, s.p50_ms, s.p90_ms, s.n
            FROM run_metric_summary s
            JOIN metric m ON m.metric_id = s.metric_id
            WHERE s.run_id IN ({placeholders})
            """,
            tuple(run_ids),
        ).fetchall()

        by_run: dict[int, list[HistoryMetric]] = {}
        for run_id, name, unit, p50, p90, n in rows:
            by_run.setdefault(run_id, []).append(
                HistoryMetric(metric_name=name, p50=p50, p90=p90, n=n, unit=unit)
            )
        return by_run

    def _history_system_summaries(self, run_ids: Sequence[int]) -> dict[int, list[HistoryMetric]]:
        """system_sample-family per-run summaries for the whole window in
        ONE `UNION ALL` query — raw per-iteration values batched across every
        field, then reduced per (run, metric) with `domain/statistics`
        (median p50, ceil-nearest-rank p90). Identifiers are the FIXED
        `_SYSTEM_SAMPLE_METRIC_FIELDS`; the `?`-placeholder `IN (...)` text is
        not a bound value (SKILL rule 4)."""

        placeholders = ",".join("?" for _ in run_ids)
        union_sql = " UNION ALL ".join(
            f"SELECT i.run_id AS run_id, '{field}' AS metric_name, s.{field} AS value "
            "FROM iteration i JOIN system_sample s ON s.iteration_id = i.iteration_id "
            f"WHERE i.run_id IN ({placeholders}) AND s.{field} IS NOT NULL"
            for field in _SYSTEM_SAMPLE_METRIC_FIELDS
        )
        params = tuple(run_ids) * len(_SYSTEM_SAMPLE_METRIC_FIELDS)
        rows = self._conn.execute(union_sql, params).fetchall()

        grouped: dict[tuple[int, str], list[float]] = {}
        for run_id, metric_name, value in rows:
            grouped.setdefault((run_id, metric_name), []).append(value)

        by_run: dict[int, list[HistoryMetric]] = {}
        for (run_id, metric_name), values in grouped.items():
            by_run.setdefault(run_id, []).append(
                HistoryMetric(
                    metric_name=metric_name,
                    p50=statistics.median(values),
                    p90=statistics.percentile(values, _HISTORY_P90),
                    n=len(values),
                    unit=_SYSTEM_SAMPLE_UNITS.get(metric_name, "ms"),
                )
            )
        return by_run

    def latest_run(self, flow_name: str, device_key: str, mode: str) -> LatestRun | None:
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

    def count_baseline_exclusions(
        self,
        flow_name: str,
        device_key: str,
        mode: str,
        current_commit: str | None,
    ) -> tuple[int, int]:
        """Diagnostic counts (anti-false-positive batch, Task 4) for the runs
        the baseline query SILENTLY drops for this flow/device/mode: returns
        `(runs on the current commit, runs with no git commit)`. Pretty output
        surfaces these so a dev iterating on ONE sha understands why history
        looks thin; the `--json` contract never sees them. Every value is
        `?`-bound; all identifiers are static literals (SKILL rule 4). The
        current-commit count is `0` when there is no resolvable `current_commit`
        (the baseline query adds no same-commit exclusion in that case)."""

        same_commit = 0
        if current_commit is not None:
            same_commit = self._conn.execute(
                """
                SELECT COUNT(*) FROM run r
                JOIN flow f ON f.flow_id = r.flow_id
                JOIN device d ON d.device_id = r.device_id
                WHERE f.name = ? AND d.device_key = ? AND r.mode = ? AND r.git_commit = ?
                """,
                (flow_name, device_key, mode, current_commit),
            ).fetchone()[0]

        no_commit = self._conn.execute(
            """
            SELECT COUNT(*) FROM run r
            JOIN flow f ON f.flow_id = r.flow_id
            JOIN device d ON d.device_id = r.device_id
            WHERE f.name = ? AND d.device_key = ? AND r.mode = ? AND r.git_commit IS NULL
            """,
            (flow_name, device_key, mode),
        ).fetchone()[0]
        return same_commit, no_commit

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
        current_commit: str | None,
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

        The `s.p90_ms IS NOT NULL` filter mirrors
        `baseline_system_sample_points`'s `s.{field} IS NOT NULL` guard, so a
        genuinely NULL percentile never leaks a `None` into
        `median_by_commit`. NOTE: since the p90 CEIL nearest-rank fix
        (0003_fix_p90_ceil_rank.sql) an n=1 run's p90 is its single value
        (rank ceil(0.9)=1), NOT NULL — so n=1 runs now CONTRIBUTE a baseline
        point; the filter remains as defensive backstop for any other
        NULL-percentile edge."""

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
        current_commit: str | None,
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
