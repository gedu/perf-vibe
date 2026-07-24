# Specification: `init` capability (New)

**Grounded in**: proposal `docs/specs/init-command/proposal.md`, plus post-proposal design decisions on recursive discovery, mandatory `appId`, template-placeholder handling, and mismatch resolution (see proposal addendum in the orchestrator's launch context).

## Purpose

`perfvibe init` scaffolds (or safely merges into) a `perf.toml` `[flows]` table — plus a detected `bundle_id` — from a Maestro flows directory, so `perfvibe run <flow>` has a config-known flow to invoke without hand-writing TOML. Purely additive: it never touches `run`/`compare`/`budget-check` behavior, schema, or `load_config`'s read path; it only produces text that is valid input to the existing `_read_toml`/`_merge`/`_build_flows` path.

## Scope

| Concern | This capability |
|---|---|
| New `perfvibe init` command, registered in `main.py` | YES |
| Recursive flow discovery under `--flows-dir`, excluding `subflows/` path segments | YES |
| Mandatory `appId:` header parsing → `bundle_id` detection/reconciliation | YES |
| Interactive wizard (TTY, dim-placeholder default, accept/override) | YES |
| Non-interactive flags (`--yes`, `--bundle-id`, `--force`, `--flows-dir`) | YES |
| Safe merge into an existing `perf.toml` (new flow names; collision guard) | YES |
| `--json` machine-readable summary (`init_v1`) | YES |
| `AndroidManifest.xml`/`build.gradle` detection | OUT OF SCOPE |
| Writing `driver`/`sampler`/`marker_source` overrides | OUT OF SCOPE — code defaults already match a real Maestro setup |
| Any `domain/`/`application/`/schema/`load_config` change | OUT OF SCOPE (unchanged, read-compatible only) |

## Requirements

### Requirement: Recursive Flow Discovery

The system SHALL recursively discover flow files (`*.yaml`, `*.yml`, case-insensitive extension) under `--flows-dir`, EXCLUDING any file where a path segment (case-insensitive) equals `subflows`. Each remaining file SHALL become one candidate flow, named by its filename stem. Flows nested under other, non-`subflows`-named category directories SHALL still count as real top-level flows.

#### Scenario: Nested category directories are included
- GIVEN `e2e/flows/checkout/cold.yaml` exists
- WHEN `perfvibe init --flows-dir e2e/flows` runs
- THEN a candidate flow named `cold` is discovered

#### Scenario: subflows/ is excluded regardless of case or depth
- GIVEN `e2e/flows/subflows/login-fragment.yaml` and `e2e/flows/Subflows/util.yml` both exist
- WHEN discovery runs
- THEN neither file becomes a candidate flow

#### Scenario: Zero flows discovered is a usage error
- GIVEN `--flows-dir` contains only files under `subflows/`, or contains no `.yaml`/`.yml` files at all
- WHEN `perfvibe init` runs
- THEN the exit code is `2` and no `perf.toml` is written or modified

#### Scenario: Nonexistent or empty flows directory is a usage error
- GIVEN `--flows-dir` does not exist or is empty
- WHEN `perfvibe init` runs
- THEN the exit code is `2`

### Requirement: Mandatory appId Parsing

For each discovered flow file, the system SHALL parse the `appId:` key from the config header block (lines before the first `---` separator) via a tolerant line-scan — no YAML dependency. A concrete value SHALL be captured verbatim. A value matching the `${...}` template pattern (an env-var reference Maestro itself resolves at runtime) SHALL be treated as NOT a concrete detection — it SHALL NEVER be written into `perf.toml` as a literal `bundle_id`.

#### Scenario: Concrete appId is parsed
- GIVEN a flow header contains `appId: com.example.app`
- WHEN parsed
- THEN `com.example.app` is captured as that flow's concrete `appId`

#### Scenario: Templated appId is not a concrete detection
- GIVEN a flow header contains `appId: ${APP_ID}`
- WHEN parsed
- THEN that flow contributes NO concrete `appId` value (treated identically to "absent" for reconciliation)

#### Scenario: Missing or malformed header does not error by itself
- GIVEN a flow file has no `appId:` line, or has no `---` separator at all, or the header is otherwise unparsable
- WHEN parsed
- THEN the flow is still discovered (named by its filename stem) and simply contributes no `appId` signal; this alone is NOT a command-level error

### Requirement: Bundle ID Reconciliation

The system SHALL reconcile all concrete `appId` values found across discovered flows. A single distinct concrete value SHALL become the default/candidate `bundle_id`. Zero concrete values SHALL leave `bundle_id` undetected (no default). Two or more DIFFERENT concrete values SHALL be surfaced explicitly as a mismatch — interactively, by showing the conflicting values and prompting; non-interactively, by exiting `2` UNLESS `--bundle-id` is explicitly passed to resolve it.

#### Scenario: Single concrete value is the default
- GIVEN all flows with a concrete `appId` agree on `com.example.app`
- WHEN reconciliation runs
- THEN `com.example.app` becomes the candidate `bundle_id`

#### Scenario: Zero concrete values leaves bundle_id undetected
- GIVEN every flow's `appId` is absent, malformed, or templated
- WHEN reconciliation runs
- THEN no default `bundle_id` is proposed; `bundle_id` is written only if `--bundle-id` is explicitly passed (interactive mode may still prompt with no default)

#### Scenario: Mismatch — non-interactive without --bundle-id exits 2
- GIVEN two flows have different concrete `appId` values (multi-app monorepo)
- WHEN `perfvibe init --yes` (or non-TTY) runs without `--bundle-id`
- THEN the exit code is `2` and the conflicting values are listed on stderr

#### Scenario: Mismatch — non-interactive with --bundle-id resolves
- GIVEN the same mismatch
- WHEN `perfvibe init --yes --bundle-id com.example.app` runs
- THEN `com.example.app` is written as `bundle_id`, with no error

#### Scenario: Mismatch — interactive mode prompts
- GIVEN the same mismatch and a TTY, no `--yes`
- WHEN `perfvibe init` runs
- THEN the wizard shows the conflicting values and prompts the user to choose or enter one, before proceeding

### Requirement: perf.toml Writing and Merge Semantics

If no `perf.toml` exists, the system SHALL create one with `[flows.<name>]` entries (`maestro_path`) for every discovered flow, plus `bundle_id` if resolved. If `perf.toml` already exists, the system SHALL merge in genuinely new flow names, leaving existing `[flows.*]` entries untouched. A discovered flow NAME that already exists as a key in the file SHALL be refused (exit `2`) UNLESS `--force` is passed, in which case that entry SHALL be overwritten. Output SHALL always parse via `load_config`, and every scaffolded flow SHALL be config-known (`FlowConfig` round-trip).

#### Scenario: New perf.toml is created
- GIVEN no `perf.toml` exists in the project directory
- WHEN `perfvibe init --flows-dir e2e/flows` runs successfully
- THEN a `perf.toml` is created with a `[flows.<name>]` entry per discovered flow and (if resolved) a `bundle_id` key

#### Scenario: New flow names merge into an existing file
- GIVEN an existing `perf.toml` with `[flows.login]` already declared, and discovery finds `login` and a new `checkout`
- WHEN `perfvibe init` runs (no `--force` needed)
- THEN `[flows.checkout]` is appended and `[flows.login]` is left byte-for-byte unmodified

#### Scenario: Colliding flow name without --force is refused
- GIVEN an existing `perf.toml` already declares `[flows.login]`, and discovery also finds a flow named `login`
- WHEN `perfvibe init` runs without `--force`
- THEN the exit code is `2`, and the existing `perf.toml` is left unmodified

#### Scenario: Colliding flow name with --force overwrites
- GIVEN the same collision
- WHEN `perfvibe init --force` runs
- THEN `[flows.login]` is overwritten with the newly discovered `maestro_path`

#### Scenario: Round-trip guarantee
- GIVEN a `perf.toml` freshly written or merged by `init`
- WHEN `load_config` reads it
- THEN every scaffolded flow appears in `PerfConfig.flows`, and `MaestroDriver` accepts each flow name without error

### Requirement: Interactive Wizard vs Non-Interactive Mode

The system SHALL auto-detect non-interactive mode via TTY (`sys.stdin.isatty()` or equivalent). An explicit `--yes` flag SHALL force the non-interactive path even when a TTY IS present. In interactive mode, a single detected concrete `appId` SHALL be shown as a dim placeholder default; the user MAY accept it (blank input) or override by typing a different value. In non-interactive mode, no prompts SHALL be shown: `--bundle-id` wins if given, else the single auto-detected value is used, else `bundle_id` is left unset — subject to the mismatch rule above.

#### Scenario: TTY without --yes runs the wizard
- GIVEN stdin is a TTY and `--yes` is not passed
- WHEN `perfvibe init` runs
- THEN interactive prompts are shown

#### Scenario: --yes forces non-interactive even under a TTY
- GIVEN stdin is a TTY
- WHEN `perfvibe init --yes` runs
- THEN no prompts are shown; flags and detected defaults resolve every value

#### Scenario: Non-TTY auto-detects non-interactive
- GIVEN stdin is not a TTY (e.g. CI)
- WHEN `perfvibe init` runs without `--yes`
- THEN the command still runs fully non-interactively

#### Scenario: Wizard shows a dim placeholder default
- GIVEN a single concrete `appId` was detected
- WHEN the interactive wizard prompts for `bundle_id`
- THEN the detected value is shown as a dim, pre-filled default that Enter accepts as-is

### Requirement: `init_v1` `--json` Output Contract

On success, `--json` SHALL emit a `schema_version`-tagged `init_v1` payload summarizing what was written: flows added, flows skipped/unchanged, the resolved `bundle_id` (if any), and the `perf.toml` path. The pretty (human) view SHALL remain lossy and MUST NEVER be parsed by tooling — only the `--json` payload is the machine contract, per project convention.

#### Scenario: --json summarizes a successful write
- GIVEN a successful `init` run that adds two new flows
- WHEN `--json` is requested
- THEN the payload has `schema_version`, the list of added flow names, the resolved `bundle_id` (or `null`), and the written file path

#### Scenario: Pretty view is never the parse target
- GIVEN the same successful run without `--json`
- WHEN pretty output renders
- THEN it is human-readable confirmation text only, carrying no `schema_version` and not intended for parsing

### Requirement: Exit-Code Discipline

The tool SHALL exit `0` on a successful write or merge. The tool SHALL exit `2` on any usage error: zero flows discovered, an unresolved `appId` mismatch without `--bundle-id`, a flow-name collision without `--force`, or an invalid/empty `--flows-dir`. The tool SHALL exit `3` on a runtime/tooling failure (e.g. an unexpected I/O error writing `perf.toml`). The tool SHALL NEVER exit `1` — that code is reserved for `compare`/`budget-check` regressions.

#### Scenario: Successful write exits 0
- GIVEN a valid `--flows-dir` with discoverable flows and no unresolved conflicts
- WHEN `perfvibe init` runs
- THEN the exit code is `0`

#### Scenario: init never exits 1
- GIVEN any combination of the corner cases in this spec
- WHEN `perfvibe init` runs
- THEN the exit code is always one of `0`, `2`, or `3` — never `1`

## Corner-Case Matrix

`perfvibe init` SHALL handle every discovery, parsing, and merge corner case without crashing, and SHALL NEVER exit `1`.

| # | Corner case | Behavior |
|---|---|---|
| I1 | `--flows-dir` nonexistent or empty | usage error, exit `2` |
| I2 | Zero flow files after `subflows/` exclusion | usage error, exit `2` |
| I3 | All flows share one concrete `appId` | auto-detected default `bundle_id` |
| I4 | `appId` is `${VAR}` template on all flows | no concrete `bundle_id`; blank/prompt with no default |
| I5 | `appId` missing/malformed on some flows, concrete+consistent on the rest | detection succeeds from the concrete subset; missing ones are ignored, not errors |
| I6 | Concrete `appId`s differ across flows — interactive | wizard shows conflicting values and prompts |
| I7 | Same mismatch — non-interactive, no `--bundle-id` | usage error, exit `2` |
| I8 | Same mismatch — non-interactive, `--bundle-id` given | resolved; value written, exit `0` |
| I9 | No existing `perf.toml` | file created fresh with discovered flows + `bundle_id` |
| I10 | Existing `perf.toml`, only new flow names | merged in; existing entries untouched |
| I11 | Existing `perf.toml`, colliding flow name, no `--force` | usage error, exit `2`; file untouched |
| I12 | Existing `perf.toml`, colliding flow name, `--force` | entry overwritten; exit `0` |
| I13 | TTY present, no `--yes` | interactive wizard runs |
| I14 | TTY present, `--yes` passed | forced non-interactive despite TTY |
| I15 | Non-TTY, no `--yes` | auto-detected non-interactive |
| I16 | Round-trip: written `perf.toml` reloaded | `load_config` parses it; flows are config-known; `MaestroDriver` accepts them |
| I17 | Unexpected I/O failure writing the file | exit `3`, never silently `0` or `1` |

**Invariant**: `init` NEVER crashes, NEVER writes a partial/corrupt `perf.toml` on error, and NEVER exits `1`.
