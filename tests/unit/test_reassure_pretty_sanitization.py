"""Terminal-control-sequence injection guard (re-verification finding
W-6). `reassure-read` is the first code in this CLI to render text it did
not generate itself onto a real terminal: a reassure entry `name` (and a
`.perf` header's `branch`/`commit_hash`) come straight from a
third-party-generated `.perf` file, fully attacker-controlled. Verified
over a real pty in the original finding: a `name` containing `ESC[31m`,
`ESC[0m`, and a BEL byte produced those exact bytes in a rendered
`reassure entries` row, invisible to every OTHER test in this suite
because they all capture rather than allocate a tty (Click's `echo`
strips ANSI incidentally on a non-terminal stream).

**THE TRAP THIS FILE EXISTS TO AVOID**: a capture-based CLI test proves
nothing here, because the stripping happens on the OUTPUT SIDE regardless
of what the renderer produced. Every test below calls a renderer function
DIRECTLY and asserts on the exact bytes it RETURNS — the only way to
actually observe this class of defect."""

from __future__ import annotations

from perf.cli.output.primitives import sanitize_untrusted_text
from perf.cli.output.reassure_compare_pretty import render_reassure_compare
from perf.cli.output.reassure_entries_pretty import render_reassure_entries
from perf.cli.output.reassure_history_pretty import render_reassure_history
from perf.cli.output.reassure_list_pretty import render_reassure_list
from perf.cli.output.reassure_show_pretty import render_reassure_show
from perf.domain.model import (
    HistoryMetric,
    ReassureEntryRow,
    ReassureImportRow,
    ReassureSeriesPoint,
    Verdict,
)
from perf.domain.reassure_compare import (
    SERIES_DURATION,
    SERIES_RENDER_COUNT,
    ReassureComparison,
    UpdateCountChange,
)

_ESC = "\x1b"
_BEL = "\x07"
_MALICIOUS_NAME = f"{_ESC}[31mRED{_ESC}[0m{_BEL}bell"
_MALICIOUS_COMMIT = f"{_ESC}[31mevil{_ESC}[0m"
_MALICIOUS_BRANCH = f"{_ESC}[2Jmain"  # ESC[2J: clear-screen
_NON_LATIN_NAME = "パネルが正しく描画される — Виджет панель"


def _assert_inert(rendered: str) -> None:
    assert _ESC not in rendered
    assert _BEL not in rendered
    # C1 8-bit escape/CSI range (also `Cc`) must be gone too.
    assert not any("\x80" <= ch <= "\x9f" for ch in rendered)


# ===== primitives.sanitize_untrusted_text: the shared helper itself =====


def test_sanitize_replaces_c0_control_characters():
    assert sanitize_untrusted_text(f"a{_ESC}[31mb{_BEL}c") == "a�[31mb�c"


def test_sanitize_replaces_c1_control_characters():
    # U+009B is the C1 CSI introducer — the 8-bit equivalent of ESC[.
    assert sanitize_untrusted_text("a\x9bb") == "a�b"


def test_sanitize_replaces_del():
    assert sanitize_untrusted_text("a\x7fb") == "a�b"


def test_sanitize_leaves_plain_ascii_untouched():
    assert sanitize_untrusted_text("WidgetPanel renders correctly") == (
        "WidgetPanel renders correctly"
    )


def test_sanitize_leaves_non_latin_scripts_completely_untouched():
    """The scope guard, proven positively: real script text — Japanese,
    Cyrillic, an em dash — is NOT in the `Cc` category and must survive
    byte-for-byte. Only genuine control code points are ever touched."""
    assert sanitize_untrusted_text(_NON_LATIN_NAME) == _NON_LATIN_NAME


# ===== reassure entries =====


def test_entries_pretty_neutralizes_a_malicious_entry_name():
    row = ReassureEntryRow(
        entry_id=1,
        name=_MALICIOUS_NAME,
        entry_type="render",
        runs=1,
        duration=HistoryMetric(metric_name="duration_ms", p50=1.0, p90=1.0, n=1, unit="ms"),
        count=HistoryMetric(metric_name="render_count", p50=1.0, p90=1.0, n=1, unit="count"),
        initial_update_count=None,
    )
    rendered = render_reassure_entries(1, [row])
    _assert_inert(rendered)
    # The visible text (minus the stripped control bytes) still survives —
    # this is neutralization, not deletion of the whole field.
    assert "RED" in rendered
    assert "bell" in rendered


def test_entries_pretty_preserves_a_non_latin_entry_name():
    row = ReassureEntryRow(
        entry_id=1,
        name=_NON_LATIN_NAME,
        entry_type="render",
        runs=1,
        duration=None,
        count=None,
        initial_update_count=None,
    )
    rendered = render_reassure_entries(1, [row])
    assert _NON_LATIN_NAME in rendered


# ===== reassure show =====


def test_show_pretty_neutralizes_a_malicious_entry_name():
    entry = ReassureEntryRow(
        entry_id=1,
        name=_MALICIOUS_NAME,
        entry_type="render",
        runs=1,
        duration=None,
        count=None,
        initial_update_count=None,
    )
    rendered = render_reassure_show(1, entry, None)
    _assert_inert(rendered)


# ===== reassure history =====


def _series_point(**overrides: object) -> ReassureSeriesPoint:
    defaults: dict[str, object] = {
        "import_id": 1,
        "ordered_at": "2026-01-01",
        "ordering_key": "created_date",
        "entry": ReassureEntryRow(
            entry_id=1,
            name="whatever",
            entry_type="render",
            runs=1,
            duration=HistoryMetric(metric_name="duration_ms", p50=1.0, p90=1.0, n=1, unit="ms"),
            count=HistoryMetric(metric_name="render_count", p50=1.0, p90=1.0, n=1, unit="count"),
            initial_update_count=None,
        ),
        "commit_hash": None,
        "branch": None,
    }
    defaults.update(overrides)
    return ReassureSeriesPoint(**defaults)


def test_history_pretty_neutralizes_a_malicious_name_argument():
    rendered = render_reassure_history(_MALICIOUS_NAME, [_series_point()])
    _assert_inert(rendered)


def test_history_pretty_neutralizes_a_malicious_commit_hash_label():
    point = _series_point(commit_hash=_MALICIOUS_COMMIT)
    rendered = render_reassure_history("safe name", [point])
    _assert_inert(rendered)


# ===== reassure compare =====


def _verdict(**overrides: object) -> Verdict:
    defaults: dict[str, object] = {
        "metric_name": SERIES_DURATION,
        "delta_pct": 0.0,
        "threshold_pct": 5.0,
        "status": "stable",
        "latest_value": 100.0,
        "baseline_value": 100.0,
        "unit": "ms",
        "sample_n": 5,
        "baseline_commit_n": 5,
        "series": (100.0, 100.0),
        "floor": 5.0,
        "higher_is_better": False,
    }
    defaults.update(overrides)
    return Verdict(**defaults)


def test_compare_pretty_neutralizes_a_malicious_name():
    comparison = ReassureComparison(
        name=_MALICIOUS_NAME,
        latest=_series_point(),
        baseline_import_n=1,
        verdicts=(
            _verdict(metric_name=SERIES_DURATION),
            _verdict(metric_name=SERIES_RENDER_COUNT, unit="count"),
        ),
        update_count=UpdateCountChange(baseline=0, latest=0, state="unchanged"),
    )
    rendered = render_reassure_compare(comparison)
    _assert_inert(rendered)


# ===== reassure list =====


def test_list_pretty_neutralizes_a_malicious_branch_and_commit_hash():
    row = ReassureImportRow(
        import_id=1,
        ordered_at="2026-01-01T00:00:00.000Z",
        ordering_key="created_date",
        imported_at="2026-01-01T00:00:00.000Z",
        created_date="2026-01-01T00:00:00.000Z",
        branch=_MALICIOUS_BRANCH,
        commit_hash=_MALICIOUS_COMMIT,
        source_path="current.perf",
        entry_count=1,
    )
    rendered = render_reassure_list([row])
    _assert_inert(rendered)


# ===== --json stays byte-for-byte untouched (module docstring's own
# claim) — a sanity check that this whole file's helper is never wired
# into the JSON path, where `json.dumps` already escapes control bytes
# and a rewrite would corrupt data an agent needs to match verbatim. =====


def test_json_reporter_module_never_imports_the_sanitizer():
    import inspect

    from perf.cli.output import json_reporter

    source = inspect.getsource(json_reporter)
    assert "sanitize_untrusted_text" not in source
