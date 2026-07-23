---
name: python-architecture
description: "Trigger: designing, structuring, or refactoring Python in perf-lab-cli; adding a class/abstraction/pattern; deciding where code goes. Keep behavior local and resist needless indirection."
license: Apache-2.0
metadata:
  author: eduardo-graciano
  version: "1.0"
---

## Activation Contract

Load before making a STRUCTURAL choice in the `perf-vibe` repo: adding a module/class/abstraction, introducing a pattern, splitting or moving logic, or extending the tool with a new source. Complements — does not replace — `perf-cli-standards` (the hard-rule contract). This skill is about *shape*, not syntax.

## Hard Rules

1. **Locality of behavior (the north star).** A reader should understand a unit of behavior without hopping across files, and a bug should be fixable in ONE place. If fixing one behavior forces edits in 3+ files, that is a smell — the logic is scattered; consolidate it. Obvious-but-local beats clever-but-spread.
2. **Hexagonal spreads by RESPONSIBILITY, never by scattering one behavior.** All of "how we parse markers" lives in `markers_adb_logcat.py`; all of "the regression rule" lives in `regression.py`. The layers (domain / application / adapters) separate *concerns* — they must never smear a single decision across several files. Adding a layer hop is only justified when it isolates a distinct concern, not to satisfy a pattern.
3. **No abstraction until it earns its place (rule of three).** Do NOT add an interface, base class, factory, strategy, or wrapper for a single implementation. In this project the **port IS the abstraction** — one `Protocol` + one adapter per seam. Do not add indirection *inside* an adapter or use-case. Speculative extension points are the #1 cause of scattered, hard-to-debug code — reject them in review.
4. **Composition over inheritance.** Prefer small functions and plain data (`@dataclass(frozen=True)`) passed explicitly. No deep class hierarchies; no mixins. A class only when it holds state or satisfies a port.
5. **Pure core, effects at the edges.** Keep decision logic (stats, verdict, plan composition) as pure functions in `domain/`; keep I/O in adapters. Pure logic is testable and movable without touching wiring.
6. **Extend at the seam, not the core.** A new platform/tool = a new adapter behind an existing port + a registry entry — never scattered `if platform == ...` branches in `domain/` or `application/`.
7. **Don't build for platforms you aren't shipping.** A documented seam + one clean adapter is the deliverable. No config, flags, or classes for hypothetical futures.

## Decision Gates

| Situation | Do |
| --- | --- |
| Tempted to add an interface/base class/factory | Is there >1 real implementation TODAY? No → use a plain function/class. The port is the only seam. |
| A behavior change touches 3+ files | Stop — the logic is scattered. Pull it into one cohesive place. |
| Logic needs a side effect (subprocess, db, clock, fs) | Put it behind/inside an adapter; keep the caller pure. |
| Adding platform/tool support | New adapter + registry entry; zero edits to domain/application. |
| A function is getting long | Split by *step*, keeping related steps in the same module — do not scatter into many files. |

## Review Checklist

- [ ] Could a newcomer fix a bug in this behavior by opening ONE file?
- [ ] No interface/factory/base class introduced for a single implementation.
- [ ] No `if platform/tool == ...` branch in `domain/` or `application/`.
- [ ] Decision logic is pure and lives in `domain/`; effects live in adapters.
- [ ] No speculative extension point for something we are not shipping now.

## References

- `.claude/skills/perf-cli-standards/SKILL.md` — the hard-rule contract (hexagonal layering, ports, boundaries) this skill deepens.
- Design source (private, not in this repo; context only): the perf-lab-cli
  master design doc, §4 principles, §5 seams, §15 extensibility. This skill is
  self-contained; you do not need the source to apply it.
