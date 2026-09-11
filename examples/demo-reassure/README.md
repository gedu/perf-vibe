# Device-free `reassure` demo

This directory lets you SEE the whole `reassure` loop end-to-end — recorded
`.perf` files -> `SqliteStore` -> roster, per-test detail, chart, and a real
direction-aware verdict -> exit code — **without a device, without Node, and
without `@callstack/reassure` installed**.

reassure measures React render timings from a JavaScript test run, so its
input is a `.perf` file rather than anything a phone produces. That makes the
whole capability demonstrable from four recorded fixtures.

`seed.py` imports them through the REAL path `perfvibe reassure import` uses
(`build_reassure_parser().parse()` then `Store.save_reassure_import()`), never
a shortcut around it:

- 3 baseline imports (`c0000001`..`c0000003`, Jan-Mar) where
  `CartPanel renders` sits at ~100ms with one render and a clean mount
  diagnostic.
- 1 latest import (`c9999999`, Apr) where `CartPanel renders` regresses on
  BOTH series — ~100ms to ~140ms, one render to two — and gains an extra
  render on mount, while `Header renders` stays exactly where it was.

Three baselines is the minimum. `MIN_BASELINE_IMPORTS` is 3, and below it
every verdict reports `insufficient-data` rather than a silent `stable`, so
this is the smallest history that can produce a verdict at all.

`Header renders` exists to prove the signal discriminates. A demo where
everything regresses cannot show the difference between a finding and noise.

## Seed it

From the repo root:

```sh
python examples/demo-reassure/seed.py
```

Safe to re-run: the database is recreated each time, and reassure imports are
idempotent by content hash anyway.

## Then read it back

From the repo root. Every command takes the same `--config`, written out in
full rather than held in a shell variable — an unquoted `$VAR` word-splits in
bash but not in zsh, so the shorthand silently breaks on a default macOS shell.

```sh
perfvibe --config examples/demo-reassure/perfvibe.toml reassure list
perfvibe --config examples/demo-reassure/perfvibe.toml reassure entries 4
perfvibe --config examples/demo-reassure/perfvibe.toml reassure show "CartPanel renders"
perfvibe --config examples/demo-reassure/perfvibe.toml reassure history "CartPanel renders"
perfvibe --config examples/demo-reassure/perfvibe.toml reassure compare "CartPanel renders"
perfvibe --config examples/demo-reassure/perfvibe.toml reassure compare "Header renders"
```

## What to notice

**`compare` exits `0` on that regression.** It reports and never gates —
`budget-check` is the only command in this tool that exits `1`. Read the
verdict out of `--json`'s `verdicts[].status`, never the exit code:

```sh
perfvibe --json $C reassure compare "CartPanel renders" | jq '.verdicts[].status'
```

**The two series are never paired.** `entries` shows `DUR N` beside `CNT N`
in separate column groups, and `history` draws two independent charts.
reassure builds durations from the outlier-filtered set and counts from the
unfiltered one, so they have different lengths and describe different runs.

**The mount-render line is a state, not a percentage.** `0 -> 1` means an
extra render on mount appeared. A component that never reported the
diagnostic at all renders as unavailable, never as a clean zero — "we never
measured this" must not read as "we measured it and found nothing".

## Where the real thing differs

In a real project you would not import fixtures by hand. Either point
`reassure_path` at your own `.reassure/current.perf` and run
`perfvibe reassure import`, or let the tool drive the whole thing:

```sh
perfvibe reassure run     # runs reassure_command, then imports what it produced
```

`reassure_command` is a TOML array (`["npx", "reassure"]` by default), never a
string that gets split — so there is no shell-quoting surface to get wrong.
