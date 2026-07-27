# Design: `init --prune-missing` (stale-flow reconciliation)

## Technical Approach

Purely additive, opt-in reconciliation inside the two files the proposal names:
`cli/commands/init.py` + `contracts/init_v1.py`. No domain/application/port/DB
change — directory glob + TOML text stays a single-implementation CLI concern
(`python-architecture` rule 1/3). The prune decision (which existing
`[flows.*]` entries no longer have a discovered file) is a pure set-difference;
the deletion is gated by the SAME interactive-confirm / non-interactive-`--yes`
pattern the comment-loss guard already established. `init` still exits `0/2/3`
only — never `1`.

## Architecture Decisions

| Decision | Choice | Rejected | Rationale |
|---|---|---|---|
| Where the diff lives | New pure helper `compute_pruned_flows(existing, new_flows) -> list[str]` (sorted `existing_flow_names - discovered_names`) **+** a `prune: bool` param on `merge_config` that internally reuses that helper to drop those entries | Return `(merged, pruned)` tuple from `merge_config`; a whole new `update` command | The confirm-gate must know the would-be-pruned set BEFORE the merge/write runs, so merge-then-report is impossible. One pure helper is the single source of truth reused by both the gate and `merge_config` — add + drop reconciliation still lives in `merge_config` (locality preserved), no duplication. |
| Confirm-gate ordering | Prune gate slots AFTER the comment-loss guard, BEFORE `merge_config` | Prune before comment guard; one merged prompt | Comment-loss is a precondition on rewriting the file at all (can early-exit `2`); no point previewing a prune we'd abort anyway. Both operate on already-read `existing_data`. |
| Two gates in one run | Two sequential single-purpose prompts | One combined prompt | Each gate keeps its own golden-testable message; `--force` (overwrite/comments) and `--prune-missing`+confirm (delete) stay orthogonal. |
| `flows_pruned` shape | `list[str]` (names only, uniform reason: source file gone) | `list[{name,reason}]` mirroring `flows_skipped` | No abstraction until earned; `flows_skipped` = discovered-but-not-added is the OPPOSITE action — never overload it. |
| Schema bump | `SCHEMA_VERSION 1→2`; additive `flows_pruned` key | Reuse v1 | Adding a `--json` key IS a shape change (`perf-cli-standards` rule 6/8). |
| Non-interactive w/o `--yes` | Emit the v2 payload (would-be-pruned set) to stdout under `--json`, else preview to stderr; then exit `2` | Silent no-op; silent delete | CI sees exactly what would go, never destroys unconfirmed. |

## Data Flow

    init.py …existing guards → appId/bundle_id → read existing perf.toml
        └─→ comment-loss guard (may exit 2)
        └─→ IF --prune-missing:
              missing = compute_pruned_flows(existing_data, flows)   # pure
              missing empty            → prune=False, flows_pruned=[]
              interactive              → preview + confirm; decline → exit 2
              non-interactive + --yes  → prune=True
              non-interactive, no --yes→ emit v2 payload/preview + exit 2
        └─→ merge_config(existing, flows, bundle, force, prune) → serialize → write
        └─→ build_init_payload(..., flows_pruned=missing if prune else [])
        └─→ exit 0

## Interfaces / Contracts

`init_v1` v2 payload adds one key; `build_init_payload` gains
`flows_pruned: Sequence[str] = ()` (default keeps callers valid) → `"flows_pruned": list(flows_pruned)`:

```json
{ "schema_version": 2, "config_path": "perf.toml", "bundle_id": "com.x",
  "bundle_id_source": "detected", "flows_added": ["checkout"],
  "flows_skipped": [], "flows_pruned": ["stale"], "flows_total": 1,
  "appid_conflict": null }
```

New Typer option: `prune_missing: bool = typer.Option(False, "--prune-missing", help="Remove [flows.*] entries whose flow file is gone from flows-dir (confirm-gated; needs --yes when non-interactive)")`. Composes orthogonally with `--force`/`--yes`/`--bundle-id`/`--driver`/`--db`.

New pure render helpers mirror the comment-loss pair:
`_render_prune_confirm_prompt(missing)` (interactive) and
`_render_prune_preview(missing)` (non-interactive stderr). `_render_confirmation`
gains a `flows_pruned: Sequence[str]` param and appends a `flows pruned: …` line
ONLY when non-empty — existing 4 goldens stay byte-identical.

## File Changes

| File | Action | Description |
|---|---|---|
| `cli/commands/init.py` | Modify | `--prune-missing` flag; `compute_pruned_flows`; `prune` param on `merge_config`; prune gate; render helpers; wire `flows_pruned`. |
| `contracts/init_v1.py` | Modify | `flows_pruned` param+key; `SCHEMA_VERSION = 2`. |
| `tests/contract/test_init_v1_contract.py` | Modify | Assert v2 shape incl. `flows_pruned: list`; `test_schema_version_is_2`. |
| `tests/unit/test_init_merge.py` | Modify | `compute_pruned_flows` + `merge_config(prune=…)` cases. |
| `tests/integration/test_cli_init.py` | Modify | Prune scenarios. |
| `tests/golden/test_init_pretty_golden.py` | Modify | Pruned-line + prune-prompt goldens. |

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit | `compute_pruned_flows`: some-missing, none-missing, all-missing; `merge_config` prune=True drops ONLY missing, prune=False identical to today | pure, table |
| Contract | v2 shape; `flows_pruned` round-trips; unversioned-shape guard fails on drift | `tests/contract/` |
| Integration | interactive confirm/decline; non-interactive `--yes` prunes; non-interactive w/o `--yes` → v2 payload + exit 2; zero-missing no-op; plain `init` unchanged | `CliRunner` + `tmp_path` perf.toml with a stale `[flows.stale]` entry (dynamically written — NO new static fixture) + existing `tests/fixtures/flows/` |
| Golden | pruned `flows pruned:` line; prune confirm/preview text, color off | `--update-golden` |

Fixtures: reuse existing `flows/` tree; write the stale-entry `perf.toml` into
`tmp_path` (the established `test_cli_init.py` pattern) — avoids a new static
fixture file, per the stdlib-first/minimal-fixture stance.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process integration. Input safety unchanged: `tomllib` only,
never `eval`/`exec`, no execution of any read file.

## Migration / Rollout

No data migration. `--prune-missing` off by default → plain `init` identical to
today. `run`/`compare`/`budget-check`, the DB schema, and `load_config` are
untouched. Rollback = revert branch. Consumers pinned to `schema_version==1`
must accept the additive `flows_pruned` key at v2.

## Open Questions

- [x] Non-interactive w/o `--yes` emits v2 payload then exit 2 — **Resolved** (confirmed decision #1).
- [x] `flows_pruned` is `list[str]`, no `reason` — **Resolved** (confirmed decision #2).
