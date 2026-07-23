# perf CLI — agent instructions

**Always run `perf` with `--json` and parse that output.** The pretty
terminal view (sparklines, color, human confirmation text) is lossy and NOT a
stable contract — it may change without notice. Never parse the pretty view;
only the `--json` payload (`schema_version`-carrying) is machine-safe.

`perfvibe run` is persist-only and `perfvibe compare` is show-only: both exit
`0` on success, `2` on a usage error, and `3` on any runtime/tooling failure.
**Neither ever exits `1`** — a `compare` regression still exits `0`, because
`compare` reports and does not gate. Exit `1` is reserved for a future
`budget-check` CI gate. Never treat a non-zero exit as "regression found";
read the verdict out of the `--json` payload.

See `AGENTS.md` for project skill registration and coding standards.
