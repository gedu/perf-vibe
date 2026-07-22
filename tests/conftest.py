"""Root test configuration. Adds `tests/` to `sys.path` so any test module
(regardless of subdirectory) can `import fakes` for the shared port
doubles (SKILL rule 8), and registers `--update-golden` for the golden
output tests (SKILL rule 8: "`--update-golden` regenerates")."""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


def pytest_addoption(parser):
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Regenerate golden fixture files instead of asserting against them.",
    )
