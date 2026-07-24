# Design: `perfvibe init` command

## Technical Approach

`init` is **pure CLI-adjacent scaffolding**: point it at a Maestro flows dir, get a
committable `perf.toml` whose `[flows]` satisfy the existing `load_config`/`MaestroDriver`
read path. It performs local fs I/O (glob flow files, read headers, write TOML) — no device,
subprocess, git, or DB. It composes exactly like `run.py`/`budget_check.py`: read `ctx.obj`
(`output`, `config`), usage-error guards first, one `try/except` → exit `0/2/3` (never `1`),
`--json` dispatched through the shared `render_json`. Fulfils `spec.md`; realises the proposal.

## Architecture Decisions

| Decision | Choice | Rejected | Rationale |
|---|---|---|---|
| Where code lives | ALL logic in one module `cli/commands/init.py`, with the pure helpers (`parse_app_id`, `discover_flows`, `serialize_toml`, `merge_config`) as module-level functions imported directly by unit tests | New port + adapter; a `config/scaffold.py` module | Single implementation, single behavior — a reader/bug-fixer opens ONE file (`python-architecture` rule 1). A port for one impl violates rule 3. It is NOT domain (does fs I/O) nor application (no port orchestration); it is CLI tooling. |
| appId parse | Minimal stdlib **line-scan** of the pre-`---` header | PyYAML / new YAML dep | `perf-cli-standards` rule 9 (stdlib-first); we read ONE key, not a document. |
| Flow discovery | **Recursive** `rglob` for `*.yaml`/`*.yml`, EXCLUDING any file whose relative path has a segment `== "subflows"` (case-insensitive) | Flat glob; blanket recursive | Maestro `subflows/` are `runFlow` utilities, never top-level tests (official convention). Other nested category dirs are real flows. |
| TOML write | tomllib-**parse existing → deep-merge dict → full canonical re-serialize** by a hand-rolled `serialize_toml` | Blind text-append | Collision detection needs structural awareness; appending a top-level `bundle_id` after existing tables is INVALID TOML. Re-serialize is always-valid + deterministic. |
| String escaping | Prefer TOML **literal** strings `'…'` for `maestro_path` (no escaping — safe for `\`); fall back to **basic** `"…"` with `\`/`"` escaped only when the value contains `'` or a control char | Always basic-string escaping | Literal strings sidestep backslash-escaping for the common path case; basic-string fallback covers the rare quote-bearing path. |
| `--json` contract | New `contracts/init_v1.py`, `schema_version=1`, own `build_init_payload` | Reuse `json_v1` | `init` reports scaffolding actions, not a run — its own lean, independently contract-tested shape (mirrors `budget_check_v1`). |
| bundle_id write | Always write detected concrete `appId`; reserved-but-inert | Skip it | Free to capture; mismatch-detection is a real correctness guard (accepted dead config). |

## Data Flow

    init.py ─→ discover_flows(flows_dir)         # rglob, drop subflows/  (pure over a listing)
        └─→ parse_app_id(header_text) per flow    # line-scan → concrete | TEMPLATE | None
        └─→ reconcile bundle_id:
              single concrete  → default
              mismatch         → interactive: prompt to choose · non-interactive: exit 2 (unless --bundle-id)
              only ${…}/none   → "no value"; prompt (dim placeholder) or leave unset
        └─→ tomllib.load(existing perf.toml) → collision check on flow NAMES
              collide & not --force → exit 2
        └─→ merge_config(existing, new flows, bundle_id) → serialize_toml → write file
        └─→ --json: contracts.init_v1.build_init_payload · pretty: confirmation text
        └─→ exit 0 (2 usage / 3 runtime; NEVER 1)

## appId line-scan (exact algorithm)

Scan lines top-down. On a line whose `strip() == "---"` → **stop** (commands section begins).
On a line `strip().startswith("appId:")` → take the remainder, strip surrounding matching
quotes; if it contains `${` → return `TEMPLATE` sentinel ("no concrete value"); else return the
value. End-of-header with no match → `None`. Bound scanned lines/line-length; never `eval`.

## Interfaces / Contracts

`init_v1` payload (`schema_version=1`):

```json
{ "schema_version": 1, "config_path": "perf.toml", "bundle_id": "com.x",
  "bundle_id_source": "detected|flag|prompt|none",
  "flows_added": ["checkout"], "flows_skipped": [{"name":"login","reason":"exists"}],
  "flows_total": 2, "appid_conflict": null }
```

Typer signature — `init(ctx, flows_dir: Path = Argument(..., exists=True, file_okay=False),
--bundle-id, --driver="maestro" (written only if non-default), --out="perf.toml", --force,
--yes)`. Global `--json`/`--no-color`/`--db`/`--config` come from the callback. Interactive
(TTY and not `--yes`): `click.prompt(default=styled_dim(detected))`. Exit: `0` success; `2`
bad/empty flows-dir, appId mismatch non-interactive w/o `--bundle-id`, colliding flow name w/o
`--force`; `3` fs read/write or unexpected failure.

## File Changes

| File | Action | Description |
|---|---|---|
| `cli/commands/init.py` | Create | Discovery, appId parse, wizard/flags, merge, TOML write; exit 0/2/3. |
| `contracts/init_v1.py` | Create | `schema_version=1` `build_init_payload`. |
| `cli/main.py` | Modify | Register `init` (wiring only, mirrors `run`). |
| `tests/fixtures/flows/` | Create | Hand-crafted fake flow tree: concrete `appId`, `${APP_ID}` template, missing `appId`, a `subflows/util.yaml` (must be excluded), a nested real flow. None exist in-repo today. |
| `config/loader.py` | Unchanged | Round-trip target — init output must satisfy `load_config`. |

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit | `parse_app_id`: concrete, quoted, `${…}` template, missing, stop-at-`---` | table cases, pure |
| Unit | `discover_flows`: recursive, excludes `subflows/` segment (case-insensitive), keeps nested real flows | fixture tree |
| Unit | `serialize_toml`: quote/backslash escaping, literal-vs-basic choice; `merge_config` collision detection | round-trip via `tomllib.loads` |
| Contract | `init_v1` `--json` shape — fails on unversioned change | `tests/contract/` |
| Integration | Full `perfvibe init` via `CliRunner` on the fixture dir → `load_config` sees the flows (round-trip); merge into existing file adds new / rejects collision unless `--force`; exit 0/2/3, never 1; mismatch → exit 2 non-interactive | real fs (`tmp_path`), no device |
| Golden | pretty confirmation, color forced off | `--update-golden` |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, or process integration. Input safety
still applies: bounded line-scan, `json.loads`/`tomllib` only, never `eval`/`exec`, subprocess,
or execution of any read file (`perf-cli-standards` rule 5).

## Migration / Rollout

No migration. Purely additive: one command module + one contract module + one registration
line. `run`/`compare`/`budget-check`, the schema, and `load_config` are untouched. Rollback =
revert branch.

## Open Questions

- [x] `--out` target vs reusing global `--config` as the write path — **Resolved** (tasks.md decision #2): no new `--out` flag. `init` reuses the existing global `--config` option as its write path; defaults to `./perf.toml` in CWD when `--config` is omitted, matching `_find_project_config`'s CWD-only discovery. Implemented in `init.py`'s output-path resolution; covered by `tests/integration/test_cli_init.py`'s 3.6 output-path-resolution tests.
- [x] Comment loss: re-serializing an existing `perf.toml` drops comments (tool-managed file, accepted). — **Resolved** (tasks.md decision #3): never silent. `has_comments()` detects a `#` outside string literals; if the target file has one, `init` requires explicit confirmation (interactive, via `_render_comment_loss_confirm_prompt`) or `--force` (non-interactive/scripted, exit `2` otherwise with `_render_comment_loss_error`'s text) before overwriting. Covered by `tests/integration/test_cli_init.py`'s 3.4 comment-guard tests and golden-pinned in `tests/golden/test_init_pretty_golden.py`.
