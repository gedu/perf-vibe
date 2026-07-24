# Archive: `init` capability (CLI scaffolding slice)

**Status**: SHIPPED AND ARCHIVED

## Summary

The `init` capability was designed, implemented, verified, and merged across three chained PRs (PR-A fixture tree + `init_v1` contract, PR-B pure helpers + command body, PR-C integration tests + wizard + golden output + docs). This marker indicates the change is no longer active in `openspec/changes/init-command/` and has been consolidated into the canonical spec.

## Delivery

| Item | Status | Ref |
|---|---|---|
| Specification | SHIPPED ✓ | `openspec/specs/init-command.md` |
| Implementation | MERGED ✓ | Three chained PRs to main (PR-A, PR-B, PR-C) |
| Verification | PASS ✓ | `docs/specs/init-command/verify-report.md` — 0 CRITICAL, 1 WARNING, 1 SUGGESTION |
| Test Suite | 506 passing ✓ | Full `pytest` suite (includes 49/49 new init-command tests) |
| Linting & Type Check | CLEAN ✓ | `ruff check .`, `ruff format --check .`, `mypy src/perf` all green |
| Coverage | 94.53% ✓ | Floor 93%; `init.py` 89%, `contracts/init_v1.py` 100% |
| Layering (CLI-only) | VERIFIED ✓ | Zero `domain/`/`application/` imports in `init.py` |
| Exit-code discipline | VERIFIED ✓ | `init.py` never emits `1`; only `0`/`2`/`3` |

## Historical Record

Complete SDD artifacts for init-command remain in:
- `docs/specs/init-command/` — full SDD record (proposal, spec, design, tasks, verify-report)

## Mid-Flow Decisions (resolved after proposal) — implemented and verified

1. **`--driver`/`--db` verbatim pass-through** — written as literal top-level TOML keys only when supplied; no detection logic.
2. **Output path reuses `--config`**, no new `--out` flag — defaults to `./perf.toml` in CWD when `--config` is omitted.
3. **Comment-loss requires confirmation/`--force`** — interactive mode prompts, non-interactive mode exits `2` unless `--force` is passed.

## Corner-Case Coverage (I1–I17) — all tested and passing

Every discovery, parsing, merge, and round-trip corner case has real, passing test coverage (49 total new tests across unit/integration/golden/contract):

- I1–I2: Zero flows / nonexistent dir → exit `2`
- I3–I5: appId detection and reconciliation (single, zero, missing/malformed)
- I6–I8: Mismatch handling (interactive prompt, non-interactive with/without `--bundle-id`)
- I9–I12: perf.toml creation and merge (fresh create, new names, collision without/with `--force`)
- I13–I15: Interactive vs non-interactive auto-detection (TTY, `--yes`, non-TTY)
- I16: Round-trip guarantee (output parses via `load_config`, flows config-known)
- I17: Unexpected I/O failure → exit `3`

Exit-code invariant: `init` never exits `1` across any scenario.

## Non-Critical Findings (from verify report)

1. **WARNING**: design.md's "Typer signature" prose line describes `--out` and default-dependent `--driver` — both superseded by the three resolved decisions above. The implementation follows tasks.md and actual resolved decisions. No functional impact; internal doc inconsistency only.

2. **SUGGESTION**: spec.md's I10 scenario says `[flows.login]` is left "byte-for-byte unmodified" — design deliberately chose full canonical re-serialize to enable collision detection. Values ARE preserved (the real guarantee); wording could soften to "semantically unmodified" to match design intent.

Both findings are documentation-consistency notes, safe to address in future housekeeping. None block archive.

## Ruff/Mypy Integration

**ruff** and **mypy** are both ACTIVE and CLEAN across the implementation:
- `ruff check .` and `ruff format --check .` pass
- `mypy src/perf` (with existing project config) passes
- No violations introduced

## Architecture and Design Summary

- **Scope**: Pure CLI-adjacent scaffolding — discovery, parsing, validation, TOML write/merge
- **Layering**: CLI-only module; no domain/application imports; no schema changes; fully additive
- **Dependency discipline**: stdlib-first; uses only existing `typer` and `rich` (sanctioned); no new runtime deps
- **Contract**: New `init_v1` with `schema_version=1`; independent from `compare_v1`/`budget_check_v1`
- **Rollback**: Purely additive (one command module + one contract module + one registration line); revert branch to undo
- **Safety**: NEVER exits `1`; NEVER writes partial/corrupt `perf.toml` on error; all errors handled explicitly with proper exit codes

---

**Archived**: 2026-07-24
