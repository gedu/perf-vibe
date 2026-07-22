# `perf run` — Spec-Driven Development record

Public, versioned record of the SDD cycle that produced the `perf run` capability (Phase 1). These documents are the source-of-truth "why & shape" behind the code in `src/perf/`.

| Document | Role |
|---|---|
| [`proposal.md`](./proposal.md) | **PRD** — intent, scope, approach, risks, success criteria |
| [`spec.md`](./spec.md) | Requirements & scenarios (the `SHALL`s) |
| [`design.md`](./design.md) | **RFC** — architecture and the technical "how" |
| [`tasks.md`](./tasks.md) | Task breakdown & execution record |

**Status:** implemented, verified, and merged to `main` across PRs #1–#3. Suite: 197 tests, ~96% coverage.

**Flow of the cycle:** explore → propose (PRD) → spec → design (RFC) → tasks → apply → verify → archive.

> The upstream design reference (external, not part of this repo) is the Performance Lab CLI master design document. Coding conventions live in `.claude/skills/` (the `perf-cli-standards` contract plus the `python-architecture`/`python-testing`/`python-style` craft guides) and `AGENTS.md`.
