# perf-vibe

`perfvibe` — a **local-first performance lab CLI**. It drives a Maestro flow N
times on a fixed device, captures in-app `[PERF]` markers plus Flashlight system
samples (FPS/CPU/RAM), and persists each run to a local SQLite store for later
comparison against history. Lab-only, pre-merge complement to Embrace real-user
monitoring — no network telemetry, no cloud store, nothing leaves your machine.
(Runs are tagged `local:$USER` so you can tell yours apart from CI's; that stays
in your local SQLite file, which is gitignored.)

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

### From a source checkout

`perfvibe-cli.py` is a thin launcher, but the CLI still needs its dependency
(`typer`), so install into a venv first:

```bash
python3.11 -m venv .venv                 # see Development if python3.11 is missing
./.venv/bin/pip install -e .
./.venv/bin/perfvibe --help              # or: ./.venv/bin/python perfvibe-cli.py --help
```

## Try it without a device

`perfvibe run` normally needs a real Android device + `maestro` + `flashlight`.
To see it work without any of that, a `replay` driver runs recorded captures
through the exact production pipeline.

This needs `perfvibe` on your PATH — do one of the Install steps above first, or
run it straight from a source checkout with the venv from the previous section:

```bash
# globals (--config/--json) go BEFORE the subcommand
perfvibe --config examples/demo-run/perf.toml run demo          # pretty output
perfvibe --json --config examples/demo-run/perf.toml run demo   # machine contract

# no install? from a source checkout, same thing via the launcher:
./.venv/bin/python perfvibe-cli.py --config examples/demo-run/perf.toml run demo
```

There is a second, seeded demo that shows `compare` computing a real regression
verdict — see [`examples/demo-compare/`](./examples/demo-compare/) — a third
that shows `budget-check` gating on that same regression and exiting `1` — see
[`examples/demo-budget-check/`](./examples/demo-budget-check/) — and the `run`
demo lives in [`examples/demo-run/`](./examples/demo-run/).

## Usage

```bash
perfvibe run <flow> [n] [--restart] [--device <serial>]     # measure and persist
perfvibe compare <flow>                                     # verdict vs history
perfvibe budget-check <flow> [--strict] [--metric <name>] [--verbose] [--restart] [--device <serial>]
perfvibe --json run <flow>          # stable machine output (schema_version=1)
perfvibe --json compare <flow>
perfvibe --json budget-check <flow>
```

`run` persists a run. `compare` reads that history and shows a per-metric,
direction-aware verdict (median-by-commit baseline, sparklines, `--json`).
`budget-check` reuses `compare`'s verdict and applies ONE gate rule: any
`regression` fails the flow. It is the CI-gating command — `run` and `compare`
never exit `1`, `budget-check` does.

Exit codes: `0` success (or a `budget-check` gate `pass`/`skipped`) · `1`
**`budget-check` only** — a confirmed regression (or, under `--strict`, an
unprovable-safety case) · `2` usage error · `3` runtime/tooling failure.
`run` and `compare` never exit `1` — `compare` is show-only, so even a
regression exits `0`; `budget-check` is what spends the CI-gating exit `1`.

## Development

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

No `python3.11`? Any Python **3.11+** works — try `python3.12`/`python3.13`, or
install one (`brew install python@3.11` on macOS). `install.sh` does this
discovery automatically if you prefer the one-liner above.

CI runs lint (`ruff`), format check, type check (`mypy`) and the suite with a
93% coverage floor on every push and PR. Run the same locally before opening
one — see [`CONTRIBUTING.md`](./CONTRIBUTING.md).

Conventions live in [`AGENTS.md`](./AGENTS.md) and the project skills under
[`.claude/skills/`](./.claude/skills/). Spec-Driven Development records for the
shipped capabilities are in [`docs/specs/`](./docs/specs/) (`perf-run`,
`compare`), with the canonical current specs in
[`openspec/specs/`](./openspec/specs/).
