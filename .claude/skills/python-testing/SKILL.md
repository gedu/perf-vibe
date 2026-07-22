---
name: python-testing
description: "Trigger: writing or editing tests in perf-lab-cli (pytest, fakes, golden, contract, coverage). Test behavior through the ports, not implementation details."
license: Apache-2.0
metadata:
  author: eduardo-graciano
  version: "1.0"
---

## Activation Contract

Load when writing or changing tests under `tests/` in the `perf-vibe` repo. Layout: `tests/unit/` (pure domain), `tests/integration/` (adapters + store vs fixtures), `tests/contract/` (`--json` shape), `tests/golden/` (pretty output). Complements `perf-cli-standards` rule 8.

## Hard Rules

1. **Test behavior, not implementation.** Assert observable outcomes (return value, persisted rows, exit code, rendered text) — never private call order or internal attributes. One clear reason-to-fail per test.
2. **Fakes over mocks.** Every side effect is a port; drive code through a hand-written fake in `tests/fakes.py` (`FakeDriver`, `FakeStore`, `FrozenClock`, ...). Do NOT `unittest.mock.patch` internals — a patched internal couples the test to the code's shape and rots. Inject a fake through the port instead.
3. **Never monkeypatch the thing under test.** If a CLI test patches `build_driver`, it is not testing the real wiring — this exact gap once hid a broken driver behind green tests. Every code path must be exercised through the REAL wiring at least once (fake only the process/device/clock boundary).
4. **RED before GREEN, and RED for the right reason.** Write the failing test first; confirm it fails because the behavior is missing, not because of an import/typo.
5. **Property-based for pure math.** Use `hypothesis` for `domain/statistics.py`/aggregation invariants (e.g. `min ≤ p50 ≤ p90 ≤ max`; nearest-rank edges n=1, all-equal, even/odd n; CPU total = sum of per-thread). Example-based tests miss the edges these expose.
6. **Golden + contract discipline.** Pretty output: golden files with color forced OFF (`--update-golden` regenerates). `--json`: a contract test that FAILS on any shape change without a `schema_version` bump. The banner must never appear in `--json` output — assert it.
7. **Adapters test against recorded fixtures, never live devices.** Small hand-written fixtures (trimmed logcat, minimal Flashlight JSON) checked into `tests/fixtures/`; never a 360KB real dump, never a real adb/maestro/flashlight call.
8. **Cover risk, not lines.** Hit the highest-blast-radius surfaces hardest — the §9.6 ingestion transaction (rollback → zero rows), marker/Flashlight parsing, and the exit-code mapping (0/2/3, never 1). Do NOT chase 100% on pure-I/O lines (a real `subprocess.Popen`) that can only run against a device — that is what the port/fake split is for.

## Decision Gates

| Need | Do |
| --- | --- |
| Isolate a side effect | Inject the port's fake — never patch an internal |
| Test pure stats/aggregation | `hypothesis` property test for the invariants |
| Test a failure/exit path | Drive the REAL wiring; fake only the process/device; assert the exact exit code |
| Snapshot human output | Golden file, color off |
| Guard the machine contract | Contract test that breaks on unversioned `--json` change |

## Review Checklist

- [ ] Asserts behavior/outputs, not internal calls; one reason to fail.
- [ ] Uses a port fake, not `mock.patch` on internals.
- [ ] The real wiring is exercised for this path (not fully monkeypatched).
- [ ] Pure math has a property-based test covering the edges.
- [ ] Highest-risk paths (ingestion rollback, parsing, exit codes) covered hardest.

## References

- `.claude/skills/perf-cli-standards/SKILL.md` — rule 8 (testing) and the exit-code / `--json` rules this deepens.
- `tests/fakes.py` — the canonical port fakes to reuse.
