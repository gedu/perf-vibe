---
name: python-style
description: "Trigger: writing/formatting Python or setting up lint/type-check in perf-lab-cli (ruff, mypy, typing, docstrings, naming). Keep style consistent, typed, and stdlib-first."
license: Apache-2.0
metadata:
  author: eduardo-graciano
  version: "1.0"
---

## Activation Contract

Load when writing, formatting, or reviewing Python style in the `perf-vibe` repo, or configuring lint/type-check tooling. Complements `perf-cli-standards` (rule 9 dependency policy). Style serves readability and bug-prevention — not decoration.

## Hard Rules

1. **Tooling: `ruff` + `mypy`.** `ruff` for lint AND format (replaces black/flake8/isort); `mypy` for type-check. Config lives in `pyproject.toml` — see `assets/pyproject-tooling.toml` for the drop-in block. Both must pass before merge; wire them into CI when it exists. These are dev-only deps (they do not touch `perf`'s runtime dependency budget).
2. **Type everything public.** Every module starts with `from __future__ import annotations`. Annotate all function params/returns. Ports are `typing.Protocol`; value objects are `@dataclass(frozen=True)`. `mypy` runs with `disallow_untyped_defs` on `src/perf`.
3. **Line length 100.** Wrap at 100 columns (ruff-enforced). Prefer breaking at natural argument boundaries over dense one-liners.
4. **Docstrings explain WHY, not WHAT.** A module docstring states the file's ONE job (and cites the design/spec section it implements) — matching the existing files. Public functions get a short docstring only when the name isn't self-evident; skip noise like `"""Return the value."""`. Never restate the code.
5. **Naming reveals intent.** Full words over abbreviations (`marker_source`, not `ms`). No single-letter names except trivial loop indices. A name should make a comment unnecessary.
6. **Modern idioms.** `pathlib.Path` over `os.path`; f-strings over `%`/`.format`; `enumerate`/comprehensions over index loops; `match` where it clarifies; `contextlib`/`with` for resources. stdlib-first (SKILL rule 9) — `typer`/`rich` are the only sanctioned runtime deps.
7. **Exceptions are specific.** Catch the narrowest exception that fits; no bare `except:`. The deliberate last-resort `except Exception` guards that map to an exit code are the ONLY broad catches, and each carries a `# noqa: BLE001` + a comment saying why (never exit 1).
8. **No dead scaffolding.** Delete commented-out code, unused imports, and TODO stubs before merge; `ruff` flags them.

## Decision Gates

| Situation | Do |
| --- | --- |
| Formatting/import order | `ruff format` + `ruff check --fix` — never hand-format |
| Adding a function | Type its signature; docstring only if the name isn't obvious |
| Handling a filesystem path | `pathlib.Path`, not string concatenation |
| Broad `except` needed | Only for the exit-code safety net; annotate `# noqa: BLE001` + reason |
| Reaching for a new dependency | Justify against SKILL rule 9; default to stdlib |

## Execution Steps (tooling setup)

1. Merge `assets/pyproject-tooling.toml` into the project `pyproject.toml` (`[tool.ruff]`, `[tool.mypy]`, dev deps).
2. `ruff check . && ruff format --check . && mypy src/perf` — fix findings.
3. Add the same three commands as a CI gate / pre-merge check.

## References

- `assets/pyproject-tooling.toml` — drop-in ruff + mypy config for this repo.
- `.claude/skills/perf-cli-standards/SKILL.md` — rule 9 (dependency policy) and the exit-code guard rationale.
