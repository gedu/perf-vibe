# `perf run` — Spec-Driven Development record

Public, versioned record of the SDD cycle that produced the `perf run` capability (Phase 1). These documents are the source-of-truth "why & shape" behind the code in `src/perf/`.

| Document | Role |
|---|---|
| [`proposal.md`](./proposal.md) | **PRD** — intent, scope, approach, risks, success criteria |
| [`spec.md`](./spec.md) | Requirements & scenarios (the `SHALL`s) |
| [`design.md`](./design.md) | **RFC** — architecture and the technical "how" |
| [`tasks.md`](./tasks.md) | Task breakdown & execution record |

**Status:** implemented, verified, and merged to `main` across PRs #1–#3.

> **Frozen record.** This directory is a snapshot of the Phase 1 cycle, kept as written. The suite size quoted here (197 tests) was accurate when `perf run` shipped and is **not** current — it has grown since, notably with `compare`. Run `pytest` for the live count; the canonical, maintained spec is [`openspec/specs/perf-run.md`](../../../openspec/specs/perf-run.md). The ~96% coverage figure still holds: `pytest --cov=perf` reports 96%, and CI now enforces a floor.

**Flow of the cycle:** explore → propose (PRD) → spec → design (RFC) → tasks → apply → verify → archive.

> The upstream design reference (external, not part of this repo) is the Performance Lab CLI master design document. Coding conventions live in `.claude/skills/` (the `perf-cli-standards` contract plus the `python-architecture`/`python-testing`/`python-style` craft guides) and `AGENTS.md`.
