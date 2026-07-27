# Tasks: `init --prune-missing` (stale-flow reconciliation)

Grounded in spec `docs/specs/prune-missing/spec.md`, design `docs/specs/prune-missing/design.md`.
Scope: `src/perf/cli/commands/init.py` + `src/perf/contracts/init_v1.py` only — additive,
opt-in; plain `init` (no `--prune-missing`) stays byte-identical to today, including its 4
existing golden fixtures.

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | ~380–470 (prod ~150–180, contract test ~20–30, unit tests ~40–50, integration tests ~120–150, golden ~20–30, README ~10–15) |
| 400-line budget risk | Medium (near the classic default threshold) |
| 800-line budget risk | Low (session-cached budget for this change is 800 lines) |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | single-pr-default (cached) |
| Chain strategy | pending — not needed, estimate fits the 800-line budget |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Medium

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| 1 | `compute_pruned_flows` + `merge_config(prune=…)` + v2 contract, in one slice with their unit/contract tests | Single PR (contract layer) | `pytest tests/unit/test_init_merge.py tests/contract/test_init_v1_contract.py -q` | N/A — pure helpers, not yet wired to the CLI | Revert `compute_pruned_flows`, `merge_config`'s `prune` param, `init_v1.py`'s `flows_pruned`/`SCHEMA_VERSION=2`, and their tests; plain `init` untouched |
| 2 | `--prune-missing` flag, confirm-gate control flow, render helpers, payload wiring — SAME PR, includes command-level integration tests for these same lines (CI's 93% floor is per-PR; PR-B's coverage miss on `init-command` must not repeat) | Single PR (command layer) | `pytest tests/integration/test_cli_init.py tests/golden/test_init_pretty_golden.py -q` | `perfvibe init tests/fixtures/flows --prune-missing --yes` against a `tmp_path` `perf.toml` with a stale `[flows.*]` entry — device-free, real fs | Revert the `--prune-missing` option, the prune gate block in `init()`, `_render_prune_confirm_prompt`/`_render_prune_preview`, and their integration/golden tests; plain `init` behavior and the 4 existing golden fixtures are unaffected |

> Both units land in ONE PR per the size estimate — split above is for review-reading order
> only, not separate commits/branches. Per the project instructions: whatever slice adds
> `init.py`'s prune-gate control flow MUST include its own integration-test coverage in the
> SAME commit range — never defer command-level coverage to a later slice (this is exactly
> the gap `init-command`'s PR-B hit against the 93% floor).

## Phase 1: Foundation — pure helper + contract (v2)

- [x] 1.1 RED: `tests/unit/test_init_merge.py` — `compute_pruned_flows(existing, new_flows)`: some-missing (sorted names), none-missing (`[]`), all-missing, empty `existing["flows"]`
- [x] 1.2 GREEN: `src/perf/cli/commands/init.py` — `compute_pruned_flows(existing: Mapping[str, object], new_flows: Mapping[str, Path]) -> list[str]` — sorted `existing_flow_names - discovered_names`, pure, no I/O
- [x] 1.3 RED: `tests/unit/test_init_merge.py` (extend) — `merge_config(..., prune: bool)`: `prune=False` (default) identical output to today (existing tests unchanged, still pass); `prune=True` drops ONLY names in `compute_pruned_flows`'s result, leaves colliding/force logic untouched
- [x] 1.4 GREEN: `init.py` — add `prune: bool = False` param to `merge_config`, reuse `compute_pruned_flows` internally to drop missing entries before merging in `new_flows`
- [x] 1.5 RED: `tests/contract/test_init_v1_contract.py` — `test_schema_version_is_2`; `flows_pruned: list` in `_REQUIRED_KEYS_AND_TYPES`; `_sample_payload()` passes `flows_pruned=["stale"]`; round-trip/shape-drift guard extended
- [x] 1.6 GREEN: `src/perf/contracts/init_v1.py` — `SCHEMA_VERSION = 2`; `build_init_payload(..., flows_pruned: Sequence[str] = ())` → `"flows_pruned": list(flows_pruned)`, no other key changed

## Phase 2: Command wiring — flag, confirm-gate, render helpers

- [x] 2.1 `init.py` — add `prune_missing: bool = typer.Option(False, "--prune-missing", ...)` Typer option, composes orthogonally with `--force`/`--yes`/`--bundle-id`/`--driver`/`--db`
- [x] 2.2 `init.py` — `_render_prune_confirm_prompt(missing: Sequence[str]) -> str` (interactive) and `_render_prune_preview(missing: Sequence[str]) -> str` (non-interactive stderr), mirroring `_render_comment_loss_confirm_prompt`/`_render_comment_loss_error`'s pure-function pattern
- [x] 2.3 `init.py` — `_render_confirmation` gains `flows_pruned: Sequence[str] = ()`; appends a `flows pruned: …` line ONLY when non-empty (conditional-render — protects the 4 existing golden fixtures)
- [x] 2.4 `init.py` — prune gate in `init()`, slotted AFTER the comment-loss guard, BEFORE `merge_config`: empty missing set → `prune=False`, `flows_pruned=[]`, no prompt; interactive → preview + `typer.confirm`, decline → no write, exit `2`; non-interactive + `--yes` → `prune=True`, no prompt; non-interactive, no `--yes` → build the v2 payload (or stderr preview if not `--json`) WITHOUT writing, exit `2`
- [x] 2.5 `init.py` — wire `merge_config(..., prune=prune)` and `build_init_payload(..., flows_pruned=missing if prune else [])`

## Phase 3: Tests — integration + golden (SAME PR as Phase 2, per coverage-floor note above)

- [x] 3.1 RED/GREEN: `tests/integration/test_cli_init.py` — interactive confirm accepted (`CliRunner` stdin input `y`): stale entry removed, exit `0`
- [x] 3.2 interactive confirm declined: `perf.toml` unmodified, exit `2`
- [x] 3.3 non-interactive `--yes`: pruned immediately, no prompt, exit `0`
- [x] 3.4 non-interactive, no `--yes`, `--json`: v2 payload to stdout with `flows_pruned` populated, `perf.toml` NOT written, exit `2`
- [x] 3.5 non-interactive, no `--yes`, no `--json`: would-be-pruned names to stderr, no write, exit `2`
- [x] 3.6 zero-missing set: no prompt, `flows_pruned: []`, exit `0`, regardless of interactive/`--yes`
- [x] 3.7 composes with `--force`: a colliding name overwritten AND a missing name removed in one write (`--force --prune-missing --yes`)
- [x] 3.8 composes with the comment-loss guard: hand-written comments + non-empty missing set, interactive, decline either prompt → no write, exit `2`
- [x] 3.9 plain `init` (no `--prune-missing`) with a stale entry present: entry left untouched, no comparison computed (assert `compute_pruned_flows` path not exercised via behavior, not mocking)
- [x] 3.9a (P9, pre-apply review) `--prune-missing --yes` with a `--flows-dir` that discovers ZERO flows: the pre-existing zero-flows guard wins — exit `2`, existing `perf.toml` untouched, nothing pruned (proves `--prune-missing` cannot empty a table via an empty/wrong glob)
- [x] 3.9b (P10, pre-apply review) `--prune-missing --yes` (no `--force`), non-interactive, existing `perf.toml` has comments AND missing flows: comment-loss guard fires first → exit `2`, comment-loss message, no prune, file untouched
- [x] 3.9c (P11, pre-apply review) `--prune-missing --force --yes`, non-interactive, commented `perf.toml` with missing flows: BOTH guards waived → comments dropped AND missing entries pruned in one write, exit `0`
- [x] 3.9d (P12, pre-apply review) non-interactive preview (no `--yes`), `--json`, discovery adds a NEW flow while an existing entry is missing: v2 payload shows BOTH `flows_added` and `flows_pruned` populated (full would-be state), no write, exit `2`
- [x] 3.10 RED [UX]: `tests/golden/test_init_pretty_golden.py` — new fixture `init_pruned_flows_summary.txt` for the `flows pruned:` line; assert the 4 EXISTING golden fixtures (`init_fresh_create_summary.txt`, `init_merge_added_flows_summary.txt`, `init_mismatch_prompt.txt`, `init_comment_loss_confirm_prompt.txt`/`init_comment_loss_error.txt`) remain byte-identical (regression guard for the conditional-render decision)
- [x] 3.11 GREEN: `--update-golden` regenerates only the new fixture; re-run full golden suite to confirm the other 4 are untouched

## Phase 4: Docs + verification

- [x] 4.1 Update `README.md`'s "Configuring flows" section — document `--prune-missing`: opt-in, confirm-gated, composes with `--force`, non-interactive needs `--yes` or previews+exits `2`
- [x] 4.2 Run full `pytest --cov=perf --cov-fail-under=93`; confirm `init.py`'s new prune-gate lines are covered by Phase 3 integration tests IN THIS SAME PR (no deferred coverage)
- [x] 4.3 `ruff check`/`ruff format --check`/`mypy src/perf` clean
- [x] 4.4 Manual proof: `perfvibe init tests/fixtures/flows --prune-missing --yes` against a `tmp_path` `perf.toml` with a stale `[flows.*]` entry removes it; re-run without `--prune-missing` leaves a (re-added) stale entry untouched
