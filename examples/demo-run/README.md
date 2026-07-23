# Device-free `perf run` demo

This directory lets you SEE `perf run` work end-to-end — marker parse ->
Flashlight parse -> `SqliteStore` -> confirmation output — **without a
physical or emulated device**. `driver = "replay"` (`ReplayDriver`) replays
two recorded fixtures through the exact same production pipeline every
other driver uses:

- `logcat.txt` — a recorded `ReactNativeJS` logcat capture with both marker
  forms (`[PERF] <name>: <n>ms` and `[PERF] {json}`) and a `[PERF-META]`
  line.
- `flashlight.json` — a minimal, real-shape Flashlight report (2 iterations).

## Run it

From the repo root (paths in `perf.toml` are relative to the current
working directory):

```sh
# Pretty (human) output
./.venv/bin/perf run demo --config examples/demo-run/perf.toml

# Machine --json contract
./.venv/bin/perf run demo --config examples/demo-run/perf.toml --json
```

Both commands exit `0` and persist exactly one run into
`examples/demo-run/perf.db` (the config's `db_path`, unless overridden with
`--db`).

## Inspect the persisted run

```sh
./.venv/bin/perf run demo --config examples/demo-run/perf.toml --db examples/demo-run/perf.db
sqlite3 examples/demo-run/perf.db "SELECT * FROM run;"
sqlite3 examples/demo-run/perf.db "SELECT * FROM measure;"
```

No device, no `adb`, no `maestro`, no `flashlight` binary is invoked — only
the two recorded fixture files above are read.
