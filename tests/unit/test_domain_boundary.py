"""Hexagonal boundary guard (spec: "Hexagonal Boundary Enforcement").

RED before adapters exist (task 2.1): asserts `domain/` imports zero
`adapters/` modules via static AST analysis — no adapter needs to exist for
this test to be meaningful, since it inspects import statements, not runtime
behavior. It stays valid (and must keep passing) once PR2 adds `adapters/`.
"""

from __future__ import annotations

import ast
from pathlib import Path

DOMAIN_DIR = Path(__file__).resolve().parents[2] / "src" / "perf" / "domain"


def _imported_module_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_domain_has_no_adapter_imports():
    domain_files = sorted(DOMAIN_DIR.glob("*.py"))
    assert domain_files, "expected domain/*.py modules to exist"

    offenders: dict[str, set[str]] = {}
    for path in domain_files:
        imported = _imported_module_names(path.read_text())
        adapter_imports = {name for name in imported if "adapters" in name}
        if adapter_imports:
            offenders[str(path)] = adapter_imports

    assert not offenders, f"domain/ modules importing adapters/: {offenders}"


def test_domain_package_has_no_io_stdlib_imports():
    """Domain modules must perform no I/O — a light guard against the most
    common accidental leaks (subprocess, socket, sqlite3, open()-adjacent
    modules)."""
    disallowed = {"subprocess", "socket", "sqlite3", "os.path", "shutil"}
    domain_files = sorted(DOMAIN_DIR.glob("*.py"))

    offenders: dict[str, set[str]] = {}
    for path in domain_files:
        imported = _imported_module_names(path.read_text())
        hit = imported & disallowed
        if hit:
            offenders[str(path)] = hit

    assert not offenders, f"domain/ modules importing I/O stdlib: {offenders}"
