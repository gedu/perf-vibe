# Delta for `init` capability

**Grounded in**: proposal `docs/specs/prune-missing/proposal.md`; existing full spec `docs/specs/init-command/spec.md` (unchanged except the `init_v1` requirement below).

## ADDED Requirements

### Requirement: Stale Flow Pruning (`--prune-missing`)

The system SHALL support an opt-in `--prune-missing` flag on `init`. A flow is **missing** when its name exists as an `existing[flows].<name>` key but is NOT among this run's freshly `discover_flows`-ed candidate names. `merge_config` cannot and SHALL NOT distinguish WHY a name is missing (file deleted, renamed to a different stem, or moved outside `--flows-dir`) — all three are the identical, indistinguishable case from its point of view, and this spec treats them as one.

Without `--prune-missing`, the missing set SHALL NOT even be computed and every existing `[flows.*]` entry SHALL pass through untouched — behavior identical to today, zero risk.

With `--prune-missing` and a non-empty missing set, the system SHALL confirm-gate the deletion before writing: interactively (TTY, no `--yes`), it SHALL preview the missing flow names and prompt; declining SHALL abort with NO file written and exit `2`. Non-interactively with `--yes`, it SHALL prune immediately with no prompt and exit `0`. Non-interactively WITHOUT `--yes`, it SHALL NEVER silently prune or silently no-op: it SHALL compute the full merge (including the would-be-pruned set) without writing `perf.toml`, then exit `2`. If `--json` was requested, it SHALL emit the v2 `init_v1` payload (with `flows_pruned` set to the would-be-pruned names) to stdout as a scriptable preview; otherwise it SHALL print the would-be-pruned names to stderr, mirroring the existing non-interactive guard pattern.

An empty missing set SHALL make `--prune-missing` a no-op regardless of interactivity or `--yes`: exit `0`, `flows_pruned: []`, no prompt shown.

`--prune-missing` and `--force` are independent and composable: a discovered name can be simultaneously new/colliding (governed by `--force`) or missing (governed by `--prune-missing`), but never both for the SAME name — collision applies only to names discovery still finds; pruning applies only to names discovery no longer finds. Both effects SHALL apply together in the same run with no special-casing.

`--prune-missing`'s confirm-gate is independent of, but composes with, the existing comment-loss confirm-gate. If both guards apply in the same run (an existing `perf.toml` has hand-written comments AND has missing flows), the write SHALL proceed ONLY if both are satisfied (confirmed interactively, or waived via `--force`/`--yes` as applicable); declining or failing either SHALL abort with no write and exit `2`. Because the comment-loss guard runs FIRST (it is a precondition on rewriting the file at all) and is waived non-interactively by `--force` while the prune gate is waived by `--yes`, a non-interactive prune of a commented `perf.toml` SHALL require BOTH `--force` AND `--yes`; with only one, the first unsatisfied guard in order (comment-loss, then prune) SHALL emit its own message and exit `2`.

### Interaction with the zero-discovered-flows guard (safety)

`init`'s pre-existing usage-error guard — zero candidate flows discovered under `--flows-dir` exits `2` before any config is read or written — SHALL remain in force and SHALL take precedence over `--prune-missing`. Consequently `--prune-missing` CANNOT be used to empty a flows table down to zero entries: if `--flows-dir` currently contains no discoverable flows (all deleted, or a mistyped/wrong directory), the command SHALL exit `2` with the existing "no candidate flows discovered" error and prune NOTHING. This is deliberate: a glob that matches nothing is far more likely a wrong-path mistake than a genuine intent to delete every flow, and pruning-everything on an empty match would be an unrecoverable footgun. Pruning therefore only ever removes the subset of existing entries not matched by an otherwise-non-empty discovery.

### Preview payload completeness (non-interactive, no `--yes`)

When the non-interactive-without-`--yes` path emits the v2 `--json` preview before exiting `2`, the payload SHALL reflect the FULL would-be-written result — `flows_added`, `bundle_id`/`bundle_id_source`, `flows_total`, and `flows_pruned` all populated as they would be on a successful write — so a machine consumer sees the complete pending change, not only the deletions. The only difference from a `--yes` run is that `perf.toml` is NOT written and the exit code is `2`.

#### Scenario: Default (no flag) behavior is unchanged
- GIVEN an existing `perf.toml` has a flow entry whose file was deleted from `--flows-dir`
- WHEN `perfvibe init` runs WITHOUT `--prune-missing`
- THEN the stale entry is left untouched; no comparison against discovered flows even occurs

#### Scenario: Interactive confirm accepted prunes
- GIVEN a TTY, no `--yes`, and a non-empty missing set
- WHEN `perfvibe init --prune-missing` runs and the user confirms
- THEN the missing entries are removed, `perf.toml` is rewritten, and exit is `0`

#### Scenario: Interactive confirm declined aborts
- GIVEN the same setup
- WHEN the user declines the prompt
- THEN `perf.toml` is left unmodified and exit is `2`

#### Scenario: Non-interactive with --yes prunes immediately
- GIVEN a non-empty missing set, non-interactive mode, `--yes` passed
- WHEN `perfvibe init --prune-missing --yes` runs
- THEN the missing entries are removed with no prompt and exit is `0`

#### Scenario: Non-interactive without --yes previews and exits 2
- GIVEN a non-empty missing set, non-interactive mode, `--yes` NOT passed
- WHEN `perfvibe init --prune-missing --json` runs
- THEN `perf.toml` is NOT written, the v2 payload is emitted to stdout with `flows_pruned` listing the would-be-pruned names, and exit is `2`

#### Scenario: Zero missing flows is a no-op
- GIVEN every existing `[flows.*]` name is still discovered this run
- WHEN `perfvibe init --prune-missing` runs, interactive or not, `--yes` or not
- THEN no prompt is shown, `perf.toml` is unchanged in the flows-removal sense, `flows_pruned: []`, exit `0`

#### Scenario: Composes with --force in the same run
- GIVEN a discovered flow name collides with an existing entry AND a different existing entry is missing
- WHEN `perfvibe init --force --prune-missing --yes` runs
- THEN the colliding entry is overwritten AND the missing entry is removed, in the same write

#### Scenario: Composes with the comment-loss guard
- GIVEN the existing `perf.toml` has hand-written comments AND a non-empty missing set, interactive, no `--force`
- WHEN `perfvibe init --prune-missing` runs and the user declines EITHER prompt
- THEN no write occurs and exit is `2`

## MODIFIED Requirements

### Requirement: `init_v1` `--json` Output Contract

On success, `--json` SHALL emit a `schema_version`-tagged `init_v1` payload summarizing what was written: flows added, flows skipped/unchanged, flows pruned, the resolved `bundle_id` (if any), and the `perf.toml` path. `SCHEMA_VERSION` is `2`: it adds `flows_pruned: list[str]` (the flow names removed this run because `--prune-missing` determined their source file no longer exists — a single, uniform reason; no per-entry reason field) to the v1 shape, with no other field changed. A contract test MUST fail on any further shape change without a corresponding `schema_version` bump. The pretty (human) view SHALL remain lossy and MUST NEVER be parsed by tooling — only the `--json` payload is the machine contract.
(Previously: `SCHEMA_VERSION = 1`, no `flows_pruned` field, no pruning existed.)

#### Scenario: --json summarizes a successful write
- GIVEN a successful `init` run that adds two new flows
- WHEN `--json` is requested
- THEN the payload has `schema_version`, the list of added flow names, the resolved `bundle_id` (or `null`), and the written file path

#### Scenario: Pretty view is never the parse target
- GIVEN the same successful run without `--json`
- WHEN pretty output renders
- THEN it is human-readable confirmation text only, carrying no `schema_version` and not intended for parsing

#### Scenario: v2 payload reports flows_pruned
- GIVEN a run with `--prune-missing --yes` that removes one stale entry
- WHEN `--json` is requested
- THEN `schema_version` is `2` and `flows_pruned` is `["<removed-name>"]`

#### Scenario: No pruning still reports the v2 shape
- GIVEN a plain `init` run without `--prune-missing`
- WHEN `--json` is requested
- THEN `schema_version` is `2` and `flows_pruned` is `[]`

## Corner-Case Matrix (additions)

| # | Corner case | Behavior |
|---|---|---|
| P1 | `--prune-missing` omitted | zero comparison; identical to pre-flag behavior |
| P2 | Missing set non-empty, interactive, confirmed | entries removed, exit `0` |
| P3 | Missing set non-empty, interactive, declined | no write, exit `2` |
| P4 | Missing set non-empty, non-interactive, `--yes` | pruned immediately, no prompt, exit `0` |
| P5 | Missing set non-empty, non-interactive, no `--yes` | no write; v2 preview (`--json` to stdout, else names to stderr); exit `2` |
| P6 | Missing set empty | no-op regardless of mode; `flows_pruned: []`, exit `0` |
| P7 | `--prune-missing` + colliding name + `--force` | both effects applied in one write |
| P8 | `--prune-missing` + comment-loss guard, either declined | no write, exit `2` |
| P9 | `--prune-missing --yes` but `--flows-dir` discovers ZERO flows | pre-existing zero-flows guard wins: exit `2`, nothing pruned (cannot empty the table) |
| P10 | `--prune-missing` + commented `perf.toml`, non-interactive, only `--yes` (no `--force`) | comment-loss guard fires first: exit `2`, its own message, no prune |
| P11 | `--prune-missing` + commented `perf.toml`, non-interactive, both `--force` AND `--yes` | both guards waived: comments dropped AND missing entries pruned in one write, exit `0` |
| P12 | Non-interactive preview (no `--yes`) with new flows to add AND missing flows to prune | v2 payload shows BOTH `flows_added` and `flows_pruned` populated; no write; exit `2` |

**Invariant**: `--prune-missing` NEVER silently deletes without confirmation/`--yes`, NEVER silently no-ops when a preview was owed, and exit codes remain `0`/`2`/`3` only — never `1`.
