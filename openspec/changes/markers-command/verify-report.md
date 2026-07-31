# Verify Report — markers-command (Phase 1 / PR1 slice ONLY)

**Verdict: PASS WITH FINDINGS** (0 CRITICAL, 2 WARNING, 3 SUGGESTION)

Scope verified: Phase 1 / PR1 only — `PERF_TAG` promotion + `classify_line`
extraction + `parse()` behavior-neutral refactor. Phases 2-4 (contracts, CLI
sub-app, docs) are out of scope and correctly untouched.

## Executive summary

`parse()`'s "behavior-neutral" claim HOLDS: the `perf_lines_seen` accounting
is faithful to the pre-refactor code for every flagged drift path, and the
full `MarkerParseResult` (markers, partial_coverage, diagnostic) is
byte-identical. All quality gates are green. No CRITICAL issues. The findings
are (a) one genuine characterization-test coverage gap on the empty-`[PERF]`
payload diagnostic path, (b) a real spec/design contradiction about oversized
reason-attribution that Phase 3 (`doctor`) must resolve, and (c) low-value
coverage/edge notes. Nothing blocks archive of the Phase 1 slice.

## Real evidence (this run)

| Gate | Command | Result |
|---|---|---|
| Focused tests | `pytest -q tests/integration/test_markers_adb_logcat.py tests/unit/test_markers.py` | **53 passed** in 0.15s |
| Full suite + coverage | `pytest -q --cov=perf` | **882 passed** in 6.87s; TOTAL coverage **94.96%** (gate fail_under=93, branch on) — reached |
| Adapter per-file coverage | `--cov-report=term-missing` | `markers_adb_logcat.py` **99%** (104 stmts, 1 miss + 1 branch part at line 147) |
| Lint | `ruff check .` | All checks passed! |
| Format | `ruff format --check .` | 121 files already formatted |
| Types | `mypy src/perf` | Success: no issues found in 53 source files |

Line 147 miss = the `not isinstance(data, dict)` defensive branch in
`_classify_json_payload`. CONFIRMED unreachable and pre-existing (a `{`-prefixed
string that `json.loads` accepts can only be a dict); identical dead-defensive
line existed in the pre-refactor code. Not a regression.

## Spec conformance (Phase-1-relevant requirements)

| Requirement | Status | Evidence |
|---|---|---|
| Shared `PERF_TAG` constant, no second tag string | PASS | `PERF_TAG = "[PERF]"` at `model.py:277`; adapter imports it (`markers_adb_logcat.py:53`), local `_PERF_TAG` deleted. `grep` confirms exactly ONE `"[PERF]"` literal in `src/`; all other `[PERF]` occurrences are docstring/diagnostic prose, not tag-matching logic. Producer (snippet) side lands in Phase 3. |
| Shared line-classification function (additive, single owner) | PASS | Public `classify_line(raw_line) -> LineVerdict`, `LineKind(StrEnum)`, `LineVerdict(frozen)`, `REASON_*` constants added. `parse()` delegates every per-line decision to `classify_line`. |
| `parse()`'s contract unaffected (signature/inputs/output) | PASS | Signature `parse(lines, *, iterations)` and `MarkerParseResult` shape unchanged; characterization test pins byte-identical output. |
| Diagnosis categories map to `LineKind`/reasons | PASS (Phase-1 scope) | COMPLETED / MARK_START / PERF_META / IGNORED / FAILURE(malformed_text|invalid_json|invalid_value|oversized) all implemented and unit-tested. |

## Adversarial results (the drift hunt)

### 1. `perf_lines_seen` accounting drift — NO DRIFT (highest priority)

Line-by-line diff of old vs new confirms the counted set is IDENTICAL. New
`parse()` counts a line iff `kind in {COMPLETED, MARK_START}` or
`(kind is FAILURE and reason != REASON_OVERSIZED)`; excludes PERF_META,
IGNORED, and oversized. This exactly reproduces the old control flow where the
`perf_lines_seen += 1` sat AFTER the oversized/PERF-META/no-tag/empty-payload
`continue`s and covered COMPLETED/markStart/malformed-text/invalid-json/
invalid-value.

- (a) oversized line CONTAINING `[PERF]`: old `continue`d before the tag was ever found → not counted. New returns `FAILURE/OVERSIZED` → excluded → not counted. **MATCH. Pinned by characterization case `oversized-line`** (its golden diagnostic is the `perf_lines_seen==0` message, which would flip if oversized were counted — so this genuinely distinguishes the drift).
- (b) `[PERF]` with empty/whitespace-only payload: old `if not payload: continue` fired BEFORE `+= 1` → not counted. New returns `IGNORED` → not counted. **MATCH. NOT pinned by the characterization golden — see Finding W1.**
- (c) `[PERF-META]` line: old `continue`d on the meta check → not counted. New returns `PERF_META` → not counted. **MATCH. Pinned by characterization case `perf-meta-line`.**

### 2. Characterization coverage of the drift paths

The 12 golden cases DO cover the two most dangerous drift siblings: the
oversized case's input `"[PERF] checkout: " + "9"*20000 + "ms"` genuinely
CONTAINS `[PERF]` (so it exercises "oversized-but-tagged not counted"), and the
PERF-META case pins the `perf_lines_seen==0` diagnostic. The empty-`[PERF]`
payload path is the one drift sibling NOT in the golden set (Finding W1).

### 3. `classify_line` check-ordering — REPRODUCED EXACTLY

Order is oversized-bound → PERF-META → tag `.find` → empty-payload → `{`/JSON →
`markStart` → text, matching the original. Ambiguous probes all behave
identically old/new: oversized+`[PERF]` → oversized wins; `[PERF-META]`+`[PERF]`
on one line → PERF_META wins (skip); `{`-prefixed invalid JSON → invalid_json
FAILURE (counted); text with a colon in the name (`foo:bar: 5ms`) → text regex
stops name at first `:`, remainder non-numeric → malformed_text. No divergence.

### 4. Known deviation (oversized handled inside the classifier) — HARMLESS in Phase 1, real Phase-3 question

`classify_line` performs the `_MAX_LINE_LENGTH` bound-check internally and
returns `FAILURE/REASON_OVERSIZED`, whereas spec.md says oversized is "skipped
before reaching the classifier and NOT reason-attributed." For `parse()` this
is provably harmless: the oversized verdict is excluded from `perf_lines_seen`
and yields no marker, so `parse()` output is byte-identical (case `oversized-line`
proves it). It does NOT change Phase 1 behavior. See Finding W2 — it exposes a
spec-vs-design contradiction Phase 3 must resolve.

### 5. Corner cases beyond spec/tests

- Logcat PREFIX before `[PERF]` (e.g. `... I ReactNativeJS: [PERF] x: 5ms`): handled correctly via `line.find` (unchanged from original), but UNTESTED — see Finding S1. This is the most production-representative path (real `adb logcat` always prefixes).
- Doubled tag on one line (`[PERF] a: 1ms [PERF] b: 2ms`): first `[PERF]` wins; trailing text breaks the `\s*$` anchor → whole line is malformed_text, no marker. Identical old/new; no drift — Finding S2.
- Valid JSON non-object (`[PERF] [1,2]`): doesn't start with `{` → text path → malformed_text. Tested (unit `no-colon-not-json-shaped`). `not isinstance(dict)` branch is genuinely unreachable dead-defensive code (line 147, the single miss) — Finding S3, no action.
- Value with leading `+`, unit defaulting, unicode/whitespace name: all behave as the unchanged regex dictates; consistent old/new.

## Findings (ranked, most severe first)

- **W1 — WARNING (test-coverage gap; behavior is CORRECT).** The empty/whitespace-only `[PERF]` payload `perf_lines_seen` path — flagged HIGHEST PRIORITY — is NOT pinned by a full-`MarkerParseResult` golden. `test_empty_perf_payload_is_skipped` asserts `markers == ()` ONLY, never the diagnostic. Concrete unguarded input: `parse(["[PERF]   "], iterations=3)` — I verified it currently yields the `perf_lines_seen==0` diagnostic (correct), but if a future refactor made empty payload count, the diagnostic would silently flip to "saw 1 `[PERF]` line(s)..." and BOTH the empty-payload test and the 12-case characterization would still pass green. Recommend adding the empty-`[PERF]` line to `test_characterization_parse_output_is_pinned`. Not a bug today; an unguarded regression surface.
- **W2 — WARNING (spec/design contradiction; Phase-3 concern).** spec.md "Diagnosis Categories" says oversized is "skipped before the classifier, no reason attributed," while design.md (Interfaces/Contracts, `parse_failures[].reason`) lists `oversized` as a valid reason, and the implementation defines `REASON_OVERSIZED` returned by `classify_line`. Harmless to Phase-1 `parse()`, but `markers doctor` (PR3) MUST decide whether an oversized line appears in `parse_failures` (design) or is dropped reason-less (spec). Flag for sdd-apply PR3 / sdd-spec reconciliation. Does not block Phase 1 archive.
- **S1 — SUGGESTION (coverage gap).** No test or fixture line carries a realistic logcat prefix before `[PERF]`; the `tag_index > 0` slicing branch is unexercised despite being the real-world path. Behavior correct/unchanged. Add a prefixed-line case (ideally when PR3 `doctor` lands, since `doctor` consumes real capture buffers).
- **S2 — SUGGESTION (inherent limitation, not a drift).** Doubled `[PERF]` on one line classifies as a single malformed_text line. Identical old/new; neither spec nor tests consider it. Note only.
- **S3 — SUGGESTION (no action).** `not isinstance(data, dict)` at line 147 is unreachable dead-defensive code (sole uncovered line, 99%); confirmed pre-existing, not introduced by this refactor.

## Task completeness

All 5 Phase 1 tasks (1.1-1.5) checked `[x]` in tasks.md and match code state:
`PERF_TAG` promoted (model.py:277), local `_PERF_TAG` deleted, characterization
test added (12 cases), `classify_line`/`LineKind`/`LineVerdict`/`REASON_*`
extracted with `parse()` delegating, and `tests/unit/test_markers.py` created
(18 tests). Phases 2-4 correctly remain unchecked/out of scope.

## Verdict

**PASS WITH FINDINGS.** The Phase 1 / PR1 slice is behavior-neutral for
`parse()`, fully gated (tests + lint + format + types green, coverage 94.96%),
and matches spec/design/tasks for its scope. Recommend proceeding — carry W1
(add empty-payload golden) and W2 (oversized reason-attribution) forward into
the PR3 work rather than blocking archive of this slice.


---

# Verify Report — markers-command (Phase 2 / PR2 slice ONLY)

**Verdict: PASS WITH FINDINGS** (0 CRITICAL, 2 WARNING, 2 SUGGESTION)

Scope verified: Phase 2 / PR2 only — two NEW pure builder modules
(`contracts/markers_snippet_v1.py`, `contracts/markers_doctor_v1.py`) + their
contract tests. No CLI/adapter/domain files touched this batch. Phases 3-4
(CLI sub-app, docs) out of scope and correctly untouched.

## Executive summary

Both builders match design.md's `--json` shapes EXACTLY, both `SCHEMA_VERSION=1`,
and the contract tests guard schema drift at every nesting level with exact
key-set `==` assertions (stricter than the sibling `init_v1` test). All quality
gates green. The one real risk — forward-coherence, i.e. whether PR3's `doctor`
command can honestly FILL this pre-pinned contract from PR1's `classify_line`/
`parse()` outputs — is **CONFIRMED FILLABLE with zero contortion**: every
builder parameter maps cleanly to a real classifier output. Two forward
WARNINGs (both PR3 concerns, neither a PR2 defect): `coverage_ok` degenerates
semantically in stdin mode, and the oversized-in-`parse_failures` spec/design
contradiction (carried from Phase 1's W2) is now PINNED by the doctor contract
test. Nothing blocks archive of the PR2 slice.

## Real evidence (this run)

| Gate | Command | Result |
|---|---|---|
| Focused tests | `pytest -q tests/contract/test_markers_snippet_v1_contract.py tests/contract/test_markers_doctor_v1_contract.py` | **18 passed** in 0.07s (6 snippet + 12 doctor) |
| Full suite + coverage | `pytest -q --cov=perf` | **901 passed** in 6.78s; TOTAL coverage **94.98%** (gate fail_under=93, branch on) — reached |
| Per-file coverage | `--cov-report=term-missing` | `markers_snippet_v1.py` **100%** (6 stmts, 0 miss); `markers_doctor_v1.py` **100%** (12 stmts, 0 miss) |
| Lint | `ruff check .` | All checks passed! |
| Format | `ruff format --check .` | 125 files already formatted |
| Types | `mypy src/perf` | Success: no issues found in 55 source files |

## Priority 1 — Forward-coherence (the real risk): PR3 CAN fill this contract cleanly

Contract-first pin verified against PR1's actual classifier surface
(`classify_line -> LineVerdict{kind, marker, reason}`, the `REASON_*` constants,
and `parse() -> MarkerParseResult{markers, partial_coverage, diagnostic}`).
Field-by-field, `build_doctor_payload`'s parameters map to real outputs with
NO contortion:

| Builder param | PR3 source | Coherent? |
|---|---|---|
| `parsed: Sequence[Marker]` | `[v.marker for v in verdicts if v.kind is COMPLETED]` — COMPLETED verdicts carry `.marker` (domain `Marker{name, value: float, unit}`) | YES — direct |
| `parse_failures: Sequence[tuple[str,str]]` | `[(raw, v.reason) for raw, v in ... if v.kind is FAILURE]`; `v.reason` is exactly one of `REASON_MALFORMED_TEXT/INVALID_JSON/INVALID_VALUE/OVERSIZED` | YES — and the contract test imports those SAME constants from the adapter, so drift is structurally impossible |
| `mark_start_without_end` / `perf_meta` / `ignored` | counts of `MARK_START` / `PERF_META` / `IGNORED` verdicts | YES — direct |
| `lines_scanned` | `len(buffer)` | YES |
| `diagnostic` | `MarkerParseResult.diagnostic` verbatim | YES — direct |
| `coverage_ok` | `bool(parsed) and not partial_coverage` (design.md), `partial_coverage` from `parse()` | Derivable — but see W-A |

Conclusion: the shape does NOT fight the classifier. The `parse_failures`
reason vocabulary is the single source of truth already owned by `classify_line`
(PR1), and `parsed` is populated straight from COMPLETED verdicts' `.marker`.
This is a clean contract-first pin, not one PR3 will have to bend to satisfy.

## Priority 2 — Schema conformance (literal design comparison)

- `build_snippet_payload` → exactly `{schema_version, lang, code}`. Matches
  design.md `markers_snippet_v1: { "schema_version": 1, "lang": "ts"|"js",
  "code": "<snippet>" }` EXACTLY. `SCHEMA_VERSION=1`. **PASS.**
- `build_doctor_payload` → exactly `{schema_version, mode,
  input_summary:{lines_scanned}, breakdown:{parsed:[{name,value,unit}],
  mark_start_without_end, perf_meta, parse_failures:[{line,reason}], ignored},
  coverage_ok, diagnostic}`. Field names + nesting match design.md's unified
  JSON block LITERALLY (verified key-by-key). `SCHEMA_VERSION=1`. **PASS.**

## Priority 3 — Contract-test quality

- **Drift guards are exact-set, not presence-only.** snippet:
  `set(payload.keys()) == set(_REQUIRED_KEYS_AND_TYPES)`. doctor pins the full
  set at EVERY level — top-level, `input_summary`, `breakdown`, per-item
  `parsed` (`{name,value,unit}`), per-item `parse_failures` (`{line,reason}`) —
  all with `==`. This is STRICTER than the sibling `test_init_v1_contract.py`
  (which used `.issubset()`), so any additive field now forces a
  `SCHEMA_VERSION` bump — the desired discipline. **PASS.**
- **"line vs stdin identical key set" IS tested**
  (`test_mode_line_produces_the_same_shape_as_mode_stdin`) — asserts equal
  top-level and equal `breakdown` keys across both modes, directly proving the
  spec's "ONE coherent schema ... not two competing shapes." Note: at the
  builder level this is near-tautological (the builder never branches on
  `mode`); the CLI-level identical-keys guarantee across real captures lands in
  PR3's `test_cli_markers.py`. Adequate for the PR2 contract. **PASS.**
- **Reason strings imported from the adapter**, never redefined
  (`REASON_MALFORMED_TEXT/INVALID_JSON/INVALID_VALUE/OVERSIZED` from
  `perf.adapters.markers_adb_logcat`). **PASS.**
- **No-unexpected-keys guard**: the exact-set `==` assertions serve as the
  no-extra-keys guard. No explicit "no secrets" test, but N/A for a pure
  builder over caller-provided data (the sibling contract tests omit it too).

## Priority 4 — Convention fit (vs `init_v1.py` sibling)

Both modules mirror the `init_v1.py` pattern: module docstring citing SKILL
rules 6 & 8 + the design shape; `__all__`; module-level `SCHEMA_VERSION`; pure
builder, no I/O; private `_*_payload` shaping helpers exactly like init_v1's
`_flows_skipped_payload`. `markers_doctor_v1` imports `Marker` from
`domain/model` for typing only — layer-clean (domain is the innermost layer;
contracts depending on it is fine). **PASS.**

## Findings (ranked, most severe first)

- **W-A — WARNING (forward / PR3 semantics; not a PR2 defect).** `coverage_ok`
  is mechanically derivable in both modes, but its MEANING degenerates in stdin
  mode. The design treats a stdin buffer as one capture (`iterations=1`), so
  `partial_coverage = len(markers) < 1` and `coverage_ok = bool(parsed) and not
  partial_coverage ≡ bool(parsed)` — i.e. "found at least one marker," NOT a
  coverage ratio. Meanwhile spec Non-Goals explicitly rejects "`doctor` as a
  CI/coverage gate" and the stdin scenario is "INFORMATIONAL (no pass/fail
  gate)." So a consumer could misread `coverage_ok` as a gate the spec says
  does not exist. PR3 CAN fill it honestly (it is just `bool(parsed)` in
  stdin), but PR3/docs should document `coverage_ok` as informational, not a
  gate. This is the honest answer to the #1 concern: the shape does not fight
  the classifier, but `coverage_ok` imports a single-capture "coverage" concept
  into a mode where it collapses. Does not block PR2 archive.
- **W-B — WARNING (spec/design contradiction; carries Phase 1's W2 forward,
  now PINNED by the PR2 contract).** `classify_line` returns
  `FAILURE/REASON_OVERSIZED`, and `test_oversized_lines_use_the_same_reason_vocabulary`
  PINS `oversized` as a valid `parse_failures[].reason`. This matches design.md's
  reason enum but CONTRADICTS spec "Diagnosis Categories" ("Oversized ... skipped
  before the classifier, NOT reason-attributed"). Because `doctor` will iterate
  the single-source `classify_line`, PR3 following the classifier faithfully
  WILL land oversized lines in `parse_failures` — and echo the verbatim
  >4096-char line into `parse_failures[].line`, bloating the JSON with garbage.
  PR3 must reconcile: (a) drop `OVERSIZED` verdicts from `parse_failures` per
  spec, or (b) keep them per design/contract (ideally truncating the echoed
  line). The PR2 contract is internally consistent with design; the spec prose
  is the outlier. Reconciliation is a PR3 / sdd-spec concern, not a PR2 defect.
  Does not block PR2 archive.
- **S1 — SUGGESTION (weak assertion, harmless).**
  `test_mode_line_produces_the_same_shape_as_mode_stdin` is near-tautological at
  the builder layer (the builder does not branch on `mode`). It still guards
  the contract; the meaningful cross-mode-identical-keys proof belongs to PR3's
  CLI integration test. No action for PR2.
- **S2 — SUGGESTION (apply-progress bookkeeping, immaterial).** The
  apply-progress entry said "13 doctor tests / 19 new" then self-corrected to
  18 collected; the arithmetic "882 + 19 = 901" is loose (12 doctor + 6 snippet
  = 18 new). Verified real numbers this run: 18 focused passing, 901 full-suite
  passing, both modules 100% covered. No action.

## Task completeness

All 4 Phase 2 tasks (2.1-2.4) checked `[x]` in tasks.md and match code state:
`markers_snippet_v1.py` (`build_snippet_payload`), its contract test (6 tests),
`markers_doctor_v1.py` (`build_doctor_payload` with `_parsed_payload` /
`_parse_failures_payload` helpers), its contract test (12 tests). Phases 3-4
correctly remain unchecked/out of scope.

## Verdict

**PASS WITH FINDINGS.** The Phase 2 / PR2 slice pins both `--json` contracts to
design.md exactly, is fully gated (18 focused + 901 full-suite passing, both
modules 100% covered, lint/format/types green, coverage 94.98%), and matches
spec/design/tasks for its scope. The forward-coherence risk is retired: PR3 can
fill `build_doctor_payload` cleanly from `classify_line`/`parse()` outputs with
no contortion. Recommend proceeding to archive — carry W-A (`coverage_ok` stdin
semantics) and W-B (oversized reason-attribution, = Phase 1 W2) forward into PR3
rather than blocking this slice.


# Verify Report — markers-command (Phase 3 / PR3 slice ONLY)

Adversarial verify of the heaviest, user-facing slice: first nested Typer in
the repo + the copy-paste instrumentation snippet. Scope: `commands/markers.py`,
`main.py` add_typer wiring, and the three test suites (integration/contract/unit).

## Gates (REAL output, full run)

- `./.venv/bin/ruff check .` -> **All checks passed!**
- `./.venv/bin/ruff format --check .` -> **128 files already formatted**
- `./.venv/bin/mypy src/perf` -> **Success: no issues found in 56 source files**
- `./.venv/bin/pytest -q --cov=perf` -> **928 passed; coverage 95.01%** (gate fail_under=93)

All green. Matches apply-progress exactly (928 passed / 95.01%).

## Spec/tasks/apply conformance

Tasks 3.1-3.6 all `[x]` and genuinely implemented; Phase 4 (docs) correctly
untouched. `coverage_ok` reconciliation verified conformant (see below). ctx.obj
propagation (design #1 risk) verified live. Every spec Requirement in scope has a
passing behavioral match. Verdict: **PASS-WITH-FINDINGS** — gates green, spec
conformant, tests solid; one HIGH/CRITICAL fidelity finding on the snippet that
should be reconciled BEFORE Phase 4 bakes it into the README.

## Findings (most severe first)

### C-1 (CRITICAL — fidelity deviation + correctness risk) — snippet diverges from the user's proven-working module on TWO counts

The emitted `render_snippet('ts'/'js')` is an ORIGINAL variant, not a mirror of
the user's actual `react-native-performance` module. Two divergences carry real
runtime risk in copy-paste code (the whole product of this slice):

1. **Import style changed from default to named.**
   - User (proven working): `import performance from 'react-native-performance';`  (DEFAULT import)
   - Emitted snippet:          `import { performance } from 'react-native-performance';`  (NAMED import)
   `react-native-performance`'s documented public API is the DEFAULT export
   (`import performance from '...'`). If the installed version does not ALSO
   expose a named `performance` export, `{ performance }` binds `undefined` and
   the first `performance.mark(...)` throws "undefined is not an object" at
   runtime. (context7 was not reachable in this executor to confirm the exact
   export map across versions; regardless, deviating from the user's
   known-good default import introduces risk for zero benefit.)

2. **Dropped the user's defensive try/catch around `performance.measure`.**
   - User: wraps `performance.measure(...)` in try/catch, `console.warn`s on failure.
   - Emitted snippet: `measureMark` calls `performance.measure(name, ...)` with
     NO guard. Per the W3C User Timing spec that RN-performance implements,
     `measure()` THROWS when a referenced start/end mark is absent — exactly the
     `markStart`-without-`markEnd` case (crash / early-exit mid-flow) the parser
     and spec explicitly care about. The emitted snippet therefore throws an
     UNHANDLED exception in that scenario, where the user's module degrades
     gracefully. This is a robustness regression baked into copy-paste code.

Faithful/defensible parts (no action): the emitted `console.log` shape is
EXACTLY `[PERF] ${name}: ${measureEntry.duration}ms` -> substitutes to
`[PERF] <name>: <n>ms`, byte-identical to the text form the parser accepts
(verified: `classify_line("[PERF] example: 123ms")` -> COMPLETED
Marker(example,123,ms)). The markStart/markEnd/measureMark trio + a `MARKERS`
map are present (spec "Text-Form Emitter Contract" satisfied literally). The
example `MARKERS` content (`{Home,Checkout}` vs the user's `{LENDING:'/loans'}`)
and exporting `measureMark` are neutral/defensible.

Verdict on the variant: **unwanted deviation on the import + missing try/catch;
faithful on the load-bearing log shape.** Recommend reconciling to match the
user's module (default import + try/catch) BEFORE Phase 4 embeds it in README.
Classification: correctness bug / fidelity deviation.

### W-1 (WARNING — coverage gap / hollow anti-drift) — `emitted_sample()` is NOT derived from the snippet body

Spec scenario "Emitted line parses cleanly (anti-drift)" reads "the sample
marker line EMBEDDED IN the generated snippet ... fed through parse()". Proven
otherwise: the snippet emits `console.log(`[PERF] ${name}: ${measureEntry.duration}ms`)`
while `emitted_sample()` returns an INDEPENDENTLY hand-authored
`f"{PERF_TAG} example: 123ms"` — the strings "example"/"123ms" appear nowhere in
the snippet body. They share ONLY `PERF_TAG`. So the contract test guarantees
the TAG cannot drift, but NOT the line structure (`: ` separator, `ms` unit). If
someone edited the snippet's `console.log` to e.g. `[PERF] ${name}=${...}ms`, the
anti-drift test would STILL PASS and the pasted snippet would emit lines the
parser rejects — the exact drift the test claims to prevent. Recommend deriving
the tested line FROM `render_snippet` (regex the console.log template and
substitute a name/value) or asserting the snippet body contains the
`emitted_sample`-shaped substring. Classification: coverage gap / test integrity.

### W-2 (WARNING — usability, untested) — `markers --help` / `markers -h` error with exit 2 "No such option: --help"

The root app disables auto-help (`context_settings={"help_option_names": []}`,
main.py:33) and each FLAT command re-enables it. `add_typer(markers_app, ...)`
(main.py:141) inherits the empty list; only the two SUBCOMMANDS re-enable help
via their own `context_settings`. Result: the natural discovery command for the
FIRST nested group fails:
  - `perfvibe markers --help` -> "No such option: --help", **exit 2**
  - `perfvibe markers -h`     -> **exit 2**
  - `perfvibe markers snippet --help` -> exit 0 (works)
  - `perfvibe markers` (no subcommand) -> "Missing command.", exit 2
No integration test covers group-level `--help`. Not spec-violating (spec is
silent on group help) and never exits 1, but a real discoverability regression
for a user-facing command group. One-line fix: pass
`context_settings={"help_option_names":["--help","-h"]}` to the `markers_app`
`typer.Typer(...)`. Classification: usability defect / coverage gap.

### S-1 (SUGGESTION — spec-conformant sharp edge) — non-TTY-but-empty stdin + arg is rejected as "both"

`detect_mode` keys off `sys.stdin.isatty()` alone (per design). So
`perfvibe markers doctor "[PERF] x: 1ms" < /dev/null` (or any CI/subprocess
context where stdin is a non-TTY but nothing meaningful is piped) resolves to
"both a <logcat line> argument and piped stdin" -> exit 2, even though the user
supplied ONLY an argument. Matches the spec's literal wording ("both an argument
AND non-TTY piped stdin"), but makes single-line mode unusable from any non-TTY
script unless stdin is explicitly a TTY. Flagging as a known sharp edge, not a
defect. Classification: spec ambiguity / UX.

### S-2 (SUGGESTION) — a name containing a colon is reported `malformed_text`

The text regex `^(?P<name>[^:]+):...` cannot represent a name with a `:` in it,
so `[PERF] my:weird:name: 42ms` -> FAILURE/malformed_text. The spec's own
"surprising name chars" example `my-weird/name.v2` DOES parse (verified
COMPLETED), so this is narrow, but a route name like `foo:bar` would be reported
malformed. Inherited from the existing parser (out of PR3 scope to change);
noted for honesty. Classification: coverage gap (pre-existing).

### S-3 (SUGGESTION) — reused `parse()` diagnostic reads oddly in doctor context

Empty piped stdin -> `doctor` surfaces `parse()`'s verbatim diagnostic
"no logcat output was captured at all — check the device is connected
(`adb devices`) ...". Spec REQUIRES surfacing `MarkerParseResult.diagnostic`, so
this is conformant, but the run-capture-centric wording ("device connected") is
confusing when the user simply piped an empty buffer to `doctor`. Cosmetic.

## Positive verifications (no finding)

- **coverage_ok honesty CONFIRMED conformant.** Code uses `coverage_ok =
  bool(breakdown.parsed)` (markers.py:385). With `parse(lines, iterations=1)`
  in BOTH modes, design's `bool(parsed) and not partial_coverage` reduces
  algebraically to exactly `bool(parsed)` — matches the reconciled spec
  ("this line parsed" / "any marker parsed", never a ratio). Verified
  `coverage_ok:false` NEVER changes the exit code: single-line-fail -> exit 0
  coverage_ok False; zero-perf-lines stdin -> exit 0 coverage_ok False. Not a
  gate, consistent with the Non-Goal.
- **ctx.obj propagation (design #1 risk) verified live** for BOTH the happy path
  (`--json markers doctor "[PERF] x: 12ms"` -> exit 0, payload mode=line,
  parsed=[{x,12,ms}]) and a global flag before the subcommand
  (`--config <bad> markers snippet` -> ConfigError -> exit 2, proving
  main_callback runs before the sub-app resolves). Never exits 1.
- **Exit-code discipline: doctor NEVER exits 1** across empty stdin, only-oversized,
  only-PERF-META, only-markStart, mixed, both-arg-and-pipe (2), unknown-flag (2),
  stdin OSError (3). snippet: 0 / unknown-lang 2. Verified live + by tests.
- **Oversized truncation applied IN the payload:** a 5000-char line -> payload
  574 bytes, echoed `line` exactly 121 chars (120 + `…`). Multibyte (`界`x5000)
  and an emoji ZWJ family straddling char 120 truncate cleanly on a code-point
  boundary — no crash, no mojibake (str slicing, not bytes).
- **snippet `--json` round-trips losslessly:** keys exactly {schema_version,
  lang, code}; `[PERF]` present in code; `__PERF_TAG__` placeholder never leaks.
- **Pretty snippet stays paste-clean:** the NON_TTY_NUDGE is emitted to STDERR
  (err=True), so `perfvibe markers snippet > x.ts` yields pure code on stdout.

## Verdict

**PASS-WITH-FINDINGS.** Gates fully green (928 passed, 95.01%, ruff/format/mypy
clean); Phase 3 tasks 3.1-3.6 conform to spec/design and are genuinely
implemented; the CLI wiring, exit-code discipline, oversized truncation,
coverage_ok semantics, and ctx.obj propagation all verified live and correct.
The blocking concern is **C-1**: the copy-paste snippet deviates from the user's
proven-working module (named import + dropped try/catch) with real runtime-crash
risk — this should be reconciled before Phase 4 embeds it in the README.
Secondary: **W-1** (anti-drift shares only the tag, not the line shape) and
**W-2** (`markers --help` errors). If a human accepts the snippet variant as-is,
this slice can proceed to archive; otherwise loop back through apply to fix C-1
(and ideally W-1/W-2).
