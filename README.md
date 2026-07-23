# perf-vibe

`perfvibe` — a **local-first performance lab CLI**. It drives a Maestro flow N
times on a fixed device, captures in-app `[PERF]` markers plus Flashlight system
samples (FPS/CPU/RAM), and persists each run to a local SQLite store for later
comparison against history. Lab-only, pre-merge complement to Embrace real-user
monitoring — no network telemetry, no cloud store, no PII.

> The command is `perfvibe` (not `perf`) so it never collides with the Linux
> kernel profiler `perf`. The Python package is `perf` internally.

**Machine contract:** for scripts / CI / AI, always pass `--json` and parse that.
The pretty terminal view is for humans and is not a stable contract — never parse
it. See [`AGENTS.md`](./AGENTS.md).

## Install

### One-liner (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/gedu/perf-vibe/main/install.sh | bash
perfvibe --help
```

This installs the `perfvibe` command globally and isolated via [`pipx`](https://pipx.pypa.io),
straight from the Git repo (no PyPI publish needed). It requires a **Python
3.11+** interpreter on your machine — `perfvibe` is a Python CLI, not a
standalone binary.

### With pipx directly

```bash
pipx install "git+https://github.com/gedu/perf-vibe.git"
```

### From a source checkout (no install)

```bash
python perfvibe-cli.py --help
```

## Try it without a device

`perf run` normally needs a real Android device + `maestro` + `flashlight`. To
**see it work with zero setup**, a `replay` driver runs recorded captures through
the exact production pipeline:

```bash
# globals (--config/--json) go BEFORE the subcommand
perfvibe --config examples/demo-run/perf.toml run demo          # pretty output
perfvibe --json --config examples/demo-run/perf.toml run demo   # machine contract
```

See [`examples/demo-run/`](./examples/demo-run/).

## Usage

```bash
perfvibe run <flow> [n] [--restart] [--device <serial>]
perfvibe --json run <flow>          # stable machine output (schema_version=1)
```

Exit codes: `0` success · `2` usage error · `3` runtime/tooling failure.
(`perf run` never exits `1`; that code is reserved for a future `compare`.)

## Development

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Conventions live in [`AGENTS.md`](./AGENTS.md) and the project skills under
[`.claude/skills/`](./.claude/skills/). The Spec-Driven Development record for
the shipped `perf run` capability is in
[`docs/specs/perf-run/`](./docs/specs/perf-run/).
