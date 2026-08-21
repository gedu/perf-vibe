"""Unit tests for the pure helpers in `cli/commands/reassure_import.py`
(tasks.md PR4b task 4.9's "put the derivation in a pure, unit-testable
function" instruction — mirrors `tests/unit/test_markers.py` testing
`cli/commands/markers.py`'s pure `detect_mode`/`bucket_lines` helpers
directly, no CLI runner needed).

`derive_reassure_kind` decides the `reassure_import.kind` value from the
file's basename alone (design/prompt: `current.perf` -> `current`,
`baseline.perf` -> `baseline`, anything else -> `unknown`); `--kind`
overriding this derivation is exercised at the CLI layer
(`tests/integration/test_cli_reassure_import.py`), not here.
"""

from __future__ import annotations

from perf.cli.commands.reassure_import import derive_reassure_kind


def test_current_perf_basename_derives_current():
    assert derive_reassure_kind("current.perf") == "current"
    assert derive_reassure_kind("/some/project/.reassure/current.perf") == "current"


def test_baseline_perf_basename_derives_baseline():
    assert derive_reassure_kind("baseline.perf") == "baseline"
    assert derive_reassure_kind("/some/project/.reassure/baseline.perf") == "baseline"


def test_any_other_basename_derives_unknown():
    assert derive_reassure_kind("nightly.perf") == "unknown"
    assert derive_reassure_kind("tests/fixtures/reassure_sample.perf") == "unknown"


def test_derivation_looks_only_at_the_basename_not_the_directory():
    # A directory component that happens to say "current" or "baseline"
    # must never leak into the derivation — only the file's own basename.
    assert derive_reassure_kind("current/baseline.perf") == "baseline"
    assert derive_reassure_kind("baseline/current.perf") == "current"
