---
name: perf-cli-standards
description: "Trigger: writing or editing Python in perf-lab-cli / perf-vibe, sdd-apply, adapters, ports, hexagonal, SQLite store, typer CLI. Enforce the project's architecture and coding standards."
license: Apache-2.0
metadata:
  author: eduardo-graciano
  version: "1.0"
---

## Activation Contract

Load when writing or editing Python in the `perf-vibe` repo (the `perf-lab-cli` / `perf` package): domain, application, adapters, CLI, store, or tests. Applies to `sdd-apply`, `sdd-verify`, and any code-writing agent. These rules override generic Python habits.

## Hard Rules (review-blocking if violated)

1. **Hexagonal layering.** `domain/` imports NO adapter and performs NO I/O. Ports are `typing.Protocol` in `domain/ports.py`. `application/` use-cases orchestrate ports only — no I/O of their own. All tool-specific code (adb, maestro, flashlight, sqlite, git) lives in `adapters/`. A `domain/` or `application/` module importing an adapter is a blocking violation.
2. **Domain modeling.** Value objects are `@dataclass(frozen=True)` (Run, Measure, SystemSample, RunContext, Marker, Verdict). No behavior with side effects on them.
3. **Persistence.** SQLite via stdlib `sqlite3`. Ingest one run in a SINGLE transaction (`BEGIN` → upsert dimensions → insert facts → `COMMIT`); roll back the whole run on ANY exception — never leave half-written history. Set `PRAGMA foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout`. Migrations run via `PRAGMA user_version` + numbered files in `db/migrations/`. NEVER edit `schema.sql` in place for an existing DB — add a migration.
4. **Subprocess safety.** ALWAYS `subprocess.run([...])` with an argv list. NEVER `shell=True` or string composition for adb/maestro/git/flashlight. Validate `flow_name` against config-known flows before invoking.
5. **CLI.** `typer` app, entry point `perf.cli:main`. Global flags: `--json`, `--no-color` (also honor `NO_COLOR` env + TTY detection), `--db`, `--config`. The machine contract is `--json` (carries `schema_version`); the pretty view is lossy and MUST NEVER be parsed. On non-TTY stdout without `--json`, print a one-line nudge to STDERR.
6. **Exit codes.** `0` success · `1` regression (ONLY `compare`/`budget-check` — `run` must NEVER emit `1`) · `2` usage error · `3` runtime/tooling error (device offline, maestro failure, no markers).
7. **Testing (TDD, RED before GREEN).** pytest layout `unit/ contract/ golden/ integration/`. Pure domain unit-tested with no I/O. Every side effect is behind a port and faked (`FakeDriver`, `FakeStore`, `FrozenClock`). Golden files for pretty output with color forced off (`--update-golden` regenerates). A contract test MUST fail on any `--json` shape change without a `schema_version` bump. Adapters test against recorded fixtures, not live devices.
8. **Dependencies.** stdlib-first. A new dependency must earn its place; `typer` and `rich` are the only sanctioned ones. Justify any addition.
9. **Hard boundaries.** NO network-metric ingestion (that is Embrace's domain), even if present in Flashlight output. Lab-only / local-first. The `.db` is local and uncommitted (`.gitignore`). NEVER read BCP app source — consume only runtime signals (logcat, Flashlight JSON) and config.

## Review Checklist (self-apply before finishing)

- [ ] No `import` of an adapter inside `domain/` or `application/`.
- [ ] Ports are `Protocol`; value objects are frozen dataclasses.
- [ ] Ingestion is one transaction; failure path proven to leave 0 fact rows.
- [ ] WAL + `busy_timeout` + `foreign_keys` set; migration bumps `user_version`.
- [ ] Every subprocess call uses an argv list; no `shell=True`; `flow_name` validated.
- [ ] `--json` shape unchanged, or `schema_version` bumped + contract test updated.
- [ ] Exit codes correct; `run` never returns `1`.
- [ ] Each new side effect has a fake; RED test written before implementation.
- [ ] No network metrics stored; `.db` gitignored; no BCP source read.

## References

- Design source (do not copy wholesale; distilled here): `/Users/eduardo.graciano/Documents/CK/Clients/PeruBank/perf-lab-cli-master-design.md` (§5 seams, §9 schema/transaction, §10 ports, §11 adapters, §13 --json contract, §16 testing).
