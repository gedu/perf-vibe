# perf-vibe

`perf` — a local-first performance lab CLI. Drives a Maestro flow N times on a
fixed device, captures in-app `[PERF]` markers plus Flashlight system samples
(FPS/CPU/RAM), and persists each run to a local SQLite store for later
comparison against history. Lab-only, pre-merge complement to Embrace
real-user monitoring — no network telemetry, no cloud store.

**Machine contract: always use `--json` and parse that.** The pretty
terminal view is for humans and is not a stable contract — never parse it.
See `CLAUDE.md` / `AGENTS.md`.

## Status

Greenfield — under active Spec-Driven Development. The `run` capability is
being delivered as three sequential PRs:

1. **PR1 (this PR)**: project skeleton, pure domain model + ports, DB schema
   and initial migration.
2. **PR2**: adapters (Maestro driver, logcat marker source, Flashlight
   sampler, run-context provider, SQLite store).
3. **PR3**: application use-case + CLI wiring.

## Install

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

```bash
perf run <flow> [n] [--mode warm|cold] [--restart] --json
```

Not yet runnable end-to-end — the CLI entry point lands in PR3.

## Development

```bash
pytest
```
