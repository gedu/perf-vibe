# Baselines, history, and the `-dirty` commit tag

`compare` and `budget-check` never grade you against "the last run." They grade
the **latest run** against a **median-by-commit baseline** built from your
flow's history. Whether that baseline is trustworthy — or `compare` falls back
to `INSUFFICIENT-DATA` for every metric — depends entirely on the *shape* of
that history: how many **distinct commits** it spans, not how many times you
hit "run." This doc is the deep dive on that mechanic.

---

## One baseline point per commit, not per run

Re-running the same flow 10 times on the same commit does not give you 10
baseline points. All 10 runs on that commit collapse into **one** point — the
median of those 10 — before anything gets compared. History grows by
**committing and running again**, not by re-running.

```text
run #1  commit abc1234   total_time_ms = 800
run #2  commit abc1234   total_time_ms = 820   ┐
run #3  commit abc1234   total_time_ms = 810   ┘── one baseline point: 810 (median)
```

## The `-dirty` tag: uncommitted work never builds history

Every run records the `git rev-parse HEAD` sha it was measured against. If the
working tree has **any** uncommitted change — staged, unstaged, or
untracked — `perfvibe` appends `-dirty` to that sha before storing it:

```text
clean tree:  abc1234
dirty tree:  abc1234-dirty
```

This exists to stop a modified tree's numbers from silently polluting the
median of the real, committed `abc1234` — a `-dirty` run is measuring code
that was never actually merged, so it must never be folded into that commit's
history.

The consequence people trip over: **as long as you don't commit, every run
shares the exact same `<sha>-dirty` string** — it behaves exactly like
re-running on one commit. Ten runs against the same uncommitted diff still
collapse into one baseline point, same as the clean-commit case above.

```text
run #1  commit abc1234-dirty   total_time_ms = 900
run #2  commit abc1234-dirty   total_time_ms = 950   ┐
run #3  commit abc1234-dirty   total_time_ms = 920   ┘── one baseline point: 920
```

## Your own commit never backs itself

`compare` also drops any history run that shares the **exact same commit
identity** as the run being compared — otherwise a metric would be graded
against itself. This applies identically to real commits and `-dirty` ones. If
your *latest* run is on `abc1234-dirty`, every other run also on
`abc1234-dirty` is excluded from its baseline, even though they're technically
separate invocations — they're the same untested diff.

This is the exact rule behind the note `compare`/`budget-check` prints:

```text
note: 3 run(s) excluded from baseline: 3 on the current commit — commit your changes to grow history
```

That number is *not* "3 runs missing" — it's "3 runs that exist but can't
count, because they're on the same commit you're comparing right now."

## `min_baseline_commits`: the real gate behind `INSUFFICIENT-DATA`

After exclusions, `compare` counts how many **distinct** commits remain in the
baseline. If that count is below `min_baseline_commits` (default **3**), the
metric is graded `INSUFFICIENT-DATA` — full stop, regardless of how many raw
runs are sitting in the database. One commit's worth of history, no matter how
many times you ran it, is one data point; you need three separate commits'
medians before `compare` will render a verdict at all.

Override it at the root of `perfvibe.toml` (alongside `bundle_id`, not nested
under a flow):

```toml
bundle_id = "com.example.app"
min_baseline_commits = 3   # default; raise it for a noisier device/flow
```

## `baseline_n`: how far back history looks

`compare` windows the baseline to the most recent `baseline_n` **distinct
commits** (default **10**), not the most recent N runs. Once you have enough
history, older commits age out of the window on their own — you don't need to
prune anything by hand.

## Walkthrough: watching the numbers change as you commit

Starting from nothing, on the `checkout` flow:

| Step | Action | `git_commit` stored | Distinct baseline commits available | `compare` verdict |
|---|---|---|---|---|
| 1 | `perfvibe run checkout` (dirty tree) ×3 | `abc1234-dirty` (×3, collapse to 1) | 0 (it's the current commit) | `INSUFFICIENT-DATA` |
| 2 | `git commit`, `perfvibe run checkout` | `abc1234` | 0 (still the current commit) | `INSUFFICIENT-DATA` |
| 3 | make a change, `git commit`, `perfvibe run checkout` | `def5678` | 1 (`abc1234`) | `INSUFFICIENT-DATA` (needs 3) |
| 4 | repeat: commit + run twice more | `ghi9012`, then `jkl3456` | 3 (`abc1234`, `def5678`, `ghi9012`) | **real verdict** — `STABLE`/`REGRESSION` |

Nothing about the tool changes between steps 1 and 4 — the *only* thing that
grows the baseline is a new, distinct, non-current commit showing up in
history. Re-running step 1's dirty tree a hundred more times would still leave
you at zero.

## Quick diagnosis checklist

`compare`/`budget-check` returning `INSUFFICIENT-DATA` for everything is almost
always this, not a tool bug. To confirm:

```bash
perfvibe --json history <flow> | jq '.runs[] | {commit, started_at}'
```

- **All `commit` values identical** (whether or not they end in `-dirty`) →
  every run you've taken so far is the same baseline point; nothing to compare
  against yet.
- **Values end in `-dirty`** → the target repo has uncommitted changes;
  commit them so future runs stop collapsing into that one identity.
- **Fewer than `min_baseline_commits` distinct values, excluding the newest
  one** → expected. Commit and run again to grow the count; there's no way to
  skip this by re-running.

The `note: N run(s) excluded from baseline…` line is **pretty-output only** —
it never appears in `--json` (see [`docs/commands.md`](./commands.md#compare--verdict-vs-history)).
An agent or script diagnosing this must read `history --json`'s `commit`
field directly, exactly as above, rather than parsing that note.

---

## For implementers

The mechanics above map onto four places in the source, if you're changing
this behavior rather than just hitting it:

- `-dirty` tagging — `perf.adapters.context_bash_perfmeta` (`_is_dirty_worktree`,
  appended in `context()`).
- Baseline windowing + same-commit/no-commit exclusion — the SQL in
  `perf.adapters.store_sqlite` (`baseline_measure_points`,
  `baseline_system_sample_points`, `count_baseline_exclusions`).
- Median-by-commit collapsing + the `min_baseline_commits` gate — `perf.adapters.analyzer_sql`
  (`SqlAnalyzer.compare_latest`) and `perf.domain.regression.classify`.
- Defaults (`min_baseline_commits=3`, `baseline_n=10`) — `perf.config.loader`.
