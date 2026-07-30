# Tasks: `markers` Command (snippet + doctor)

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | ~950–1200 total (adapter refactor, 2 contracts, CLI command, 3 test suites, docs) |
| 400-line budget risk | High |
| Session budget (override) | 800 — still exceeded overall; each PR below stays under it |
| Chained PRs recommended | Yes |
| Suggested split | PR1 → PR2 → PR3 → PR4 |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | PR | Focused test | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| 1 | `PERF_TAG` promotion + `classify_line` extraction | PR1 | `pytest tests/integration/test_markers_adb_logcat.py tests/unit/test_markers.py -q` | N/A, pure text classifier | Revert `model.py`/adapter hunks |
| 2 | snippet/doctor `--json` contracts | PR2 | `pytest tests/contract/test_markers_snippet_v1_contract.py tests/contract/test_markers_doctor_v1_contract.py -q` | N/A, pure builders | Delete 2 contract modules + tests |
| 3 | `markers` sub-app + wiring + integration/anti-drift tests | PR3 | `pytest tests/integration/test_cli_markers.py tests/contract/test_markers_snippet_parses.py -q` | `perfvibe markers doctor "[PERF] x: 1ms"` | Remove `add_typer` line + delete `commands/markers.py` |
| 4 | README/docs + doc-sync test | PR4 | `pytest tests/unit/test_readme_markers_sync.py -q` | N/A, docs only | Revert README/`docs/commands.md` hunks |

## Phase 1 — Shared Classifier & `PERF_TAG` (PR1)
- [x] 1.1 `domain/model.py`: add public `PERF_TAG = "[PERF]"` near `GATE_*`.
- [x] 1.2 `adapters/markers_adb_logcat.py`: import `PERF_TAG`; delete local `_PERF_TAG`.
- [x] 1.3 RED: extend `tests/integration/test_markers_adb_logcat.py` — characterization test pinning `MarkerParseResult` byte-identical, BEFORE refactor.
- [x] 1.4 GREEN: extract public `classify_line(raw_line)->LineVerdict`, `LineKind` (COMPLETED|MARK_START|PERF_META|IGNORED|FAILURE), reason constants; refactor `parse()` to delegate — 1.3 stays green.
- [x] 1.5 New `tests/unit/test_markers.py`: per-verdict `classify_line` tests (each FAILURE reason, MARK_START, PERF_META, IGNORED, COMPLETED, oversized).

## Phase 2 — JSON Contracts (PR2)
- [x] 2.1 `contracts/markers_snippet_v1.py`: `SCHEMA_VERSION=1`, `build_snippet_payload(*, lang, code)`.
- [x] 2.2 `tests/contract/test_markers_snippet_v1_contract.py`: required-keys/types + version-bump guard (mirror `test_init_v1_contract.py`).
- [x] 2.3 `contracts/markers_doctor_v1.py`: `SCHEMA_VERSION=1`, `build_doctor_payload(*, mode, lines_scanned, parsed, mark_start_without_end, perf_meta, parse_failures, ignored, coverage_ok, diagnostic)`.
- [x] 2.4 `tests/contract/test_markers_doctor_v1_contract.py`: same guard, both `mode` values.

## Phase 3 — CLI Command + Wiring (PR3)
- [ ] 3.1 `cli/commands/markers.py`: pure `render_snippet(lang)` + `emitted_sample()` (markStart/markEnd/measureMark + `MARKERS` map).
- [ ] 3.2 Same file: `detect_mode(arg, stdin_is_tty)` + doctor bucketing helper iterating `classify_line` into parsed/mark_start_without_end/perf_meta/parse_failures/ignored.
- [ ] 3.3 Same file: `markers_app` (`snippet`/`doctor` callbacks) reading `ctx.obj`, using `emit_error`/`render_json`, never exit `1`.
- [ ] 3.4 `cli/main.py`: import `markers_app`; `app.add_typer(markers_app, name="markers")`.
- [ ] 3.5 `tests/contract/test_markers_snippet_parses.py`: anti-drift — `emitted_sample()` through real `parse()`, assert exact `Marker`.
- [ ] 3.6 `tests/integration/test_cli_markers.py`: `--json` propagation through `ctx.obj`; exit-code matrix (0/2/3, never 1); stdin mode; ambiguous both/neither; `snippet --lang ts/js`.

## Phase 4 — Docs (PR4)
- [ ] 4.1 RED: `tests/unit/test_readme_markers_sync.py` — README fenced snippet == `render_snippet("ts")`.
- [ ] 4.2 GREEN: add instrumentation section to `README.md` with the fenced snippet.
- [ ] 4.3 `docs/commands.md`: add `## markers` entry; extend schema_version list with markers snippet/doctor = 1.
