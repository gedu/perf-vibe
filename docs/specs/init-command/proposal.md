# Proposal: `perfvibe init` command

## Intent

A QA/dev with a Maestro flows directory (e.g. `e2e/flows/*.yaml`) has no way to
bootstrap a `perf.toml` today. The `[flows]` table is undocumented and must be
hand-written before `perfvibe run <flow>` works — the `MaestroDriver` rejects any
flow not in `config.flows` (`driver_maestro.py:52`). `init` closes that first-run
gap: point it at a flows dir, get a working, committable `perf.toml`.

## Scope

### In Scope
- New `perfvibe init` Typer command in `src/perf/cli/commands/init.py` + `main.py` registration.
- Flow discovery: flat glob `*.yaml`/`*.yml` in `--flows-dir`; flow name = filename stem; write `[flows.<name>]` with `maestro_path`.
- `bundle_id` auto-detection: parse the mandatory `appId:` from each flow's config header (before `---`) — Maestro's own convention. Mismatched `appId`s across flows are surfaced, never silently resolved.
- Interactive wizard (default, TTY): detected `appId` shown as a dim placeholder default (Click `prompt(default=...)` + `style(dim=True)` — zero new deps); accept or override.
- Non-interactive/scriptable flags: `--flows-dir`, `--bundle-id`, `--driver`, `--db`, `--force`, `--yes` (skip prompts).
- Safe merge into an existing `perf.toml`: add genuinely-new flow names; refuse a colliding flow name (exit 2) unless `--force` (which overwrites).
- Round-trip guarantee: output parses via `load_config` and the scaffolded flow is config-known.

### Out of Scope
- Detection from `AndroidManifest.xml`/`build.gradle` (may not exist in a QA-only checkout; `appId` from the flow file is authoritative and free).
- Writing `driver`/`sampler`/`marker_source` — code defaults already match a real Maestro setup; keep the file minimal.
- CI-specific code path: `init` is a local, one-time command. CI just reads the committed `perf.toml` via the existing CWD/`--config` discovery.
- Documenting the `[flows]` table in README (separate, already-tracked doc gap; `init` makes it more urgent — recommend a follow-up note to commit the generated `perf.toml`).
- Any `domain/`/`application/`/port change — directory-glob + TOML text is a single-implementation CLI concern; a port would violate `python-architecture` rule 3.

## Capabilities

### New Capabilities
- `init`: scaffold/merge a `perf.toml` `[flows]` table from a Maestro flows directory, with `appId`-derived `bundle_id` and an interactive-or-scriptable UX.

### Modified Capabilities
- None. `run`/`compare`/`budget-check` behavior, schema, and `load_config` are untouched; `init` only produces text that is valid input to the existing `_read_toml`/`_merge`/`_build_flows` path.

## Approach

Pure CLI tooling. `init` (1) resolves `--flows-dir`, (2) globs flow files, (3) parses `appId:` from each header via a minimal line-scan (no YAML dependency — we only read one key before `---`), (4) reconciles bundle IDs (single → default; mismatch → prompt/error), (5) prompts or reads flags, (6) diffs against any existing `perf.toml` via `tomllib` to compute new-vs-colliding flow names, (7) writes hand-built TOML text through a quote/backslash-escaping helper. Follows the `run.py`/`budget_check.py` composition pattern (read `ctx.obj`, early usage-error validation, try/except → exit mapping).

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/perf/cli/commands/init.py` | New | Discovery, `appId` parse, wizard/flags, merge, TOML write. |
| `src/perf/cli/main.py` | Modified | Register `init` subcommand (wiring only). |
| `src/perf/config/loader.py` | Unchanged | Read compatibility already exists; `init` output must satisfy it (round-trip test). |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Hand-rolled TOML mis-escapes paths with quotes/backslashes | Med | Single escaping helper; round-trip test scaffold → `load_config`. |
| `appId` header parse brittle across flow styles | Med | Minimal, tolerant line-scan on the pre-`---` block; missing/multiple `appId` handled explicitly, never guessed. |
| Writing `bundle_id` — currently DEAD config (no consumer) | Low (accepted) | Deliberate: `appId` is free to capture and mismatch-detection is a real correctness guard; documented as reserved-but-inert, not speculative parsing effort. |
| Merge concatenation re-declares an existing `[flows.x]` (TOML error) | Med | Diff declared flow names via `tomllib` before appending; collision → exit 2 unless `--force`. |
| No real in-repo Maestro YAML to validate name/`appId` assumptions | Med | Treat "flow name = stem", "`appId` = bundle" as working conventions confirmed against the user's real flow files in spec. |

## Rollback Plan

Purely additive: one new command module + one registration line. No schema
migration, no change to any existing write/read path. Rollback = revert the
branch; `run`/`compare`/`budget-check` are untouched.

## Dependencies

- Existing `load_config`/`FlowConfig` read path (SHIPPED). No new runtime library — stdlib + sanctioned `typer`/Click.

## Success Criteria

- [ ] `perfvibe init --flows-dir <dir>` writes a `perf.toml` whose flows are immediately runnable (`load_config` sees them; `MaestroDriver` accepts them).
- [ ] `appId` is parsed from each flow header into `bundle_id`; mismatched `appId`s are surfaced, never silently picked.
- [ ] Interactive default shows the detected `appId` as an accept-or-override placeholder; `--yes`/flags run fully non-interactively.
- [ ] Existing `perf.toml`: new flows merge in; a colliding flow name exits 2 unless `--force`.
- [ ] Exit codes `0`/`2`/`3` only — never `1`; no `domain`/`application`/schema change; all existing tests pass.

## Proposal question round

Interactive SDD mode — these product questions should be confirmed before spec/design (executor cannot prompt directly):

1. **`bundle_id` write policy.** It is currently dead config (parsed, consumed nowhere). Confirm `init` should still write it. Assumption: yes — `appId` is free and mismatch-detection is valuable; documented as reserved-but-inert.
2. **`--json` for `init`.** `init` is a side-effecting command, not a query. Should it still honor global `--json` to emit a machine-readable summary of what it wrote (flows added/skipped, bundle_id, path)? Assumption: yes, a small `init_v1` summary payload, for scriptability parity with the rest of the CLI.
3. **`appId` mismatch in non-interactive mode.** Assumption: exit 2 (usage error) unless `--bundle-id` explicitly resolves it; interactive mode prompts to choose.
4. **Non-interactive detection.** Assumption: auto-detect via TTY, with `--yes` as an explicit override for CI/scripts. Confirm you want auto-TTY behavior rather than requiring an explicit flag.
5. **Recursive vs flat glob.** Assumption: flat (Maestro flow dirs are typically flat). Confirm no nested-dir discovery is needed for the real flow layout.
