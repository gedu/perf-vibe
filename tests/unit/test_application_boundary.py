"""Hexagonal boundary guard for `application/` (PR3 extension of the
`domain/` guard in `test_domain_boundary.py` — SKILL rule 1: "application/
use-cases orchestrate ports only — no I/O of their own... A domain/ or
application/ module importing an adapter is a blocking violation.").
"""

from __future__ import annotations

import ast
from pathlib import Path

APPLICATION_DIR = Path(__file__).resolve().parents[2] / "src" / "perf" / "application"


def _imported_module_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
            for alias in node.names:
                names.add(f"{node.module}.{alias.name}" if node.module else alias.name)
    return names


def test_application_has_no_adapter_imports():
    app_files = sorted(APPLICATION_DIR.glob("*.py"))
    assert app_files, "expected application/*.py modules to exist"

    offenders: dict[str, set[str]] = {}
    for path in app_files:
        imported = _imported_module_names(path.read_text())
        adapter_imports = {name for name in imported if "adapters" in name}
        if adapter_imports:
            offenders[str(path)] = adapter_imports

    assert not offenders, f"application/ modules importing adapters/: {offenders}"


def test_application_package_has_no_io_stdlib_imports():
    disallowed = {"subprocess", "socket", "sqlite3", "shutil"}
    app_files = sorted(APPLICATION_DIR.glob("*.py"))

    offenders: dict[str, set[str]] = {}
    for path in app_files:
        imported = _imported_module_names(path.read_text())
        hit = imported & disallowed
        if hit:
            offenders[str(path)] = hit

    assert not offenders, f"application/ modules importing I/O stdlib: {offenders}"
