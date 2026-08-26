"""Golden tests for `cli/output/reassure_import_pretty.render_reassure_import`
(SKILL rule 8: "Golden files for pretty output with color forced off"), plus
the non-golden guards on the properties the restyle exists for — a golden
proves the output is STABLE, never that it is right.

Cases: (a) a fresh import with skips and a render-issue finding, (b) a
byte-identical re-import, (c) a readable file that recovered nothing.

The renderer is a pure `payload -> str` function, so these build the payload
through `build_reassure_import_payload` rather than driving the CLI: that keeps
the contract and the view honest with each other without a store, a temp file
or a fixture `.perf`.
"""

from __future__ import annotations

from pathlib import Path

from perf.cli.output.primitives import DIM, RESET, YELLOW
from perf.cli.output.reassure_import_pretty import render_reassure_import
from perf.contracts.reassure_import_v1 import build_reassure_import_payload

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_ANSI_ESCAPE = "\x1b["

_HASH = "3f2a1b9c2d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8"
_PATH = ".reassure/current.perf"


def _payload(**overrides):
    defaults = {
        "path": _PATH,
        "content_hash": _HASH,
        "kind": "current",
        "already_imported": False,
        "entries_imported": 4,
        "entries_skipped": 6,
        "duration_samples_imported": 40,
        "count_samples_imported": 4,
        "entries_with_render_issues": 1,
    }
    defaults.update(overrides)
    return build_reassure_import_payload(**defaults)


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


# ===== (a) a fresh import =====


def test_fresh_import_matches_golden(request):
    _assert_or_update_golden(
        request, "reassure_import_fresh.txt", render_reassure_import(_payload())
    )


def test_fresh_import_has_no_ansi_escapes():
    assert _ANSI_ESCAPE not in render_reassure_import(_payload())


# ===== (b) a byte-identical re-import =====


def test_duplicate_import_matches_golden(request):
    payload = _payload(
        already_imported=True,
        entries_imported=0,
        duration_samples_imported=0,
        count_samples_imported=0,
        entries_with_render_issues=0,
    )
    _assert_or_update_golden(
        request, "reassure_import_duplicate.txt", render_reassure_import(payload)
    )


def test_duplicate_import_says_so_instead_of_printing_four_zeros():
    """Every `*_imported` counter is `0` by construction on a duplicate, so the
    old view rendered a wall of zeros that read like a FAILED import."""

    actual = render_reassure_import(
        _payload(
            already_imported=True,
            entries_imported=0,
            duration_samples_imported=0,
            count_samples_imported=0,
            entries_with_render_issues=0,
        )
    )

    assert "already imported — nothing re-persisted" in actual
    assert "duration samples" not in actual
    assert "entries with render issues" not in actual


# ===== (c) nothing recovered =====


def test_empty_import_matches_golden(request):
    payload = _payload(
        entries_imported=0,
        entries_skipped=0,
        duration_samples_imported=0,
        count_samples_imported=0,
        entries_with_render_issues=0,
    )
    _assert_or_update_golden(request, "reassure_import_empty.txt", render_reassure_import(payload))


# ===== guards on the restyle =====


def test_the_machine_contract_does_not_leak_into_the_human_view():
    """The whole reason this renderer exists. The old view printed the payload
    back as `key: value` lines, so a reader had to know the schema — including
    `already_imported: False`, a Python bool addressed to a person."""

    actual = render_reassure_import(_payload())

    for key in (
        "already_imported",
        "entries_imported",
        "entries_skipped",
        "duration_samples_imported",
        "count_samples_imported",
        "entries_with_render_issues",
        "content_hash",
        "schema_version",
    ):
        assert key not in actual, f"the payload key {key!r} leaked into the pretty view"


def test_render_is_wrapped_in_an_open_right_box():
    actual = render_reassure_import(_payload())

    assert actual.startswith(f"┌─ perfvibe reassure-import · current · {_PATH}\n")
    assert actual.rstrip("\n").endswith("└─")
    body = actual.splitlines()[1:-1]
    assert all(line.startswith("│") for line in body)
    content = [line for line in body if line.strip() != "│"]
    assert not any(line.rstrip().endswith("│") for line in content), "the box stays open-right"


def test_the_content_hash_is_truncated_to_a_recognizable_prefix():
    """Enough to recognize the same file across two runs, nowhere near enough
    to invite parsing it out of a view that is documented as unparseable."""

    actual = render_reassure_import(_payload())

    assert f"content {_HASH[:12]}" in actual
    assert _HASH not in actual


def test_skipped_lines_point_at_the_stderr_detail():
    """The per-line reasons are stderr-only by contract, so a bare count here
    would be a dead end."""

    assert "6 line(s) skipped — reasons on stderr" in render_reassure_import(_payload())


def test_no_skip_line_at_all_when_nothing_was_skipped():
    assert "skipped" not in render_reassure_import(_payload(entries_skipped=0))


def test_a_render_issue_finding_is_the_only_painted_counter():
    """Three of the four counters are VOLUMES; `entries with render issues` is
    a finding — reassure spotted an extra render on mount — and a reader
    scanning four numbers has no other reason to stop on it."""

    actual = render_reassure_import(_payload(entries_with_render_issues=2), color=True)

    assert f"{YELLOW}      2{RESET}" in actual or f"{YELLOW}2{RESET}" in actual
    # The volumes stay unpainted: no color code immediately wrapping them.
    assert f"{YELLOW}40{RESET}" not in actual
    assert f"{YELLOW}     40{RESET}" not in actual


def test_zero_render_issues_is_not_painted():
    actual = render_reassure_import(_payload(entries_with_render_issues=0), color=True)

    assert YELLOW not in actual


def test_one_entry_is_singular():
    """`1 entries imported` is the kind of detail that makes a tool feel
    unfinished."""

    assert "1 entry imported" in render_reassure_import(_payload(entries_imported=1))


def test_color_off_emits_no_ansi_in_any_outcome():
    payloads = [
        _payload(),
        _payload(already_imported=True, entries_imported=0),
        _payload(entries_imported=0, entries_skipped=0),
        _payload(entries_with_render_issues=9),
    ]

    for payload in payloads:
        assert _ANSI_ESCAPE not in render_reassure_import(payload, color=False)


def test_color_on_never_leaves_an_unclosed_escape():
    """Every painted span has to close: a `DIM` that never resets bleeds into
    the rest of the terminal session."""

    actual = render_reassure_import(_payload(), color=True)

    assert actual.count(RESET) == actual.count(DIM) + actual.count(YELLOW)


def test_recovering_nothing_is_not_marked_as_a_success():
    """A readable file with zero recovered entries exits 0 and warns on stderr,
    so it is not a failure — but a `✓` beside `0 entries imported` would claim
    more than happened."""

    from perf.cli.output.primitives import GLYPH_NEUTRAL, GLYPH_OK

    empty = render_reassure_import(_payload(entries_imported=0))
    fresh = render_reassure_import(_payload(entries_imported=4))

    assert f"{GLYPH_NEUTRAL}  0 entries imported" in empty
    assert GLYPH_OK not in empty
    assert f"{GLYPH_OK}  4 entries imported" in fresh
