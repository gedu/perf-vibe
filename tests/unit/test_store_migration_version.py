"""Pure unit test for the migration filename-version parser (§9.5).

No I/O — guards the SQL-injection-adjacent invariant that the numeric
version prefix (later string-formatted into `PRAGMA user_version = N`,
since PRAGMA does not accept bound parameters) is validated as digits-only
BEFORE it is ever interpolated, never trusted as arbitrary text.
"""

from __future__ import annotations

import pytest

from perf.adapters.store_sqlite import _parse_migration_version


def test_parse_migration_version_accepts_numeric_prefix():
    assert _parse_migration_version("0001_init.sql") == 1
    assert _parse_migration_version("0002_add_column.sql") == 2


def test_parse_migration_version_rejects_non_numeric_prefix():
    with pytest.raises(ValueError):
        _parse_migration_version("bad_migration.sql")


def test_parse_migration_version_rejects_injection_attempt_in_filename():
    with pytest.raises(ValueError):
        _parse_migration_version("1;DROP TABLE run;--_evil.sql")
