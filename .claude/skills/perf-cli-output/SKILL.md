---
name: perf-cli-output
description: "Trigger: CLI output, error/warning message, emit_error, colors, cause/hint, stderr, exit codes in perf-vibe. Keep terminal output consistent."
license: Apache-2.0
metadata:
  author: eduardo-graciano
  version: "1.0"
---

## Activation Contract

Load before writing or editing any user-facing terminal output in `perf-vibe` — errors, warnings, success confirmations, colors — in `cli/commands/*` or `cli/output/*`. Complements `perf-cli-standards` (rule 6/7); this skill is the concrete output contract those rules point at.

## Hard Rules (review-blocking if violated)

1. **Every error goes through `emit_error`.** Import `from perf.cli.output.errors import emit_error` and call `emit_error(output, message, cause=..., hint=...)`. NEVER `typer.echo(f"Error: ...", err=True)` directly — the renderer owns the red/bold `Error:` prefix and color. The `message` carries NO `Error:` prefix.
2. **Two color decisions, never mixed.** Success/pretty output (stdout) uses `output.color_enabled`; errors (stderr) use `output.error_color_enabled`. They differ on purpose — piping stdout to a file must still color errors on an interactive stderr. Both are already resolved in `resolve_output_context`; never re-derive from `isatty()` ad hoc.
3. **Highlight with backticks.** Wrap commands/flags/paths in `` `...` `` in messages and hints (`` pass `--force` ``, `` check `adb devices` ``). They bold on color and unwrap to plain text when off — a literal backtick must NEVER reach the user.
4. **Tool failures: salient line + hint, never a raw dump.** For a subprocess/tool error, pass `cause=salient_tool_line(diag)` and `hint=hint_for_diagnostics(diag)`. Add new device/tool signatures to `_HINTS` in `errors.py`, not inline in a command. Diagnostics MUST already be `bounded_diagnostics`-trimmed and `scrub_secrets`-scrubbed in the adapter before reaching the CLI.
5. **Summarize, don't enumerate.** Report multi-iteration failure as a COUNT (`{succeeded}/{total} iterations succeeded`), never the raw `['failed', …]` list.
6. **Warnings are non-fatal and go to stderr.** `note:` = the non-TTY `--json` nudge; `warning:` = a degradation (e.g. store-close failure) that must NEVER change the exit code. Partial coverage is a payload flag, not an error. A missing device/tool is a real failure (exit 3) — not a warning.
7. **Exit codes (see `perf-cli-standards` rule 7).** `0` ok · `1` regression (`compare`/`budget-check` only) · `2` usage · `3` runtime/tooling. `run` never emits `1`. The pretty view is lossy; only `--json` is machine-safe.

## Decision Gates

| Situation | Emit |
|---|---|
| Bad invocation, config, unknown flow/metric | `emit_error(output, msg)` → exit `2` |
| Tool/device failure with tool stderr | `emit_error(output, msg, cause=salient_tool_line(d), hint=hint_for_diagnostics(d))` → exit `3` |
| Non-fatal degradation (store close, etc.) | `typer.echo("warning: …", err=True)`, exit code unchanged |
| Success (pretty) | stdout via `cli/output/*_pretty.py` using `output.color_enabled` |

## Output Contract

Plain-text (color off) output MUST stay byte-identical to a bare `Error: <message>` so existing assertions/goldens hold. New rendering logic gets a unit test in `tests/unit/test_errors.py`; golden pretty output is color-forced-off.

## References

- `src/perf/cli/output/errors.py` — `render_error`, `emit_error`, `salient_tool_line`, `hint_for_diagnostics`, `_HINTS`.
- `src/perf/cli/output/context.py` — `OutputContext` (`color_enabled` vs `error_color_enabled`), `resolve_output_context`.
- `.claude/skills/perf-cli-standards/SKILL.md` — rule 6 (CLI/`--json`) and rule 7 (exit codes).
