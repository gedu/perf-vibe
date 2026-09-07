"""Unit tests for the PR5 pure/testable helpers behind `reassure run` (D6,
the last slice of this capability): `run_reassure` (`cli/commands/
reassure.py`, A12 — the ONE seam in this whole capability that spawns an
external process) and `detect_package_manager` (`cli/commands/init.py`,
the reassure wizard block's package-manager detection). RED-before-GREEN:
written before either function exists.

`run_reassure` itself performs NO import logic — it is a thin, directly
unit-testable wrapper around the house `SubprocessRunner.run_streamed`
seam (`adapters/process.py:154`). It always passes `argv` through as a
LIST (never a shell string, tasks.md 5.1) and relays every streamed line to
`on_line` unchanged; a non-zero `returncode` is surfaced as-is, unexamined —
turning that into "no import attempted" is `reassure run`'s job (the CLI
command), covered end-to-end in `tests/integration/test_cli_reassure_run.py`.
"""

from __future__ import annotations

from pathlib import Path

from fakes import FakeSubprocessRunner
from perf.cli.commands.init import detect_package_manager
from perf.cli.commands.reassure import run_reassure

# ===== run_reassure (A12) =====


def test_run_reassure_relays_argv_as_a_list_never_a_shell_string():
    runner = FakeSubprocessRunner(returncode=0)
    lines: list[str] = []

    result = run_reassure(runner, ["npx", "reassure"], on_line=lines.append)

    assert result.returncode == 0
    assert runner.run_streamed_calls == [["npx", "reassure"]]
    assert isinstance(runner.run_streamed_calls[0], list)


def test_run_reassure_relays_every_streamed_line_to_on_line_in_order():
    runner = FakeSubprocessRunner(streamed_lines=("line one", "line two", "line three"))
    lines: list[str] = []

    run_reassure(runner, ["npx", "reassure"], on_line=lines.append)

    assert lines == ["line one", "line two", "line three"]


def test_run_reassure_surfaces_a_non_zero_returncode_unchanged():
    """`run_reassure` performs no import logic itself and makes no
    judgment about the returncode — it only spawns the subprocess and
    returns the raw result. The caller (`reassure run`) is what turns a
    non-zero code into 'no import attempted' (exit 3)."""
    runner = FakeSubprocessRunner(returncode=1, stderr="boom")

    result = run_reassure(runner, ["npx", "reassure"], on_line=lambda _line: None)

    assert result.returncode == 1
    assert result.stderr == "boom"


# ===== detect_package_manager (pure fs inspection, no subprocess) =====


def test_detects_yarn_from_yarn_lock(tmp_path: Path):
    (tmp_path / "yarn.lock").write_text("")
    assert detect_package_manager(tmp_path) == "yarn"


def test_detects_pnpm_from_pnpm_lock(tmp_path: Path):
    (tmp_path / "pnpm-lock.yaml").write_text("")
    assert detect_package_manager(tmp_path) == "pnpm"


def test_detects_npm_from_package_lock(tmp_path: Path):
    (tmp_path / "package-lock.json").write_text("")
    assert detect_package_manager(tmp_path) == "npm"


def test_defaults_to_npm_when_no_lockfile_present():
    # The documented default when none of the three known lockfiles exist —
    # matches `("npx", "reassure")`, `PerfConfig.reassure_command`'s own
    # default (`config/loader.py`).
    assert detect_package_manager(Path("/nonexistent/definitely/not/a/real/dir")) == "npm"


def test_yarn_lock_wins_when_multiple_lockfiles_coexist(tmp_path: Path):
    (tmp_path / "yarn.lock").write_text("")
    (tmp_path / "package-lock.json").write_text("")
    assert detect_package_manager(tmp_path) == "yarn"
