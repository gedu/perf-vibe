"""Device-free seed for the `perf budget-check` demo (PR-C task 3.22).

Reuses `examples/demo-compare/seed.py`'s `seed_into()` function AND its
recorded fixture files verbatim — the SAME regression story (`checkout`
~800ms baseline -> ~1300ms on `head`, `ttfp`/Flashlight aggregates stable)
replayed into budget-check's OWN local `perf.db` so the two demos never
share mutable state (each can be re-seeded independently).

Run directly: `python examples/demo-budget-check/seed.py` (from anywhere —
every path is resolved relative to THIS file / `demo-compare`'s own file,
never the current working directory).
"""

from __future__ import annotations

import sys
from pathlib import Path

_DEMO_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _DEMO_DIR.parents[1]
_DEMO_COMPARE_DIR = _REPO_ROOT / "examples" / "demo-compare"
if str(_DEMO_COMPARE_DIR) not in sys.path:
    sys.path.insert(0, str(_DEMO_COMPARE_DIR))

from seed import seed_into  # noqa: E402  (examples/demo-compare/seed.py)

from fakes import SequentialClock  # noqa: E402
from perf.adapters.store_sqlite import SqliteStore  # noqa: E402

DEFAULT_DB_PATH = _DEMO_DIR / "perf.db"
DEFAULT_RESULTS_DIR = _DEMO_DIR / ".demo-results"


def seed(db_path: Path = DEFAULT_DB_PATH, results_dir: Path = DEFAULT_RESULTS_DIR) -> None:
    """(Re)creates `db_path` from scratch and seeds it with demo-compare's
    exact 4-baseline-commit + 1-regressing-latest-commit story — enough
    for `perfvibe budget-check demo` to compute a real `fail` gate."""

    if db_path.exists():
        db_path.unlink()

    store = SqliteStore(db_path, clock=SequentialClock())
    try:
        run_ids = seed_into(store, results_dir=results_dir)
    finally:
        store.close()

    print(f"Seeded {len(run_ids)} runs into {db_path} (flow='demo').")
    print("`checkout` regresses on the latest commit -> `budget-check` gate FAILS, exit 1.")


if __name__ == "__main__":
    seed()
