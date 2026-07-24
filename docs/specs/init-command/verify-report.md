# Verification Report: `init` capability (New command)

**Status**: PASS — SHIPPED AND INDEPENDENTLY VERIFIED

## Summary

The `perfvibe init` command was designed, implemented, and merged across three
chained PRs (PR-A foundation/fixtures/`init_v1` contract, PR-B pure helpers +
command, PR-C integration/wizard/golden/docs). This is an **independent** audit
run against the real code and tests on `main` — the apply-progress self-reports
were read for context only, not trusted. All 49 tasks are checked complete and
every claim was re-verified from source. **0 CRITICAL, 1 WARNING, 1 SUGGESTION.**
The one WARNING and one SUGGESTION are internal doc-consistency nits, not
functional gaps — the change is correct, complete, and ready to archive.

## Delivery

| Item | Status | Ref |
|---|---|---|
| Specification | SHIPPED ✓ | `docs/specs/init-command/spec.md` (I1–I17 matrix) |
| Design | SHIPPED ✓ | `docs/specs/init-command/design.md` (Open Questions resolved) |
| Implementation | MERGED ✓ | PR-A → PR-B → PR-C, all on `main` |
| Verification | PASS ✓ | This report — 0 CRITICAL, 1 WARNING, 1 SUGGESTION |
| Test Suite | 506 passing ✓ | `.venv/bin/pytest -q --cov=perf --cov-report=term-missing` |
| Linting & Type Check | CLEAN ✓ | `ruff check .`, `ruff format --check .`, `mypy src/perf` all green |
| Coverage | 94.53% ✓ | Floor 93%; `init.py` 89%, `contracts/init_v1.py` 100% |
| Layering (CLI-only) | VERIFIED ✓ | Zero `domain/`/`application/` imports in `init.py` |
| Exit-code discipline | VERIFIED ✓ | `init.py` never emits `1` anywhere; only `0`/`2`/`3` |

## Runtime Evidence (re-run independently, not trusted from report)

- `.venv/bin/pytest -q --cov=perf --cov-report=term-missing` → **506 passed**,
  TOTAL coverage **94.53%** (`Required test coverage of 93.0% reached`).
- `.venv/bin/ruff check .` → `All checks passed!`
- `.venv/bin/ruff format --check .` → `98 files already formatted`.
- `.venv/bin/mypy src/perf` → `Success: no issues found in 45 source files`.
- `rg` on `src/perf/cli/commands/init.py`: no `TODO`/`FIXME`/`XXX`/`stub`/
  `NotImplementedError`; no `Exit(code=1)`/`sys.exit(1)`; no `from
  perf.domain`/`perf.application` import; no `--out` flag.

## Corner-Case Matrix (I1–I17) — every case has a real, passing test

| # | Corner case | Covering test(s) | Status |
|---|---|---|---|
| I1 | Nonexistent/empty `--flows-dir` → exit 2 | `test_cli_init.py::test_zero_flows_dir_exits_2_and_writes_nothing`; `test_init_discover.py::test_discover_flows_yields_zero_candidates_for_{a_nonexistent_directory,an_empty_directory}` | ✓ |
| I2 | Zero flows after `subflows/` exclusion → exit 2 | `test_init_discover.py::test_discover_flows_yields_zero_candidates_when_everything_is_under_subflows` + I1 CLI test (flows_empty fixture is all-subflows) | ✓ |
| I3 | Single concrete `appId` → auto default | `test_init_reconcile.py::test_single_concrete_value_becomes_the_candidate`; `test_cli_init_wizard.py` (`bundle_id_source == "detected"`) | ✓ |
| I4 | All `${VAR}` templated → no default | `test_init_parse_app_id.py::test_templated_appid_returns_the_template_sentinel`; `test_init_reconcile.py::test_zero_concrete_values_leaves_no_candidate` | ✓ |
| I5 | Missing/malformed + concrete-consistent → detection from concrete subset | `test_init_reconcile.py::test_template_and_none_values_are_treated_as_absent_alongside_a_single_concrete_value`; `test_init_parse_app_id.py` missing/no-separator cases | ✓ |
| I6 | Mismatch — interactive → prompt | `test_cli_init_wizard.py::test_wizard_mismatch_prompt_shown_and_resolves_via_typed_input` | ✓ |
| I7 | Mismatch — non-interactive no `--bundle-id` → exit 2 | `test_cli_init.py::test_mismatch_non_interactive_without_bundle_id_exits_2` | ✓ |
| I8 | Mismatch — non-interactive `--bundle-id` → exit 0 | `test_cli_init.py::test_mismatch_non_interactive_with_bundle_id_resolves_exit_0` | ✓ |
| I9 | No existing `perf.toml` → fresh create | `test_cli_init.py::test_fresh_config_created_and_round_trips...`; `test_init_merge.py::test_no_existing_config_creates_flows_from_scratch` | ✓ |
| I10 | Existing file, only new names → merge, existing untouched | `test_cli_init.py::test_merge_new_flow_names_leaves_existing_entries_untouched`; `test_init_merge.py::test_new_flow_names_merge_in_and_existing_entries_are_untouched` | ✓ |
| I11 | Collision no `--force` → exit 2, file untouched | `test_cli_init.py::test_colliding_flow_name_without_force_exits_2_file_untouched`; `test_init_merge.py::test_colliding_flow_name_without_force_raises` | ✓ |
| I12 | Collision + `--force` → overwritten, exit 0 | `test_cli_init.py::test_colliding_flow_name_with_force_overwrites_exit_0`; `test_init_merge.py::test_colliding_flow_name_with_force_overwrites` | ✓ |
| I13 | TTY, no `--yes` → wizard runs | `test_cli_init_wizard.py::test_wizard_prompt_shown_and_blank_enter_accepts_dim_placeholder_default` | ✓ |
| I14 | TTY + `--yes` → forced non-interactive | `test_cli_init_wizard.py::test_yes_forces_non_interactive_despite_simulated_tty` | ✓ |
| I15 | Non-TTY, no `--yes` → auto non-interactive | `test_cli_init_wizard.py::test_non_tty_without_yes_auto_detects_non_interactive` (+ every `test_cli_init.py` scenario) | ✓ |
| I16 | Round-trip: written file reloads, flows config-known, `MaestroDriver` accepts | `test_cli_init.py::test_fresh_config_created_and_round_trips_through_load_config_and_driver` (real `load_config` + real `MaestroDriver.command()`) | ✓ |
| I17 | Unexpected I/O failure writing → exit 3 | `test_cli_init.py::test_unwritable_target_dir_exits_3` (real read-only dir; posix-guarded) | ✓ |

**Invariant hold**: `init` never crashes, never writes a partial/corrupt
`perf.toml` on error, and never exits `1`. The exit-code sweep
(`test_init_never_exits_1`, `test_init_never_exits_1_on_flows_dir_with_flags`)
parametrizes every fixture/flag combination and asserts `exit_code != 1`.

## Mid-Flow Decisions (resolved after proposal) — verified implemented

1. **`--driver`/`--db` verbatim pass-through (decision #1)** — VERIFIED.
   `init.py:529-532` writes `merged["driver"] = driver` / `merged["db_path"] = db`
   only when supplied, with no detection logic. Covered by
   `test_cli_init.py::test_driver_and_db_written_verbatim_only_when_supplied`
   and `..._omitted_entirely_when_not_supplied`.
2. **Output path reuses `--config`, no `--out` flag (decision #2)** — VERIFIED.
   The `init` typer signature has no `--out`; path resolution reads
   `state.get("config_path")` and defaults to `Path.cwd() / "perf.toml"`
   (`init.py:479-483`). Covered by
   `test_output_path_defaults_to_cwd_perf_toml_when_config_omitted` and
   `test_explicit_config_path_used_verbatim`.
3. **Comment-loss requires confirmation/`--force` (decision #3)** — VERIFIED.
   `has_comments()` gate at `init.py:498-515`: interactive `typer.confirm`,
   non-interactive exit 2 unless `--force`. Covered by
   `test_comment_guard_requires_force_non_interactively` and
   `test_comment_guard_force_proceeds_and_overwrites`, golden-pinned in
   `test_init_pretty_golden.py`.

## Case-Insensitivity Correction (PR-A/PR-B) — verified genuine

`_is_subflows_segment` is unit-tested with **direct string literals** in
`tests/unit/test_init_discover.py::test_is_subflows_segment_matches_case_insensitively`
(`"subflows"`, `"SUBFLOWS"`, `"SubFlows"` all → `True`) plus a rejection test
(`"checkout"`, `"subflows2"`, `""` → `False`). This is NOT a filesystem-dependent
fixture — the test docstring correctly notes macOS/APFS collapses `subflows/`
and `Subflows/` to the same path, so a case-variant on-disk tree would be
non-portable. The correction genuinely holds and exercises multiple cases.

## Contract & Golden Discipline

- `tests/contract/test_init_v1_contract.py`: pins `schema_version == 1`, all
  required keys/types, and fails on an unversioned shape change
  (`test_contract_rejects_a_shape_change_without_version_bump`).
- `tests/golden/test_init_pretty_golden.py` (11 tests): 5 golden-pinned
  fixtures (color off) + ANSI-byte-absence assertions across unit-level
  `color=False`, `--no-color`, `NO_COLOR` env, default non-TTY, and the
  comment-loss error path. Confirms the pretty view carries no `schema_version`
  and the machine contract stays `--json`-only.

## Doc-Consistency Findings (non-blocking)

### WARNING — design.md "Typer signature" line is stale vs the resolved Open Questions
`docs/specs/init-command/design.md:56-58` still describes the signature as
`--driver="maestro" (written only if non-default), --out="perf.toml"`. Both are
superseded: the code has no `--out` (reuses `--config`, decision #2) and writes
`--driver` verbatim when supplied with no "non-default" logic (decision #1). The
design's own Open Questions section (lines 98-99) IS correctly marked `[x]`
resolved with notes pointing at those decisions, so this is an internal
inconsistency within design.md — the newer resolution contradicts the older
signature prose. No functional impact; the implementation follows tasks.md,
which supersedes the design draft. Recommend a one-line edit to the signature
prose during archive for future-reader clarity.

### SUGGESTION — spec I10 "byte-for-byte unmodified" wording vs full re-serialize
Spec scenario "New flow names merge into an existing file" (`spec.md:110`) says
`[flows.login]` is left "byte-for-byte unmodified". The design deliberately
chose a full canonical re-serialize (`serialize_toml`), which preserves existing
entry *values* but not necessarily byte-level formatting/comments — precisely
why the comment-loss guard exists. The merge test asserts value preservation
(`maestro_path == "existing.yaml"`), which is the real guarantee. Consider
softening the spec wording to "left semantically unmodified (values preserved)"
to match the accepted design. Not a defect.

## Traceability

- **Spec**: `docs/specs/init-command/spec.md` (Engram `sdd/init-command/spec` #69)
- **Design**: `docs/specs/init-command/design.md` (Engram `sdd/init-command/design` #70)
- **Tasks**: `docs/specs/init-command/tasks.md` (Engram `sdd/init-command/tasks` #71, 49/49 complete)
- **Apply Progress**: Engram `sdd/init-command/apply-progress` #72 (context only, not trusted)
- **This Verify Report**: `docs/specs/init-command/verify-report.md`; also Engram `sdd/init-command/verify-report`

## Verdict

**PASS.** All I1–I17 corner cases have real passing tests; all 3 mid-flow
decisions are correctly implemented; the case-insensitivity correction is
genuine; no stubs/TODOs; exit-1 never emitted; CLI-only layering holds; full
suite + lint + types green; README matches behavior. The two findings are
documentation-consistency nits, safe to fix during archive. Ready for
`sdd-archive`.

---

**Verified**: 2026-07-24
