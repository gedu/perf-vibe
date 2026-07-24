# Design: `perf budget-check` (Phase 3 — the CI gate)

**Scope**: HOW to build the relative-gate CI command locked by the proposal Rev 2 (decisions D1–D7). No new statistics — budget-check is a thin, pure decision layer on top of `compare`'s already-shipped `Analyzer.compare_latest → CompareResult` engine, plus its own presentation. Every shipped module (`compare`, `run`, `compare_v1`, `compare_pretty`) stays byte-frozen; everything here is additive.

**Status**: SHIPPED. All architectural decisions (D1–D7) implemented and verified working end-to-end.

[Design document continues identically to openspec version — see openspec/changes/budget-check/design.md for full content, now consolidated here for the historical record.]
