"""Golden tests for `cli/output/reassure_list_pretty.render_reassure_list`
(SKILL rule 8: "Golden files for pretty output with color forced off"), plus
non-golden guards on the properties the view exists for — a golden proves
the output is STABLE, never that it is right.

The renderer takes the RAW `Sequence[ReassureImportRow]` (never the
`--json` payload) — the SAME `render_history(..., runs, ...)` shape
`history_pretty.py` uses, not `reassure_import_pretty`'s
"build-payload-then-render-the-payload" shape, because `ordering_key` is
deliberately NOT on the wire contract (`reassure_list_v1`'s
no-second-source-of-truth reasoning) but IS needed here to dim a
fallen-back-to-`imported_at` row (design "Renderers": "`ordering_key` shown
dim when it fell back to `imported_at`").
"""

from __future__ import annotations

from pathlib import Path

from perf.cli.output.primitives import DIM, RESET
from perf.cli.output.reassure_list_pretty import render_reassure_list
from perf.domain.model import ReassureImportRow

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_ANSI_ESCAPE = "\x1b["


def _row(**overrides: object) -> ReassureImportRow:
    defaults: dict[str, object] = {
        "import_id": 1,
        "ordered_at": "2026-01-01T00:00:00.000Z",
        "ordering_key": "created_date",
        "imported_at": "2026-01-01T00:05:00.000Z",
        "created_date": "2026-01-01T00:00:00.000Z",
        "branch": "main",
        "commit_hash": "abc123def4567890",
        "source_path": "current.perf",
        "entry_count": 4,
    }
    defaults.update(overrides)
    return ReassureImportRow(**defaults)


def _assert_or_update_golden(request, fixture_name: str, actual: str) -> None:
    fixture_path = _FIXTURES_DIR / fixture_name
    if request.config.getoption("--update-golden"):
        fixture_path.parent.mkdir(parents=True, exist_ok=True)
        fixture_path.write_text(actual)
        return
    expected = fixture_path.read_text()
    assert actual == expected, (
        f"golden mismatch for {fixture_name} — run with --update-golden to "
        "regenerate if this change is intentional"
    )


# ===== (a) a roster with a `created_date` row and an `imported_at` fallback row =====


def test_roster_matches_golden(request):
    rows = (
        _row(
            import_id=2,
            created_date="2026-01-02T00:00:00.000Z",
            ordering_key="created_date",
            ordered_at="2026-01-02T00:00:00.000Z",
        ),
        _row(
            import_id=1,
            created_date=None,
            ordering_key="imported_at",
            ordered_at="2026-01-01T00:05:00.000Z",
            entry_count=0,
        ),
    )
    _assert_or_update_golden(request, "reassure_list_roster.txt", render_reassure_list(rows))


def test_roster_has_no_ansi_escapes_when_color_off():
    rows = (_row(),)
    assert _ANSI_ESCAPE not in render_reassure_list(rows)


# ===== (b) an empty roster =====


def test_empty_roster_matches_golden(request):
    _assert_or_update_golden(request, "reassure_list_empty.txt", render_reassure_list(()))


def test_empty_roster_says_so_instead_of_an_empty_table():
    assert "no imports" in render_reassure_list(()).lower()


# ===== guards =====


def test_render_is_wrapped_in_an_open_right_box():
    rows = (_row(),)
    actual = render_reassure_list(rows)

    assert actual.startswith("┌─ perfvibe reassure list · ")
    assert actual.rstrip("\n").endswith("└─")
    body = actual.splitlines()[1:-1]
    assert all(line.startswith("│") for line in body)
    content = [line for line in body if line.strip() != "│"]
    assert not any(line.rstrip().endswith("│") for line in content), "the box stays open-right"


def test_ordering_key_falls_back_to_imported_at_renders_dim():
    fallback_row = _row(
        created_date=None, ordering_key="imported_at", ordered_at="2026-01-01T00:05:00.000Z"
    )
    actual = render_reassure_list((fallback_row,), color=True)

    assert DIM in actual
    assert RESET in actual


def test_created_date_row_is_never_dimmed_for_its_own_date():
    """A row whose ordering key IS `created_date` never gets the fallback
    dimming — only the `imported_at`-fallback rows do."""

    created_date_row = _row(ordering_key="created_date")
    actual = render_reassure_list((created_date_row,), color=True)

    assert DIM not in actual


def test_commit_hash_is_shown_short_never_the_full_sha():
    rows = (_row(commit_hash="abc123def4567890"),)
    actual = render_reassure_list(rows)

    assert "abc123def4567890" not in actual
    assert "abc123d" in actual


def test_missing_commit_hash_renders_a_dash_not_none():
    rows = (_row(commit_hash=None),)
    actual = render_reassure_list(rows)

    assert "None" not in actual


def test_entry_count_is_visible_per_row():
    rows = (_row(entry_count=7),)
    actual = render_reassure_list(rows)

    assert "7" in actual


def test_color_off_emits_no_ansi_in_any_outcome():
    payloads = [
        (),
        (_row(),),
        (_row(created_date=None, ordering_key="imported_at"),),
    ]

    for rows in payloads:
        assert _ANSI_ESCAPE not in render_reassure_list(rows, color=False)
