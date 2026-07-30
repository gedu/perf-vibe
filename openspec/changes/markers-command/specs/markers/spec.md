# markers Specification

## Purpose

`markers snippet` emits a paste-ready TS/JS emitter matching the shape
`AdbLogcatMarkerSource.parse()` actually consumes; `markers doctor` validates a real logcat
line or capture against that SAME parser and explains the verdict. Both subcommands are
read-only and never mutate run history.

## Non-Goals

JSON-form emitter output; `doctor` as a CI/coverage gate; native iOS/Android emitters.
`AdbLogcatMarkerSource.parse()`'s existing signature and observable behavior, plus
`MarkerParseResult`/`Marker`, are reused UNCHANGED. The adapter additionally gains ONE new public
line-classification function (additive — not a breaking change to `parse()`).

## Requirements

### Requirement: Shared PERF_TAG Constant

The system MUST expose one `PERF_TAG` constant referenced by BOTH `AdbLogcatMarkerSource`
(consumer) and `markers snippet` (producer). No second, independently-maintained tag string may
exist.

#### Scenario: Parser and snippet agree on the tag
- GIVEN the parser's tag-detection logic and the snippet's emitted marker text
- WHEN either is inspected for the literal tag string
- THEN both resolve to the same imported `PERF_TAG` value

### Requirement: Text-Form Emitter Contract

`markers snippet` MUST emit ONLY the text form `[PERF] <name>: <n>ms`, matching what
`AdbLogcatMarkerSource` accepts, implementing a markStart/markEnd/measureMark trio plus a
`MARKERS` route-name map (mirrors the `react-native-performance` reference module). No
`--form`/JSON-form flag exists.

#### Scenario: Emitted line parses cleanly (anti-drift)
- GIVEN the sample marker line embedded in the generated snippet
- WHEN fed through `AdbLogcatMarkerSource.parse()`
- THEN it yields exactly one `Marker` with the expected name/value/unit

### Requirement: Snippet Language Selection

`markers snippet` MUST accept `--lang ts` (default) or `--lang js`. Any other value MUST be a
usage error (exit `2`).

#### Scenario: Default is TypeScript
- GIVEN `markers snippet` with no `--lang`
- WHEN it runs
- THEN output is equivalent to `--lang ts`

#### Scenario: Unknown language is a usage error
- GIVEN `markers snippet --lang python`
- WHEN it runs
- THEN it exits `2`; no code reaches stdout

### Requirement: Snippet --json Payload

`markers snippet --json` MUST emit a payload whose keys are EXACTLY `schema_version`, `lang`,
`code`. Pretty mode MUST print the raw code only — no decoration that would break a copy-paste.

#### Scenario: --json shape
- GIVEN `markers snippet --lang js --json`
- WHEN it succeeds
- THEN the payload has exactly `schema_version`, `lang: "js"`, `code`

#### Scenario: Pretty output is paste-ready
- GIVEN `markers snippet` without `--json`
- WHEN it succeeds
- THEN stdout is the raw emitter code only, pasteable into a `.ts`/`.js` file

### Requirement: Doctor Input Mode Detection

`markers doctor` MUST accept EITHER one positional `<logcat line>` OR piped stdin, never both,
never neither. Argument present → single-line mode. No argument + non-TTY stdin → stdin/capture
mode. No argument + TTY stdin, OR both argument and piped stdin, MUST be a usage error (exit `2`).

#### Scenario: Single-line mode
- GIVEN `markers doctor "[PERF] cold_start: 812ms"`
- WHEN invoked
- THEN single-line mode runs `parse([line], iterations=1)`

#### Scenario: Stdin mode
- GIVEN `cat logcat.txt | markers doctor` with no argument
- WHEN stdin is not a TTY
- THEN stdin/capture mode treats the whole buffer as one capture

#### Scenario: Ambiguous input is a usage error
- GIVEN either (a) no argument and stdin IS a TTY, or (b) both an argument AND non-TTY piped stdin
- WHEN invoked
- THEN it exits `2`, explaining exactly one input source is required

### Requirement: Shared Line-Classification Function

`AdbLogcatMarkerSource` MUST expose ONE public line-classification function that BOTH `parse()`
(marker extraction/aggregation) and `markers doctor` (per-line reporting) call. `doctor` MUST NOT
duplicate tag/regex/JSON-detection logic of its own — this extends the anti-drift guarantee to
the classifier itself. This is ADDITIVE: `parse()`'s existing signature, inputs, and
`MarkerParseResult` output shape remain UNCHANGED; the adapter gains a new public API, it does not
modify an existing one.

#### Scenario: doctor delegates to the same classifier as parse()
- GIVEN a `[PERF]` line that `parse()` would accept, reject, or skip
- WHEN `markers doctor` classifies that same line
- THEN it calls the SAME shared classification function `parse()` uses internally

#### Scenario: parse()'s contract is unaffected
- GIVEN existing callers of `AdbLogcatMarkerSource.parse()`
- WHEN the shared classifier is introduced
- THEN `parse()`'s signature, inputs, and `MarkerParseResult` output remain unchanged

### Requirement: Diagnosis Categories

Both modes MUST classify each observed line into exactly one category via the shared
classification function. For every `[PERF]`-tagged parse FAILURE, the SPECIFIC reason MUST be
reported on a PER-LINE basis (malformed text form / invalid JSON / non-finite-or-negative value).
Oversized lines are skipped before reaching the classifier and are NOT reason-attributed.
`MarkerParseResult.diagnostic`, when set, MUST be surfaced.

| Category | Trigger | Reported as |
|---|---|---|
| Completed marker | text or JSON payload parses | `Marker` (name/value/unit) |
| markStart w/o markEnd | bare `markStart:<name>` | recognized + skipped |
| PERF-META | `[PERF-META]` line | context-only, skipped |
| Malformed text | non-numeric value after `:` | per-line failure: malformed text form |
| Invalid JSON | unparsable `{...}` payload | per-line failure: invalid JSON |
| Non-finite/negative | JSON `value` is NaN/Infinity/negative | per-line failure: invalid value |
| Oversized (>4096) | line exceeds parser bound | skipped before classifier, no reason |
| Non-`[PERF]` line | no tag match | ignored |
| Empty input | no `[PERF]` payload present | no marker: no payload |
| Surprising name chars | e.g. `my-weird/name.v2` | name reported as-is, unvalidated |

#### Scenario: Mixed stdin capture breakdown
- GIVEN a piped buffer with a completed marker, a bare `markStart`, a `[PERF-META]` line, a
  malformed `[PERF]` line, and unrelated logs
- WHEN diagnosed in stdin mode
- THEN the report is INFORMATIONAL (no pass/fail gate), enumerating each category's count and,
  for failures, the specific reason per line

#### Scenario: Nothing parsed is still a successful diagnosis
- GIVEN a piped buffer with zero `[PERF]` lines
- WHEN diagnosed in stdin mode
- THEN the command exits `0` — finding zero markers is success, not failure

### Requirement: Doctor Exit-Code Discipline

`markers doctor` MUST exit `0` on any successful diagnosis (even zero markers found), `2` on a
usage error, `3` only on a runtime failure (e.g. stdin read failure). It MUST NEVER exit `1`.

#### Scenario: Stdin read failure
- GIVEN stdin mode is selected but reading the piped stream raises an I/O error
- WHEN the command runs
- THEN it exits `3`; never `1`

### Requirement: Doctor --json Payload

`markers doctor --json` MUST emit ONE coherent, `schema_version`-carrying schema shape covering
BOTH single-line and stdin modes, not two competing shapes. Exact field naming/nesting is a
design-phase decision.

#### Scenario: Same schema shape across modes
- GIVEN `markers doctor --json` invoked once per mode
- WHEN both payloads are inspected
- THEN both carry the same top-level `schema_version` and the same overall schema shape
