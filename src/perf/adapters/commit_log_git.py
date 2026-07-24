"""`CommitLog` port adapter — git (budget-check design §4/§10, decision D6).

`subject(sha)` runs `git log -1 --format=%s <sha>` as an argv LIST via
`SubprocessRunner` — NEVER `shell=True`, never string composition (SKILL
rule 5), mirroring `context_bash_perfmeta.py`'s `_git_field` discipline.
Fail-graceful: ANY failure (non-zero exit, missing repo, unknown sha, `git`
absent from PATH, a runner timeout, empty/whitespace-only stdout) degrades
to `None` — this adapter NEVER raises to its caller (spec 'Git Context on
Regression': the detail view falls back to sha-only display, it never
crashes or aborts the whole command).
"""

from __future__ import annotations

from perf.adapters.process import SubprocessRunner


class GitCommitLog:
    """`CommitLog` (`domain/ports.py`) implementation."""

    def __init__(
        self,
        *,
        repo_path: str | None = None,
        runner: SubprocessRunner | None = None,
    ) -> None:
        self._repo_path = repo_path
        self._runner = runner if runner is not None else SubprocessRunner()

    def subject(self, sha: str) -> str | None:
        try:
            result = self._runner.run(
                ["git", "log", "-1", "--format=%s", sha], cwd=self._repo_path
            )
        except OSError:
            # `git` missing from PATH, or any other subprocess-level
            # failure — degrade to None, never raise (fail-graceful
            # contract, SKILL rule: no adapter ever crashes the CLI).
            return None
        if result.returncode != 0:
            return None
        subject = result.stdout.strip()
        return subject or None
