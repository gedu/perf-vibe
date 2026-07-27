# Proposal: `init --prune-missing` (stale-flow reconciliation)

## Intent

`perfvibe init` merges discovered flows into `perf.toml` but NEVER reconciles
deletions: `merge_config` (`init.py:259`) copies every existing `[flows.*]`
entry through untouched, so a flow whose file was deleted from `--flows-dir`
leaves a stale, silently-orphaned entry forever — no warning, no diff. This is
the one genuine gap found in exploration (moves are already covered by
`--force`; renames are not safely detectable). Close it with a single opt-in
flag while keeping every existing non-destructive guarantee.

## Scope

### In Scope
- New opt-in `--prune-missing` flag on the existing `init` command.
- Compute `missing = existing_flows.keys() - discovered_flows.keys()` inside `merge_config`; remove those entries ONLY when `--prune-missing` is set.
- Confirm-gated deletion mirroring the existing comment-loss gate: interactive TTY → preview names + `typer.confirm`; non-interactive → require explicit `--yes` alongside `--prune-missing`, else preview to stderr and exit `2` (never silently delete, never silently no-op).
- `--json` reporting of removed flows via a new `flows_pruned` field; `init_v1` `SCHEMA_VERSION` 1→2 + contract-test update.
- Document that `--force` already covers the move/repoint case (same stem, new path).

### Out of Scope
- **A new `update` command** — explicitly rejected. A flag on `init` expresses this without duplicating discover/merge/serialize logic (`python-architecture` rule 1/3). A zero-logic `update` alias is a later naming-only decision, not this change.
- **Rename detection** (different stem) — not safely automatable: `appId` is bundle-wide (not a per-flow identity) and content-hash breaks once steps change (explore §1).
- **Content/hash-based identity tracking** — speculative complexity this project resists.
- Any `domain`/`application`/port/DB-schema change — directory glob + TOML text is a single-implementation CLI concern.
- Auto-pruning by default; making `--force` imply deletion.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `init`: add opt-in, confirm-gated `--prune-missing` deletion reconciliation (off by default) plus a new `flows_pruned` `--json` field (`init_v1` schema 1→2).

## Approach

All change is inside `init.py` + `init_v1.py`: (1) plumb `prune_missing: bool`
into `merge_config`, which computes the missing set and drops only those
entries when true; (2) gate the write in `init()` reusing the existing
comment-loss confirm/exit-2 pattern before `merge_config` runs; (3) pass
removed names to `build_init_payload` as `flows_pruned`. A missing local file
is NOT proof of permanent deletion (other branches/machines may still have it)
— hence opt-in + preview + confirm, consistent with every other guard in this
file. Follows the existing `init` composition pattern (`ctx.obj`, early
usage-error validation, try/except → exit mapping).

## Schema decision

`flows_skipped` (currently always `[]`, typed `list[{name,reason}]`) means
"discovered-but-not-added". A pruned flow is the opposite — an existing entry
removed — so overloading `flows_skipped` would conflate opposite actions and
mislead machine consumers. Add a distinct `flows_pruned: list[str]` (uniform
reason: source file gone). Adding a `--json` key IS a shape change → bump
`init_v1` `SCHEMA_VERSION` 1→2 and update `tests/contract/test_init_v1_contract.py`
(`perf-cli-standards` rule 6/8: a contract test must fail on any unversioned
shape change).

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/perf/cli/commands/init.py` | Modified | `--prune-missing` flag; missing-set compute in `merge_config`; confirm/exit-2 gate; wire `flows_pruned`. |
| `src/perf/contracts/init_v1.py` | Modified | New `flows_pruned` field; `SCHEMA_VERSION` 1→2. |
| `tests/contract/test_init_v1_contract.py` | Modified | Assert v2 shape incl. `flows_pruned`; fail on unversioned change. |
| `tests/unit/test_init_merge.py` | Modified | New missing/prune cases (none exist today). |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| User prunes a flow still live on another branch/machine | Med | Opt-in only + preview + confirm; never default; document the caveat. |
| Schema bump breaks existing `--json` consumers | Low | Additive field; bump documented; contract test enforces versioning. |
| `--prune-missing` mistaken as implying `--force` | Low | Independent flags — `--force`=overwrite, `--prune-missing`=delete-gate; neither ever silent. |

## Rollback Plan

Additive + gated: revert the branch. `--prune-missing` off by default means a
plain `init` behaves exactly as today; no DB-schema migration, no change to
`run`/`compare`/`budget-check` read/write paths.

## Dependencies

- Existing `merge_config` / `build_init_payload` / comment-loss-gate paths (SHIPPED). No new runtime library — stdlib + sanctioned `typer`.

## Success Criteria

- [ ] `init --prune-missing` removes ONLY entries whose flow file is gone; nothing else changes.
- [ ] Interactive: preview + confirm before delete; declining aborts (exit 2), never deletes.
- [ ] Non-interactive: `--prune-missing` without `--yes` previews to stderr + exits 2; with `--yes` prunes.
- [ ] Plain `init` (no flag) still never deletes — identical to today.
- [ ] `--json` reports `flows_pruned`; `SCHEMA_VERSION == 2`; contract test fails on unversioned shape change.
- [ ] Exit codes `0`/`2`/`3` only — never `1`; no domain/application/DB-schema change; existing tests pass.

## Open questions

1. **Dry-run `--json` on exit 2.** When non-interactive `--prune-missing`
   without `--yes` previews and exits 2, should it still emit the v2 payload
   (showing the would-prune set) for CI scriptability? **Default assumption:**
   yes — emit the v2 payload to stdout AND exit 2 so a machine sees what would
   be removed; spec to confirm the exact "pending/not-applied" marker.
2. **`flows_pruned` shape.** `list[str]` names vs `list[{name,reason}]`
   mirroring `flows_skipped`. **Default assumption:** `list[str]` (single
   uniform reason) per "no abstraction until it earns its place"; revisit only
   if a second prune reason ever appears.
