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

## Configuring flows

`perfvibe` reads which Maestro flows exist and where their `.yaml` files live
from a `perf.toml` config file's `[flows]` table — one `[flows.<name>]`
sub-table per flow, pointing at that flow's `maestro_path`:

```toml
bundle_id = "com.example.app"

[flows.checkout]
maestro_path = "flows/checkout.yaml"

[flows.login]
maestro_path = "flows/login.yaml"
```

Hand-writing this table works, but `perfvibe init <flows-dir>` scaffolds or
merges it for you: it recursively scans a Maestro flows directory (skipping
any `subflows/` — those are `runFlow` utilities, never top-level flows),
detects a single consistent `appId:` header across the flows as your
`bundle_id`, and writes (or safely merges into) `perf.toml`.

```bash
perfvibe init tests/fixtures/flows --yes --bundle-id com.example.app
```

Add `--force` to overwrite a colliding flow name or a `perf.toml` that
contains hand-written comments (re-serializing always drops comments — this
tool refuses to do that silently). See `perfvibe init --help` for the full
flag list (`--driver`, `--db`, `--bundle-id`, `--force`, `--yes`).

**Adding flows later?** Re-run the same `perfvibe init <flows-dir>` command —
it re-scans the whole directory and merges in any genuinely new flow names,
leaving existing entries untouched. Since `perf.toml` is a plain committed
file, `git diff perf.toml` right after running it is your review of what
changed. Note this is add-only: if an *existing* flow's file moved or you
want to update its `maestro_path`, a plain re-run won't touch that entry —
pass `--force` to overwrite it (which overwrites every colliding name in
that run, not just one).

**CI should read a committed `perf.toml`, not regenerate one at CI time.**
Run `perfvibe init` locally once, review the diff, and commit the resulting
`perf.toml` alongside your Maestro flows — the same way you'd commit any
other config file. `run`/`compare`/`budget-check` in CI then read that
committed file directly; there is no `init` step in the CI pipeline itself.
This keeps the set of flows CI measures explicit and reviewable in the PR
diff, rather than implicitly whatever `init` happens to (re-)discover on a
CI runner.

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
