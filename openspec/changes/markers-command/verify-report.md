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
