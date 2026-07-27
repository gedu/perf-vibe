# Verification Report: `init --prune-missing` (stale-flow reconciliation)

**Status**: PASS — INDEPENDENTLY VERIFIED, READY TO ARCHIVE

## Summary

`init --prune-missing` was designed, implemented, and committed on branch
`feat/init-prune-missing` (single PR; CI green; not yet merged). This is an
**independent** audit run against the real code and tests on that branch — the
`apply-progress` self-report (Engram #81) was read for context only, NOT
trusted. Every spec scenario, every corner-case-matrix row (P1–P12, with
special scrutiny on P9–P12 added during pre-apply review), the v2 contract, the
byte-identical golden guarantee, exit-code discipline, CLI-only layering, and
the README were each re-verified from source. **0 CRITICAL, 1 WARNING,
1 SUGGESTION.** The WARNING and SUGGESTION are internal doc-consistency nits,
not functional gaps — the change is correct, complete, and ready to archive.

## Delivery

| Item | Status | Ref |
|---|---|---|
| Specification | SHIPPED ✓ | `docs/specs/prune-missing/spec.md` (P1–P12 matrix) |
| Design | SHIPPED ✓ | `docs/specs/prune-missing/design.md` (Open Questions resolved) |
| Implementation | COMMITTED (not merged) ✓ | `feat/init-prune-missing`, CI green |
| Verification | PASS ✓ | This report — 0 CRITICAL, 1 WARNING, 1 SUGGESTION |
| Test Suite | 536 passing ✓ | `.venv/bin/pytest -q --cov=perf --cov-report=term-missing` |
| Linting & Type Check | CLEAN ✓ | `ruff check .`, `ruff format --check .`, `mypy src/perf` all green |
| Coverage | 94.87% ✓ | Floor 93%; `init.py` 91%, `contracts/init_v1.py` 100% |
| Layering (CLI-only) | VERIFIED ✓ | Zero `domain/`/`application/` imports in `init.py` |
| Exit-code discipline | VERIFIED ✓ | `init.py` never emits `1`; only `0`/`2`/`3` |
| Golden regression | VERIFIED ✓ | 4 pre-existing fixtures byte-identical (git = added-only + in-test guard) |

## Runtime Evidence (re-run independently, not trusted from report)

- `.venv/bin/pytest -q --cov=perf --cov-report=term-missing` → **536 passed**,
  TOTAL coverage **94.87%** (`Required test coverage of 93.0% reached`).
- `.venv/bin/ruff check .` → `All checks passed!`
- `.venv/bin/ruff format --check .` → `98 files already formatted`.
- `.venv/bin/mypy src/perf` → `Success: no issues found in 45 source files`.
- `rg` on `src/perf/cli/commands/init.py` + `contracts/init_v1.py`: no
  `TODO`/`FIXME`/`XXX`/`stub`/`NotImplementedError`; no `Exit(code=1)`/
  `sys.exit(1)`; no `from perf.domain`/`perf.application` import.
- `git diff --name-status main...HEAD -- tests/golden/fixtures/` → three `A`
  (added) lines only; ZERO `M` (modified) — the 4 pre-existing fixtures
  (`init_fresh_create_summary.txt`, `init_merge_added_flows_summary.txt`,
  `init_mismatch_prompt.txt`, `init_comment_loss_confirm_prompt.txt` /
  `init_comment_loss_error.txt`) are untouched, not regenerated.

## Spec Scenario Compliance — every scenario has a real, passing test

| Requirement / Scenario | Covering test | Status |
|---|---|---|
| Default (no flag) unchanged | `test_cli_init.py::test_plain_init_without_prune_missing_leaves_stale_entry_untouched` | ✓ |
| Interactive confirm accepted prunes | `test_cli_init_wizard.py::test_prune_interactive_confirm_accepted_removes_stale_entry` | ✓ |
| Interactive confirm declined aborts | `test_cli_init_wizard.py::test_prune_interactive_confirm_declined_leaves_file_unmodified` | ✓ |
| Non-interactive `--yes` prunes immediately | `test_cli_init.py::test_non_interactive_yes_prunes_immediately_exit_0` | ✓ |
| Non-interactive no `--yes` previews + exit 2 | `test_cli_init.py::test_non_interactive_no_yes_json_previews_full_state_and_exits_2` (+ stderr variant) | ✓ |
| Zero missing = no-op | `test_cli_init.py::test_zero_missing_set_is_a_no_op_exit_0` (+ `_with_yes_too`) | ✓ |
| Composes with `--force` | `test_cli_init.py::test_composes_with_force_collision_and_prune_in_one_write` | ✓ |
| Composes with comment-loss guard | `test_cli_init_wizard.py::test_prune_composes_with_comment_loss_guard_declining_first_gate_aborts` | ✓ |
| Zero-discovered-flows guard precedence | `test_cli_init.py::test_p9_zero_discovered_flows_wins_over_prune_missing` | ✓ |
| Preview payload completeness | `test_cli_init.py::test_p12_preview_shows_both_flows_added_and_flows_pruned_populated` | ✓ |
| `init_v1` v2: `--json` summarizes write | `test_cli_init.py::test_fresh_config...round_trips...` (`schema_version == 2`) | ✓ |
| v2: pretty view never the parse target | `test_init_pretty_golden.py` (+ `test_fresh_config_pretty_output_exits_0`) | ✓ |
| v2: payload reports `flows_pruned` | `test_non_interactive_yes_prunes...` (`flows_pruned == ["stale"]`) | ✓ |
| v2: no pruning still reports v2 shape | `test_zero_missing_set_is_a_no_op` / plain-init (`flows_pruned == []`) | ✓ |

## Corner-Case Matrix (P1–P12) — every row has a real, passing test

| # | Corner case | Covering test | Status |
|---|---|---|---|
| P1 | `--prune-missing` omitted → zero comparison | `test_plain_init_without_prune_missing_leaves_stale_entry_untouched` | ✓ |
| P2 | Non-empty, interactive, confirmed → removed, exit 0 | `test_prune_interactive_confirm_accepted_removes_stale_entry` | ✓ |
| P3 | Non-empty, interactive, declined → no write, exit 2 | `test_prune_interactive_confirm_declined_leaves_file_unmodified` | ✓ |
| P4 | Non-empty, non-interactive, `--yes` → pruned, exit 0 | `test_non_interactive_yes_prunes_immediately_exit_0` | ✓ |
| P5 | Non-empty, non-interactive, no `--yes` → preview + exit 2 | `test_non_interactive_no_yes_json_previews_full_state_and_exits_2` + `..._no_json_previews_to_stderr_and_exits_2` | ✓ |
| P6 | Empty missing set → no-op | `test_zero_missing_set_is_a_no_op_exit_0` + `..._with_yes_too` | ✓ |
| P7 | prune + colliding name + `--force` → both in one write | `test_composes_with_force_collision_and_prune_in_one_write` | ✓ |
| P8 | prune + comment-loss guard, declined | `test_prune_composes_with_comment_loss_guard_declining_first_gate_aborts` | ✓ |
| **P9** | `--prune-missing --yes` + ZERO-discovery dir → zero-flows guard wins, exit 2, nothing pruned, file unchanged | `test_p9_zero_discovered_flows_wins_over_prune_missing` (seeds stale `perf.toml`, points at `flows_empty`, asserts exit 2 + `read_text() == original_text`) | ✓ |
| **P10** | prune + commented toml, only `--yes` (no `--force`) → comment-loss fires first, exit 2, no prune | `test_p10_comment_loss_guard_fires_first_with_only_yes` (asserts `"comment"` in output + file unchanged) | ✓ |
| **P11** | prune + commented toml, `--force` AND `--yes` → both waived, comments dropped + pruned in one write, exit 0 | `test_p11_force_and_yes_together_drop_comments_and_prune_in_one_write` (asserts `"# keep me"` gone + `stale` gone, exit 0) | ✓ |
| **P12** | Non-interactive preview, `--json`, add + prune together → both `flows_added` and `flows_pruned` populated, no write, exit 2 | `test_p12_preview_shows_both_flows_added_and_flows_pruned_populated` (asserts 5 `flows_added` + `flows_pruned == ["stale"]` + `bundle_id`/`flows_total` + file unchanged) | ✓ |

## Contract (v2) Verification

- `contracts/init_v1.py`: `SCHEMA_VERSION = 2` ✓; `build_init_payload` gains
  `flows_pruned: Sequence[str] = ()` → `"flows_pruned": list(flows_pruned)` ✓;
  no other key changed.
- `test_init_v1_contract.py`: `test_schema_version_is_2` ✓; `flows_pruned: list`
  in `_REQUIRED_KEYS_AND_TYPES` ✓; `test_flows_pruned_round_trips` +
  `test_flows_pruned_defaults_to_empty_list_when_omitted` ✓;
  `test_contract_rejects_a_shape_change_without_version_bump` fails on
  unversioned shape drift ✓.

## Design Coherence

Implementation matches design exactly:
- `compute_pruned_flows(existing, new_flows) -> list[str]` — pure sorted
  set-difference, single source of truth reused by both the `init()` prune gate
  and `merge_config`'s `prune` param (design "Where the diff lives"). ✓
- Prune gate slotted AFTER comment-loss guard, BEFORE `merge_config`
  (`init.py:584–637`); knows the would-be-pruned set before the write. ✓
- `_render_prune_confirm_prompt` / `_render_prune_preview` mirror the
  comment-loss pair; `_render_confirmation` gains `flows_pruned` with
  conditional-render (line only when non-empty → 4 goldens byte-identical). ✓
- Non-interactive-no-`--yes` emits the FULL v2 payload (`flows_added` +
  `flows_pruned` + `bundle_id` + `flows_total`) then exits 2 without writing. ✓

## README Verification (no drift)

`README.md` "Configuring flows" correctly documents `--prune-missing`: opt-in;
confirm-gated the same way as the comment-loss guard; interactive previews +
prompts; non-interactive needs `--yes` or previews (stdout as `--json`, else
stderr) + exits 2; composes with `--force`; and — critically — states that a
commented `perf.toml` pruned non-interactively needs **BOTH** `--force` and
`--yes` "since each guard is waived independently." It correctly conveys that
`--yes` is the prune confirmation and does NOT wrongly imply `--force` is the
prune confirmation. Also documents the zero-discovery footgun guard. No drift.

## Issues

### WARNING (non-blocking)
- **On-disk `tasks.md` checkboxes not updated.** `docs/specs/prune-missing/tasks.md`
  still shows all 25 tasks as `- [ ]` (unchecked), while the tracked Engram
  tasks artifact (#80) marks all `[x]` complete. This is the documented process
  deviation (apply-progress §Deviations): the orchestrator instructed that the
  staged spec docs not be modified, so completion was persisted only to Engram.
  Code state fully matches every task; this is a doc-consistency nit, not a
  functional gap. Optionally sync the on-disk checkboxes at archive time.

### SUGGESTION (non-blocking)
- **`init.py` interactive-abort branches uncovered.** `init.py` file coverage is
  91%; the uncovered lines are almost entirely `typer.Abort` (Ctrl-C/EOF mid-
  prompt) edge paths — including the new prune-confirm abort (601–603) — which
  mirror the pre-existing, already-uncovered equivalent branch on the
  comment-loss guard. Total coverage 94.87% clears the 93% floor. A future
  simulated-EOF test could close these symmetric abort branches.

## Verdict

**PASS.** 0 CRITICAL, 1 WARNING, 1 SUGGESTION. All 14 spec scenarios and all 12
corner-case rows (P1–P12) have real, passing tests; P9–P12 were verified line by
line against their assertions. The v2 contract, byte-identical golden guarantee,
exit-code discipline (never `1`), CLI-only layering, absence of TODO/stub, and
README accuracy all hold. Ready for `sdd-archive`.
