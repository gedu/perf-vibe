"""`perf` CLI package. Exposes `main` so the `perf.cli:main` console script
declared in `pyproject.toml` resolves."""

from __future__ import annotations

from perf.cli.main import main

__all__ = ["main"]
