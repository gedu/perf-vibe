#!/usr/bin/env python3
"""Run perfvibe from a source checkout WITHOUT installing.

    python perfvibe-cli.py --help
    python perfvibe-cli.py --config examples/demo-run/perf.toml run demo

Puts `src/` on the import path so the `perf` package resolves even when the
project has not been `pip install`-ed. For a normal install use the `perfvibe`
console script instead (see the README).
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))

from perf.cli import main  # noqa: E402 — path setup must precede the import

if __name__ == "__main__":
    main()
