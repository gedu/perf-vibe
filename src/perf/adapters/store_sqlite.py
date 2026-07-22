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
    output. History/compare read models are NOT this PR.

SQL-injection safety (SKILL rule 4): every value (device_key, flow name,
metric/marker name, paths, metadata) is bound via `?` placeholders. SQL
identifiers (table/column names) are static literals in this file only;
no identifier is ever built from a `?`-bound value. The one apparent
exception — `PRAGMA user_version = <int>`, which SQLite does not allow to
be parameterized — interpolates an integer that was already validated as
digits-only by `_parse_migration_version` before use, never raw text.
"""

from __future__ import annotations

import sqlite3
from dataclasses import fields as dc_fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence, Union

from perf.domain.model import Marker, RunContext, SystemSample, default_higher_is_better
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
    def _upsert_metric(conn: sqlite3.Connection, name: str, higher_is_better: bool) -> int:
        conn.execute(
            """
            INSERT INTO metric (name, higher_is_better)
            VALUES (?, ?)
            ON CONFLICT(name) DO NOTHING
            """,
            (name, int(higher_is_better)),
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
                    conn, marker.name, default_higher_is_better(marker.name)
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
