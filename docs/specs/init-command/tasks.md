# Tasks: `perfvibe init` capability

Grounded in spec `docs/specs/init-command/spec.md`, design `docs/specs/init-command/design.md`.
Scope: new `perfvibe init` command only — `run`/`compare`/`budget-check`, schema, and
`load_config` are untouched (additive-only).

**Resolved after spec/design (hard requirements, not open questions):**
1. `--driver`/`--db` ARE in scope — trivial pass-through flags, written verbatim as literal
   top-level TOML keys (`driver = "..."`, `db_path = "..."`) only if the user supplies them. No
   detection logic for these two (unlike `bundle_id`).
2. Output path reuses the existing global `--config` option (no new `--out` flag). Defaults to
   `./perf.toml` in CWD when `--config` is omitted, matching `_find_project_config`'s CWD-only
   discovery.
3. Comment-loss on re-serialize: if the existing target `perf.toml` contains a `#` comment, print
   a clear warning and require explicit confirmation (interactive) or `--force`
   (non-interactive/scripted) before overwriting — never silently destroy hand-written notes.

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | ~1600–1750 (prod ~400, fixtures ~180, tests ~1000, docs ~35) across 3 PRs |
| 400-line budget risk | N/A — session budget for this change is 800 lines |
| 800-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR-A → PR-B → PR-C |
| Delivery strategy | single-pr-default (cached) |
| Chain strategy | pending — user decision needed |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

> Note: the orchestrator's cached review budget for this change is 800 lines (not the usual
> 400). Even against 800, the total estimate (~1600–1750) is roughly double a single PR's
> budget, and each individual PR below stays under 800. `delivery_strategy=single-pr-default`
> normally resolves to requiring `size:exception`; given the size, the recommendation is instead
> to chain — surfaced here for the user to pick `stacked-to-main` or `feature-branch-chain`
> before `sdd-apply` starts, per the `single-pr` guard rule ("Decision needed before apply: Yes").

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| 1 | Fixture tree + `init_v1` contract (pure, no command yet) | PR-A (~300 ln) | `pytest tests/contract/test_init_v1_contract.py -q` | N/A — pure payload builder, no CLI wiring yet | Revert `tests/fixtures/flows*/`, `src/perf/contracts/init_v1.py`, its contract test; nothing else depends on these yet |
| 2 | Pure helpers (`discover_flows`, `parse_app_id`, `reconcile_bundle_id`, `serialize_toml`, `merge_config`, `has_comments`) + the `init` command assembled in `init.py` | PR-B (~700–750 ln) | `pytest tests/unit/test_init_discover.py tests/unit/test_init_parse_app_id.py tests/unit/test_init_reconcile.py tests/unit/test_init_serialize.py tests/unit/test_init_merge.py -q` | N/A — pure functions over fixture text/dicts, no CLI registration yet (not runnable as `perfvibe init` until PR-C) | Revert `src/perf/cli/commands/init.py` and its 5 unit test files; PR-A fixtures/contract remain independently valid |
| 3 | CLI registration + integration tests (fixture-driven, wizard, comment-guard, exit codes) + pretty golden + README | PR-C (~650 ln) | `pytest tests/integration/test_cli_init.py tests/integration/test_cli_init_wizard.py tests/golden/test_init_pretty_golden.py -q` | `perfvibe init tests/fixtures/flows --yes --bundle-id com.example.app` — device-free, real fs (`tmp_path`) | Revert `src/perf/cli/main.py` registration block, both integration test files, the golden test + its fixtures, and the README section; PR-A/PR-B remain usable standalone (unregistered command) |

## Phase 1: Foundation — fixtures + `init_v1` contract (PR-A)

- [x] 1.1 Create `tests/fixtures/flows/login.yaml` — concrete `appId: com.example.app` header + `---` + a trivial step (spec "Concrete appId is parsed")
- [x] 1.2 Create `tests/fixtures/flows/checkout/cold.yaml` — nested category dir, same concrete `appId: com.example.app` (spec "Nested category directories are included"; "Single concrete value is the default")
- [x] 1.3 Create `tests/fixtures/flows/templated_launch.yaml` — `appId: ${APP_ID}` (spec "Templated appId is not a concrete detection")
- [x] 1.4 Create `tests/fixtures/flows/missing_header.yaml` — no `appId:` line, has a `---` separator (spec "Missing or malformed header does not error by itself")
- [x] 1.5 Create `tests/fixtures/flows/no_separator.yaml` — no `---` anywhere in the file, no `appId:` line (same requirement, different malformed shape)
- [x] 1.6 Create `tests/fixtures/flows/subflows/login-fragment.yaml` and `tests/fixtures/flows/subflows/util.yml` — both must be excluded (spec "subflows/ is excluded regardless of depth"). **Correction**: the original plan called for a second, case-variant `Subflows/` directory to prove case-insensitive exclusion via the filesystem — on this machine's case-insensitive filesystem (macOS/APFS), `subflows/` and `Subflows/` collapse into the same directory, so the two files landed in one folder and git never recorded a second, distinctly-cased tree entry. A real on-disk case-variant directory is not a portable way to prove case-insensitivity (it would only "work" on case-sensitive filesystems like Linux ext4, and silently do nothing on macOS/Windows). Case-insensitivity of the exclusion match itself must instead be unit-tested directly against the segment-matching predicate with varied-case string literals (`"subflows"`, `"SUBFLOWS"`, `"SubFlows"`) — see corrected 2.1 below.
- [x] 1.7 Create `tests/fixtures/flows_mismatch/app_a.yaml` (`appId: com.example.app`) and `tests/fixtures/flows_mismatch/app_b.yaml` (`appId: com.other.app`) — conflict scenario (I6/I7/I8)
- [x] 1.8 Create `tests/fixtures/flows_empty/subflows/only.yaml` — every file lives under `subflows/`, so discovery yields zero candidate flows (I1/I2)
- [x] 1.9 Create `src/perf/contracts/init_v1.py` — `SCHEMA_VERSION = 1`, `build_init_payload(...) -> dict` mirroring `compare_v1.py`'s shape: `schema_version`, `config_path`, `bundle_id`, `bundle_id_source` (`detected|flag|prompt|none`), `flows_added`, `flows_skipped` (name+reason), `flows_total`, `appid_conflict`
- [x] 1.10 RED: `tests/contract/test_init_v1_contract.py` — required keys/types for the `init_v1` payload; fails on any unversioned shape change (mirrors `test_compare_v1_contract.py`)
- [x] 1.11 GREEN: confirm `build_init_payload` satisfies 1.10 — pure function, zero CLI/typer dependency

## Phase 2: Pure helpers + `init` command (PR-B)

- [x] 2.1 RED: `tests/unit/test_init_discover.py` — `discover_flows(flows_dir)`: recursive, case-insensitive `*.yaml`/`*.yml`, excludes any path segment `== "subflows"` (case-insensitive, any depth), keeps nested non-`subflows` real flows, empty/nonexistent dir yields zero candidates (I1/I2, fixtures from 1.1–1.2, 1.6, 1.8). **Case-insensitivity of the segment match** must be unit-tested directly against the exclusion predicate with varied-case string literals (e.g. `_is_subflows_segment("SUBFLOWS")`, `("SubFlows")`, `("subflows")` all `True`) — do NOT rely on a real on-disk case-variant directory (macOS/APFS collapses `subflows/`/`Subflows/` into one path; see 1.6's correction note)
- [x] 2.2 GREEN: `src/perf/cli/commands/init.py` — `discover_flows(flows_dir: Path) -> dict[str, Path]` (name = filename stem)
- [x] 2.3 RED: `tests/unit/test_init_parse_app_id.py` — `parse_app_id(header_text)`: concrete value, quoted value (strip matching quotes), `${...}` → `TEMPLATE` sentinel, missing `appId:` → `None`, stop scanning at a `strip() == "---"` line, unterminated file (no separator) still bounded
- [x] 2.4 GREEN: `init.py` — `parse_app_id(text: str) -> str | None | Literal["TEMPLATE"]`, exact line-scan algorithm from design (bounded lines/line-length, never `eval`)
- [x] 2.5 RED: `tests/unit/test_init_reconcile.py` — `reconcile_bundle_id(appid_by_flow)`: single concrete value → default; zero concrete values → `None`/no default; two+ differing concrete values → conflict result; `TEMPLATE`/`None` values treated as absent (I3/I4/I6, pure — no interactive/flag logic here)
- [x] 2.6 GREEN: `init.py` — `reconcile_bundle_id(appid_by_flow: Mapping[str, str | None]) -> BundleReconciliation` (candidate value, conflict tuple or `None`)
- [x] 2.7 RED: `tests/unit/test_init_serialize.py` — `serialize_toml(data)`: literal string `'…'` default for `maestro_path`; falls back to escaped basic string `"…"` only when the value contains `'` or a control char; output always round-trips via `tomllib.loads`
- [x] 2.8 GREEN: `init.py` — `serialize_toml(data: dict) -> str`
- [x] 2.9 RED: `tests/unit/test_init_merge.py` — `merge_config(existing, new_flows, bundle_id, force)`: new flow names merge in, existing `[flows.*]` entries untouched; colliding flow name refused unless `force=True` (then overwritten); `has_comments(raw_text)` detects a `#` outside string literals
- [x] 2.10 GREEN: `init.py` — `merge_config(...) -> dict` and `has_comments(raw_toml_text: str) -> bool`
- [x] 2.11 GREEN: `init.py` — assemble the `init` typer command: reads `ctx.obj` (`output`, `config`); args `flows_dir` (existing dir, usage error otherwise), `--bundle-id`, `--driver`, `--db` (verbatim literal pass-through, decision #1), `--force`, `--yes`; TTY detection (`sys.stdin.isatty()`) vs `--yes` override; wizard prompt with a dim, pre-filled placeholder default (Enter accepts, typed value overrides); comment-detected confirmation gate (interactive prompt / `--force` required non-interactively, decision #3); resolves output path from `--config` else `./perf.toml` in CWD (decision #2); exit `0/2/3` per spec's Exit-Code Discipline, never `1`
- [x] 2.12 GREEN: `src/perf/cli/main.py` — register `init` (mirrors the `run`/`compare`/`budget-check` `app.command(...)` block)

## Phase 3: Integration + golden + docs (PR-C)

- [ ] 3.1 RED (highest blast radius, real wiring): `tests/integration/test_cli_init.py` — full `perfvibe init` via `CliRunner` against `tests/fixtures/flows`: creates `perf.toml` fresh when absent (I9), round-trips through `load_config`/`MaestroDriver` (I16), `--json` `init_v1` payload end-to-end
- [ ] 3.2 RED: `test_cli_init.py` (extend) — zero-flows dir exits `2` (I1/I2, `flows_empty` fixture); mismatch non-interactive w/o `--bundle-id` exits `2` (I7, `flows_mismatch` fixture); mismatch non-interactive w/ `--bundle-id` resolves, exit `0` (I8)
- [ ] 3.3 RED: `test_cli_init.py` (extend) — merge into an existing `perf.toml`: new flow names merge in, existing entries byte-for-byte untouched (I10); colliding flow name w/o `--force` exits `2`, file untouched (I11); w/ `--force` overwrites, exit `0` (I12)
- [ ] 3.4 RED: `test_cli_init.py` (extend) — comment-preservation guard (decision #3): an existing `perf.toml` containing a `#` comment requires confirmation; non-interactive/`--yes` without `--force` exits `2` with a clear warning; `--force` proceeds and overwrites
- [ ] 3.5 RED: `test_cli_init.py` (extend) — `--driver`/`--db` passthrough (decision #1): written verbatim as literal top-level keys (`driver = "..."`, `db_path = "..."`) only when supplied; omitted entirely otherwise
- [ ] 3.6 RED: `test_cli_init.py` (extend) — output path resolution (decision #2): defaults to `./perf.toml` in CWD (via `tmp_path`) when `--config` is omitted; an explicit `--config <path>` is used verbatim
- [ ] 3.7 RED: `test_cli_init.py` (extend) — exit-code sweep: never exits `1` across I1–I17; an unexpected I/O failure (e.g. unwritable target dir) exits `3` (I17)
- [ ] 3.8 RED (interactive path): `tests/integration/test_cli_init_wizard.py` — TTY simulated (`isatty` patched `True`) w/o `--yes`: wizard prompts shown, dim-placeholder default accepted on blank Enter, override on typed input; `--yes` forces non-interactive despite TTY (I13/I14); non-TTY w/o `--yes` auto-detects non-interactive (I15)
- [ ] 3.9 GREEN: fold any implementation gaps surfaced by 3.1–3.8 back into `init.py`
- [ ] 3.10 RED [UX]: `tests/golden/test_init_pretty_golden.py` — color forced off, fixed width: (a) fresh `perf.toml` created summary, (b) merge-added-flows summary, (c) `bundle_id` mismatch prompt text, (d) comment-loss warning text; assert no ANSI bytes under `--no-color`/`NO_COLOR`/non-TTY (mirrors `test_budget_check_pretty_golden.py`)
- [ ] 3.11 GREEN: `init.py`'s pretty-render helper(s) satisfy 3.10 (`--update-golden` regenerates)
- [ ] 3.12 Update `README.md` — new "Configuring flows" section: `perf.toml`'s `[flows]` table shape (`[flows.<name>]` → `maestro_path`), that `perfvibe init` scaffolds/merges it, and that CI should read a **committed** `perf.toml` rather than regenerate one at CI time
- [ ] 3.13 Cross-check `docs/specs/init-command/design.md` Open Questions — mark both resolved (output-path reuse of `--config`; comment-loss confirm/`--force` gate) per the 3 decisions above

## Phase 4: Verification

- [ ] 4.1 Run full `pytest`; confirm `ruff`/`mypy` clean; confirm the domain/application boundary test is unaffected (`init.py` is CLI-only — no `domain/`/`application/` import)
- [ ] 4.2 Confirm the 93% coverage floor holds with `init.py` + `contracts/init_v1.py` added
- [ ] 4.3 Manual round-trip proof (I16): `perfvibe init tests/fixtures/flows --yes --bundle-id com.example.app` writes a valid `perf.toml`; `perfvibe --config <path> run <flow>` recognizes every scaffolded flow
