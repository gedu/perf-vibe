# perf-vibe

**Catch mobile performance regressions before they merge.**

`perfvibe` runs a [Maestro](https://maestro.mobile.dev) flow on an Android device
N times, measures it (in-app `[PERF]` markers **plus** [Flashlight](https://docs.flashlight.dev)
FPS / CPU / RAM), saves every run to a local SQLite file, and tells you whether your
branch got slower than its own history — with a CI gate that **fails the build on a
real regression**.

It's a **local, pre-merge lab** — a complement to real-user monitoring like Embrace,
not a replacement. **Nothing leaves your machine:** no network calls, no cloud store,
no telemetry. Your runs live in a gitignored `*.db` file, tagged `local:$USER` so you
can tell yours apart from CI's.

```text
! checkout                 1310.0 vs 812.0      ms   ↑   +61.3%  REGRESSION       ▁▁▁▁█
  ttfp                       421.0 vs 430.0     ms   ↓    -2.1%  STABLE           ████▁
  fps_avg                     58.1 vs 58.2      fps  ↓    -0.2%  STABLE           ████▁
```

> **Why `perfvibe` and not `perf`?** The command is `perfvibe` so it never collides
> with the Linux kernel profiler `perf`. (The Python package is named `perf` internally —
> you'll never type that.)

---

## The 30-second tour (no device needed)

`perfvibe run` normally needs a real Android device, `maestro`, and `flashlight`.
To see the whole pipeline work with **none** of that, a `replay` driver feeds recorded
captures through the exact same production code path. Install first (see below), then:

```bash
# 1. Measure a flow → persist one run.  Global flags go BEFORE the subcommand.
perfvibe --config examples/demo-run/perfvibe.toml run demo
```
```text
✓ perf run complete — run #1
  flow:       demo
  mode:       warm (n=2)
  source:     local:eduardograciano
  markers:
    checkout: n=2 avg=801.0ms
    ttfp: n=2 avg=417.5ms
  flashlight (per-iteration aggregates):
    fps avg: 57.4   ram peak: 222.5MB   cpu avg: 34.3%
```

```bash
# 2. Compare a branch against its history → a per-metric, direction-aware verdict.
#    (compare needs history, so seed a few recorded runs first — safe to re-run.)
python examples/demo-compare/seed.py
perfvibe --config examples/demo-compare/perfvibe.toml compare demo
```
```text
! checkout                 1310.0 vs 812.0      ms   ↑   +61.3%  REGRESSION       ▁▁▁▁█
  ttfp                       421.0 vs 430.0     ms   ↓    -2.1%  STABLE           ████▁
  ram_peak_mb                205.0 vs 206.0     mb   ↓    -0.5%  STABLE           ████▁
! total_time_ms            1310.0 vs 805.0      ms   ↑   +62.7%  REGRESSION       ▁▁▁▁█
  fps_avg                     58.1 vs 58.2      fps  ↓    -0.2%  STABLE           ████▁

✓ reasonable — 0 of 4 runs would flag
```

```bash
# 3. Gate CI on it → exits 1 on a confirmed regression, so the build fails.
perfvibe --config examples/demo-compare/perfvibe.toml budget-check demo
echo "exit: $?"   # → 1
```
```text
│   ✗  checkout          1310.0 ms     812.0 ms   ↑ +61.3%  REGRESSION         ▁▁▁▁█
│   ✓  ttfp               421.0 ms     430.0 ms    ↓ -2.1%  stable             ████▁
│   ✗  total_time_ms     1310.0 ms     805.0 ms   ↑ +62.7%  REGRESSION         ▁▁▁▁█
├──────────────────────────────────────────────────────────────────────────────────
│   ✗  GATE FAILED   ·   2 metrics regressed   ·   exit 1
```

> These three demos are real and re-runnable — see
> [`examples/`](./examples/). No device, `adb`, `maestro`, or `flashlight` binary
> is invoked; only recorded fixtures are read.

---

## Install

**One-liner (recommended)** — installs the `perfvibe` command globally and isolated
via [`pipx`](https://pipx.pypa.io), straight from Git (no PyPI needed). Requires a
**Python 3.11+** interpreter; `perfvibe` is a Python CLI, not a standalone binary.

```bash
curl -fsSL https://raw.githubusercontent.com/gedu/perf-vibe/main/install.sh | bash
perfvibe --help
```

<details>
<summary>Other ways to install</summary>

**With pipx directly:**
```bash
pipx install "git+https://github.com/gedu/perf-vibe.git"
```

**From a source checkout** (`perfvibe-cli.py` is a thin launcher, but the CLI still
needs its `typer` dependency, so install into a venv first):
```bash
python3.11 -m venv .venv          # any Python 3.11+ works — see Development
./.venv/bin/pip install -e .
./.venv/bin/perfvibe --help        # or: ./.venv/bin/python perfvibe-cli.py --help
```
</details>

---

## The five commands

| Command | What it does | Exits `1`? |
|---|---|:--:|
| `perfvibe run <flow> [n]` | Measure a flow and **persist** one run. | never |
| `perfvibe compare <flow>…` | Show a **verdict** vs. history (per metric, direction-aware). Read-only. | never |
| `perfvibe budget-check <flow>` | The **CI gate** — reuses `compare`'s verdict; any regression fails. | **on regression** |
| `perfvibe history <flow>` | Export a flow's full run series (machine-readable chart data). | never |
| `perfvibe init <flows-dir>` | Scaffold or merge the `perfvibe.toml` flow config. | never |

Only `budget-check` ever exits `1`. `run` persists, `compare`/`history` report, and
`init` configures — none of them gate, so a regression under `compare` still exits `0`.
**Full flags, JSON payloads, and per-command detail live in
[`docs/commands.md`](./docs/commands.md).**

### Exit codes (the whole contract)

| Code | Meaning |
|:--:|---|
| `0` | Success (including a `budget-check` gate that **passes** or is skipped) |
| `1` | **`budget-check` only** — a confirmed regression (or, under `--strict`, an unprovable-safety case) |
| `2` | Usage error (unknown flow/metric, bad config, flags in the wrong place) |
| `3` | Runtime / tooling failure (device, `adb`, `maestro`, `flashlight`, git, DB) |

**Getting `INSUFFICIENT-DATA` in `compare` even after several runs?** The
baseline is per-commit, not per-run, and an uncommitted tree gets tagged
`<sha>-dirty` so it can't quietly pollute a real commit's history. See
**[`docs/baselines-and-history.md`](./docs/baselines-and-history.md)**.

---

## The machine contract: always `--json`

For scripts, CI, or an AI agent, **always pass `--json` and parse that.** The pretty
terminal view (sparklines, color, confirmation text) is for humans and is **not a
stable contract** — never parse it. Every `--json` payload carries a `schema_version`
you can branch on. See [`AGENTS.md`](./AGENTS.md) and [`docs/commands.md`](./docs/commands.md).

```bash
perfvibe --json --config examples/demo-compare/perfvibe.toml budget-check demo
```
```json
{ "schema_version": 1, "gate_status": "fail",
  "offending_metrics": ["checkout", "total_time_ms"], "strict": false, "verdicts": [ … ] }
```

---

## Configuring flows

`perfvibe` learns which Maestro flows exist from a `perfvibe.toml` file's `[flows]`
table — one sub-table per flow, pointing at its `.yaml`:

```toml
bundle_id = "com.example.app"

[flows.checkout]
maestro_path = "flows/checkout.yaml"

[flows.login]
maestro_path = "flows/login.yaml"
```

You can hand-write it, but `perfvibe init <flows-dir>` scans your flows directory,
detects the `bundle_id`, and writes (or safely merges into) the file for you:

```bash
perfvibe init tests/fixtures/flows --yes --bundle-id com.example.app
```

**Commit `perfvibe.toml`** alongside your flows and let CI read it — don't regenerate
it at CI time. The full story on `init` (adding/removing flows, `--force`,
`--prune-missing`, the comment-loss guard, CI guidance) is in
**[`docs/configuring-flows.md`](./docs/configuring-flows.md).**

---

## Development

```bash
python3.11 -m venv .venv        # any Python 3.11+ works (python3.12/3.13, or `brew install python@3.11`)
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

CI runs lint (`ruff`), format check, type check (`mypy`) and the suite with a **93%
coverage floor** on every push and PR — run the same locally before opening one, see
[`CONTRIBUTING.md`](./CONTRIBUTING.md). Conventions live in [`AGENTS.md`](./AGENTS.md)
and the project skills under [`.claude/skills/`](./.claude/skills/). Spec-Driven
Development records for each shipped capability are in [`docs/specs/`](./docs/specs/),
with the canonical current specs in [`openspec/specs/`](./openspec/specs/).
