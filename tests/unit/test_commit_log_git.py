"""Unit tests for `GitCommitLog` (budget-check design §4/§10, task 1.11) —
the `CommitLog` port's ONE adapter. A fake `SubprocessRunner` stands in for
`git`; no real repo is touched. Fail-graceful contract: `subject(sha)`
NEVER raises, it returns `None` on any failure (missing repo, unknown sha,
non-zero exit, the runner itself raising, or empty/whitespace-only stdout).
"""

from __future__ import annotations

from perf.adapters.commit_log_git import GitCommitLog
from perf.adapters.process import CommandResult


class _FakeRunner:
    def __init__(self, *, result: CommandResult | None = None, raises: Exception | None = None):
        self._result = result
        self._raises = raises
        self.calls: list[tuple[list[str], dict]] = []

    def run(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if self._raises is not None:
            raise self._raises
        assert self._result is not None
        return self._result


def test_subject_invokes_git_log_dash1_format_subject_as_argv_list():
    runner = _FakeRunner(
        result=CommandResult(returncode=0, stdout="Fix checkout regression\n", stderr="")
    )
    commit_log = GitCommitLog(repo_path="/repo", runner=runner)

    subject = commit_log.subject("abc123")

    assert subject == "Fix checkout regression"
    assert len(runner.calls) == 1
    argv, kwargs = runner.calls[0]
    assert argv == ["git", "log", "-1", "--format=%s", "abc123"]
    assert kwargs.get("cwd") == "/repo"  # argv-list, never shell=True


def test_subject_returns_none_on_nonzero_returncode():
    runner = _FakeRunner(result=CommandResult(returncode=128, stdout="", stderr="unknown revision"))
    commit_log = GitCommitLog(runner=runner)
    assert commit_log.subject("deadbeef") is None


def test_subject_returns_none_when_runner_raises_never_propagates():
    runner = _FakeRunner(raises=OSError("git not found"))
    commit_log = GitCommitLog(runner=runner)
    assert commit_log.subject("abc123") is None


def test_subject_returns_none_on_empty_stdout():
    runner = _FakeRunner(result=CommandResult(returncode=0, stdout="", stderr=""))
    commit_log = GitCommitLog(runner=runner)
    assert commit_log.subject("abc123") is None


def test_subject_returns_none_on_whitespace_only_stdout():
    runner = _FakeRunner(result=CommandResult(returncode=0, stdout="   \n", stderr=""))
    commit_log = GitCommitLog(runner=runner)
    assert commit_log.subject("abc123") is None
