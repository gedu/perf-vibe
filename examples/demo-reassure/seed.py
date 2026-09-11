"""Device-free seed for the `reassure` demo.

Imports four recorded `.perf` fixtures through the REAL path `perfvibe
reassure import` uses — `build_reassure_parser().parse()` then
`Store.save_reassure_import()` (`cli/commands/reassure_import.py:119,127`)
— against a REAL `SqliteStore`. No device, no `adb`, no Node, and no
`@callstack/reassure` install: reassure data comes from a JavaScript test
run, so a recorded `.perf` file is the whole input.

Produces four imports of the same two tests:

  - 3 baseline imports (`c0000001`..`c0000003`, Jan-Mar) where
    `CartPanel renders` sits at ~100ms with one render and a clean mount
    diagnostic, and `Header renders` sits at ~20ms.
  - 1 latest import (`c9999999`, Apr) where `CartPanel renders` regresses
    on BOTH series — ~100ms to ~140ms and one render to two — and gains an
    extra render on mount (`initialUpdateCount` 0 -> 1), while
    `Header renders` stays exactly where it was.

Three baselines is the minimum: `MIN_BASELINE_IMPORTS` is 3
(`domain/reassure_compare.py`), and below it every verdict reports
`insufficient-data` rather than a silent `stable`. So this is the smallest
history that can produce a real verdict at all.

`Header renders` is here to prove the signal discriminates. A demo where
everything regresses cannot show the difference between a finding and
noise.

Re-runnable: the database is recreated on each run, and reassure imports
are idempotent by content hash anyway, so a second run of the same bytes
would be a no-op regardless.

Run directly: `python examples/demo-reassure/seed.py` (from anywhere —
every path resolves relative to THIS file, never the current working
directory).
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent

# Importable without installing the package, matching the other demos.
sys.path.insert(0, str(_REPO_ROOT / "src"))

from perf.adapters.registry import build_reassure_parser, build_store  # noqa: E402

_DB_PATH = _HERE / "perfvibe.db"

# Ordered oldest first. `kind` records WHICH FILE the bytes came from and is
# write-only provenance — it never drives a query, a filter or a comparison
# (`db/migrations/0006_add_reassure_import_kind.sql`). These are all
# `current.perf`-shaped captures, so they all carry `current`.
_FIXTURES = (
    "baseline-1.perf",
    "baseline-2.perf",
    "baseline-3.perf",
    "regression.perf",
)


def seed_into(db_path: Path) -> list[int]:
    """Import every fixture into `db_path`, returning the assigned import ids
    in order. Separated from `main()` so an integration test can reuse the
    exact seeding this demo performs, the way
    `tests/integration/test_cli_compare_replay.py` reuses `demo-compare`'s."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)

    parser = build_reassure_parser()
    store = build_store(str(db_path))
    import_ids: list[int] = []
    try:
        for name in _FIXTURES:
            path = _HERE / "fixtures" / name
            result = parser.parse(str(path))
            import_id = store.save_reassure_import(result, str(path), "current")
            import_ids.append(import_id if import_id is not None else -1)
    finally:
        store.close()
    return import_ids


def main() -> None:
    ids = seed_into(_DB_PATH)
    print(f"seeded {len(ids)} reassure imports into {_DB_PATH.relative_to(_REPO_ROOT)}")
    print("  3 baselines (Jan-Mar) + 1 regressed latest (Apr)")
    print()
    print("Now try, from the repo root:")
    print("  perfvibe --config examples/demo-reassure/perfvibe.toml reassure list")
    print(
        "  perfvibe --config examples/demo-reassure/perfvibe.toml "
        'reassure compare "CartPanel renders"'
    )


if __name__ == "__main__":
    main()
