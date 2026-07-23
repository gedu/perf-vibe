"""`RunContextProvider` port adapter — bash-owned env facts (git/adb via
argv-list subprocess) + app-owned `[PERF-META]` logcat line (design
§"Run Context and Regression-Enabling Metadata").

`is_dev_bundle` originates ONLY from a captured `[PERF-META]` JSON line —
NEVER inferred from any other signal (spec requirement / model.py
docstring). All `git`/`adb` invocations are argv lists, never shelled as a
string (SKILL rule 5); a missing git repo or unreachable device degrades
the corresponding field to `None`, it never raises. This EXPLICITLY
includes the `adb`/`git` BINARY itself being absent from `PATH`
(`FileNotFoundError`/`OSError` from `subprocess.run`) — `_adb_getprop`/
`_git_field` catch `OSError` around the runner call so an uncaught
FileNotFoundError never bubbles up and breaks the "never raises" contract
(CRITICAL resilience fix; an uncaught exception here could reach Python's
default exit code 1, forbidden by SKILL rule 7).

Protocol note: `domain/ports.py` declares `RunContextProvider.context(self)
-> RunContext` with no parameters. This adapter extends `context()` with
an OPTIONAL `logcat_lines` parameter (default `()`), which stays
Protocol-compatible — callers invoking `context()` with zero args still
work; the widened signature only ADDS an optional keyword, it never
requires one. PR3's use-case already holds `DriverResult.logcat_lines` by
the time it assembles context (design §1 step 11 runs right after markers
are parsed in step 9) and SHOULD pass it through so `[PERF-META]` is
captured; omitting it degrades gracefully to
`app_version=None`/`is_dev_bundle=None`/`bundle_source=None`, never a
crash.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence

from perf.adapters.process import SubprocessRunner
from perf.domain.model import RunContext

_PERF_META_TAG = "[PERF-META]"


class BashRunContextProvider:
    """`RunContextProvider` (`domain/ports.py`) implementation."""

    def __init__(
        self,
        *,
        build_variant: str | None = None,
        tool_version: str = "0.0.0",
        device: str | None = None,
        repo_path: str | None = None,
        runner: SubprocessRunner | None = None,
    ) -> None:
        self._build_variant = build_variant
        self._tool_version = tool_version
        self._device = device
        self._repo_path = repo_path
        self._runner = runner if runner is not None else SubprocessRunner()

    def context(self, logcat_lines: Sequence[str] = ()) -> RunContext:
        git_commit = self._git_field(["rev-parse", "HEAD"])
        git_branch = self._git_field(["rev-parse", "--abbrev-ref", "HEAD"])

        model = self._adb_getprop("ro.product.model") or "unknown"
        os_version = self._adb_getprop("ro.build.version.release") or "unknown"
        is_emulator = self._adb_getprop("ro.kernel.qemu") in ("1", "true")

        device_key = f"{model}|{os_version}|{'emulator' if is_emulator else 'physical'}"
        source = "ci" if os.environ.get("CI") else f"local:{os.environ.get('USER', 'unknown')}"

        app_version, is_dev_bundle, bundle_source = self._parse_perf_meta(logcat_lines)

        return RunContext(
            device_key=device_key,
            model=model,
            os_version=os_version,
            is_emulator=is_emulator,
            source=source,
            git_commit=git_commit,
            git_branch=git_branch,
            app_version=app_version,
            is_dev_bundle=is_dev_bundle,
            bundle_source=bundle_source,
            build_variant=self._build_variant,
            tool_version=self._tool_version,
        )

    def _adb_argv(self, *args: str) -> list:
        argv = ["adb"]
        if self._device is not None:
            argv += ["-s", self._device]
        argv += list(args)
        return argv

    def _adb_getprop(self, prop: str) -> str | None:
        try:
            result = self._runner.run(self._adb_argv("shell", "getprop", prop))
        except OSError:
            # `adb` missing from PATH — degrade to None, honor the
            # documented "never raises" contract (CRITICAL resilience fix).
            return None
        if result.returncode != 0:
            return None
        value = result.stdout.strip()
        return value or None

    def _git_field(self, args: Sequence[str]) -> str | None:
        try:
            result = self._runner.run(["git", *args], cwd=self._repo_path)
        except OSError:
            # `git` missing from PATH — degrade to None, never raise
            # (CRITICAL resilience fix).
            return None
        if result.returncode != 0:
            return None
        value = result.stdout.strip()
        return value or None

    @staticmethod
    def _parse_perf_meta(
        lines: Sequence[str],
    ) -> tuple[str | None, bool | None, str | None]:
        for raw_line in lines:
            stripped = raw_line.strip()
            tag_index = stripped.find(_PERF_META_TAG)
            if tag_index == -1:
                continue

            payload = stripped[tag_index + len(_PERF_META_TAG):].strip()
            try:
                data = json.loads(payload)  # json.loads ONLY, never eval (SKILL rule 5)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(data, dict):
                continue

            app_version = data.get("app_version")
            is_dev_bundle = data.get("is_dev_bundle")
            bundle_source = data.get("bundle_source")
            if is_dev_bundle is not None:
                is_dev_bundle = bool(is_dev_bundle)
            return app_version, is_dev_bundle, bundle_source

        return None, None, None
