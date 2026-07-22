"""`scrub_secrets` must redact a forwarded `--env PASSWORD=...` value from
diagnostics in BOTH driver shapes (SKILL rule: never leak secrets).

Regression (PR3 verify, WARNING-1 HIGH): the redactor previously only matched
a STANDALONE `--env` token, so on the primary BCP path (Maestro wrapped by
Flashlight) — where the assignment is nested inside the single
`--testCommand "maestro test <flow> --env PASSWORD=..."` argv element — the
secret was NOT redacted and could leak if Flashlight echoed its testCommand
into stderr.
"""

from __future__ import annotations

from perf.adapters.process import scrub_secrets

SECRET = "s3cr3t-value"


def test_redacts_standalone_env_tokens_driver_managed_path():
    argv = ["maestro", "test", "checkout", "--env", f"PASSWORD={SECRET}"]
    text = f"maestro failed while running: --env PASSWORD={SECRET}"
    scrubbed = scrub_secrets(text, argv)
    assert SECRET not in scrubbed
    assert "***" in scrubbed


def test_redacts_env_nested_in_flashlight_testcommand_tool_managed_path():
    # Flashlight wraps maestro: the secret lives INSIDE the --testCommand string.
    argv = [
        "flashlight",
        "test",
        "--bundleId",
        "com.example.app",
        "--testCommand",
        f"maestro test checkout --env PASSWORD={SECRET}",
        "--iterationCount",
        "10",
    ]
    text = f"flashlight error; testCommand was: maestro test checkout --env PASSWORD={SECRET}"
    scrubbed = scrub_secrets(text, argv)
    assert SECRET not in scrubbed
    assert "***" in scrubbed


def test_noop_when_no_secret_present():
    argv = ["maestro", "test", "checkout"]
    text = "maestro failed: device offline"
    assert scrub_secrets(text, argv) == text
