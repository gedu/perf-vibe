# Configuring flows

`perfvibe` reads which Maestro flows exist, and where their `.yaml` files live, from a
`perfvibe.toml` config file's `[flows]` table — one `[flows.<name>]` sub-table per
flow, pointing at that flow's `maestro_path`:

```toml
bundle_id = "com.example.app"

[flows.checkout]
maestro_path = "flows/checkout.yaml"

[flows.login]
maestro_path = "flows/login.yaml"
```

You can hand-write this table. But `perfvibe init <flows-dir>` scaffolds or merges it
for you: it recursively scans a Maestro flows directory (skipping any `subflows/` —
those are `runFlow` utilities, never top-level flows), detects a single consistent
`appId:` header across the flows as your `bundle_id`, and writes (or safely merges
into) `perfvibe.toml`.

```bash
perfvibe init tests/fixtures/flows --yes --bundle-id com.example.app
```

See `perfvibe init --help` for the full flag list: `--driver`, `--db`, `--bundle-id`,
`--force`, `--yes`, `--prune-missing`.

## Adding flows later

Re-run the same `perfvibe init <flows-dir>` command — it re-scans the whole directory
and **merges in any genuinely new flow names, leaving existing entries untouched.**
Because `perfvibe.toml` is a plain committed file, `git diff perfvibe.toml` right
after running it is your review of what changed.

Note this is **add-only**: if an *existing* flow's file moved or you want to update its
`maestro_path`, a plain re-run won't touch that entry — pass `--force` to overwrite it
(which overwrites *every* colliding name in that run, not just one).

## Removing or renaming a flow file (`--prune-missing`)

By default `init` **never deletes** — a stale `[flows.<name>]` entry whose file is
gone from `<flows-dir>` is left untouched forever. Pass `--prune-missing` to opt in to
reconciliation: it removes exactly the `[flows.*]` entries no longer matched by this
run's discovery.

It is confirm-gated the same way the comment-loss guard is:

- **Interactively** it previews the names and prompts before deleting.
- **Non-interactively** (e.g. in a script) it needs `--yes`, or it previews the
  would-be-pruned names (to stdout as `--json`, else to stderr) and exits `2` without
  writing — so it never silently deletes and never silently no-ops.

```bash
perfvibe init tests/fixtures/flows --yes --prune-missing
```

It composes with `--force` (in the same run, one name can be new/colliding while a
different name is missing) and with the comment-loss guard (a commented `perfvibe.toml`
pruned non-interactively needs **both** `--force` and `--yes`, since each guard is
waived independently).

It can never empty the flows table to zero: if `<flows-dir>` discovers no flows at all,
the pre-existing "no candidate flows discovered" usage error wins and nothing is pruned —
a glob that matches nothing is treated as a wrong-path mistake, not an intent to delete
everything.

## The comment-loss guard

Re-serializing `perfvibe.toml` always drops hand-written comments — this tool refuses
to do that silently. If your `perfvibe.toml` contains comments, an operation that would
rewrite the file (overwriting a colliding flow, pruning) requires `--force` to proceed.

## CI should read a committed `perfvibe.toml`, not regenerate one

Run `perfvibe init` **locally** once, review the diff, and commit the resulting
`perfvibe.toml` alongside your Maestro flows — the same way you'd commit any other
config file. `run` / `compare` / `budget-check` in CI then read that committed file
directly; **there is no `init` step in the CI pipeline itself.**

This keeps the set of flows CI measures explicit and reviewable in the PR diff, rather
than implicitly whatever `init` happens to (re-)discover on a CI runner.
