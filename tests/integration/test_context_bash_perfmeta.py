"""Integration tests for `BashRunContextProvider` (design §"Run Context and
Regression-Enabling Metadata").

RED-before-GREEN: written before
`src/perf/adapters/context_bash_perfmeta.py` existed. A fake subprocess
runner stands in for `git`/`adb` — no real repo or device involved.
`is_dev_bundle` must originate ONLY from a captured `[PERF-META]` line,
never inferred otherwise.
"""

from __future__ import annotations

from perf.adapters.context_bash_perfmeta import BashRunContextProvider
from perf.adapters.process import CommandResult


class _FakeRunner:
    def __init__(self, responses: dict):
        self._responses = responses
        self.calls: list[list[str]] = []

    def run(self, argv, **kwargs):
        self.calls.append(list(argv))
        key = tuple(argv)
        return self._responses.get(key, CommandResult(returncode=1, stdout="", stderr="not mocked"))


def _responses(**overrides) -> dict:
    base = {
        ("git", "rev-parse", "HEAD"): CommandResult(0, "abc123\n", ""),
        ("git", "rev-parse", "--abbrev-ref", "HEAD"): CommandResult(0, "main\n", ""),
        ("adb", "shell", "getprop", "ro.product.model"): CommandResult(0, "Pixel 8 Pro\n", ""),
        ("adb", "shell", "getprop", "ro.build.version.release"): CommandResult(0, "14\n", ""),
        ("adb", "shell", "getprop", "ro.kernel.qemu"): CommandResult(0, "\n", ""),
    }
    base.update(overrides)
    return base


def test_context_assembles_git_and_device_facts_via_argv_subprocess():
    runner = _FakeRunner(_responses())
    provider = BashRunContextProvider(runner=runner, build_variant="release", tool_version="0.1.0")

    ctx = provider.context()

    assert ctx.git_commit == "abc123"
    assert ctx.git_branch == "main"
    assert ctx.model == "Pixel 8 Pro"
    assert ctx.os_version == "14"
    assert ctx.is_emulator is False
    assert ctx.build_variant == "release"
    assert ctx.tool_version == "0.1.0"
    for call in runner.calls:
        assert isinstance(call, list)  # argv-list, never a shell string


def test_context_parses_perf_meta_json_from_logcat_lines():
    runner = _FakeRunner(_responses())
    provider = BashRunContextProvider(runner=runner, build_variant="release", tool_version="0.1.0")
    lines = [
        '[PERF-META] {"app_version":"4.20.1","is_dev_bundle":true,"bundle_source":"embedded"}',
        "[PERF] checkout: 900ms",
    ]

    ctx = provider.context(logcat_lines=lines)

    assert ctx.app_version == "4.20.1"
    assert ctx.is_dev_bundle is True
    assert ctx.bundle_source == "embedded"


def test_context_without_perf_meta_line_leaves_app_fields_none():
    runner = _FakeRunner(_responses())
    provider = BashRunContextProvider(runner=runner, build_variant="release", tool_version="0.1.0")
    ctx = provider.context(logcat_lines=[])
    assert ctx.app_version is None
    assert ctx.is_dev_bundle is None
    assert ctx.bundle_source is None


def test_context_with_no_logcat_lines_argument_still_works_protocol_compatible():
    """`domain/ports.py` declares `context(self) -> RunContext` with no
    params — calling with zero args must still work (the adapter only ADDS
    an optional keyword, it never requires it)."""
    runner = _FakeRunner(_responses())
    provider = BashRunContextProvider(runner=runner, build_variant="release", tool_version="0.1.0")
    ctx = provider.context()
    assert ctx.app_version is None


def test_context_missing_git_repo_yields_none_fields_not_a_crash():
    responses = _responses()
    responses[("git", "rev-parse", "HEAD")] = CommandResult(128, "", "not a git repository")
    responses[("git", "rev-parse", "--abbrev-ref", "HEAD")] = CommandResult(128, "", "not a git repository")
    runner = _FakeRunner(responses)
    provider = BashRunContextProvider(runner=runner, build_variant="release", tool_version="0.1.0")

    ctx = provider.context()
    assert ctx.git_commit is None
    assert ctx.git_branch is None


def test_is_emulator_true_when_qemu_prop_set():
    responses = _responses()
    responses[("adb", "shell", "getprop", "ro.kernel.qemu")] = CommandResult(0, "1\n", "")
    runner = _FakeRunner(responses)
    provider = BashRunContextProvider(runner=runner, build_variant="release", tool_version="0.1.0")
    ctx = provider.context()
    assert ctx.is_emulator is True


class _MissingBinaryRunner:
    """Simulates `adb`/`git` absent from PATH — real `subprocess.run` raises
    `FileNotFoundError` (a subclass of `OSError`) in that case."""

    def run(self, argv, **kwargs):
        raise FileNotFoundError(f"[Errno 2] No such file or directory: {argv[0]!r}")


def test_missing_binary_degrades_all_fields_to_none_never_raises():
    """Fix (CRITICAL resilience review): the module docstring promises
    'degrades... to None, it never raises' — an uncaught FileNotFoundError
    when adb/git is missing from PATH breaks that contract and could bubble
    to Python's default exit code 1 (forbidden by SKILL rule 7: `run` must
    NEVER emit 1)."""
    runner = _MissingBinaryRunner()
    provider = BashRunContextProvider(runner=runner, build_variant="release", tool_version="0.1.0")

    ctx = provider.context()  # must not raise

    assert ctx.git_commit is None
    assert ctx.git_branch is None
    assert ctx.model == "unknown"
    assert ctx.os_version == "unknown"
    assert ctx.is_emulator is False
