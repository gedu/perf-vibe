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
    """Collect every module/name an import statement pulls in.

    Covers the evasive forms a naive `node.module`-only scan misses:
    `from . import adapters` (relative, ``module`` is ``None``),
    `from perf import adapters` (``module`` lacks the offending substring),
    and `from ..adapters import X`. For ``ImportFrom`` we emit the module,
    plus each imported name qualified by the module (or bare when relative),
    so a substring match on ``adapters`` catches the package itself.
    """
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
                names.add(
                    f"{node.module}.{alias.name}" if node.module else alias.name
                )
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


def test_boundary_detector_catches_evasive_adapter_imports():
    """The guard must fail on every import form that reaches `adapters/`, not
    just `import perf.adapters`. Proves the detector's teeth against the three
    forms a naive scan would miss."""
    evasive = [
        "from . import adapters",
        "from .. import adapters",
        "from perf import adapters",
        "from perf.adapters import store_sqlite",
        "from ..adapters import store_sqlite",
        "import perf.adapters.store_sqlite",
    ]
    for source in evasive:
        imported = _imported_module_names(source)
        assert any("adapters" in name for name in imported), (
            f"detector failed to flag adapter import: {source!r} -> {imported}"
        )


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
