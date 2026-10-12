<!-- markdownlint-disable MD013 -->

# Implementation Plan: Traversal readers stop reading the stored degree counters

**Branch**: `alt/adj-traversal-readers` | **Spec**: [spec.md](spec.md)

## Summary

Five ObjectScript classes stop reading `^KG("deg")`/`^KG("degp")`. Predicate pruning becomes a
`$Data` probe on `out`; `KHopCount` loses its shortcut; the rebuild counts `^NKG(-3)` inline during the
existing BuildNKG walk; `deg2p` is computed in one pass; ArnoAccel uses `Adj.NextSource`; the dead
`^KG` fallback in NKGAccelAdjacency is removed. Writers of the counters are untouched.

## Technical Context

**Language/Version**: ObjectScript (IRIS 2024.1+), Python 3.10+ for tests
**Testing**: pytest source scan (unit, runs in worktree); live IRIS e2e (lead runs)
**Constraints**: no IRIS container used by this package; ObjectScript is uncompiled.

## Design decisions

- **BFS (P)**: `'$Data(^KG("out", pGraph, o, nextP))`, per the audit (exact, same cost).
- **KHopCount (V)**: remove lines that return the stored value; the counting branch stays as written.
- **Merged degree `^NKG(-3,s)`**: BuildNKG already visits every `out` edge per graph. Each (g, src) adds its
  own count (sum over preds of the `"#"`-or-1 multiplicity of each dst) to `^NKG(-3,s)`. `DropNKG` kills
  `^NKG` at the start of the build, so the accumulation starts at zero.
- **`deg2p`**: Build2HopStats used the sum over mids of `degp(g, mid, pred)`. Computing each mid's count
  from `out` at the point of use would cost the sum of mid out-degrees per two-hop path. Instead, one pass
  over `out` counts each `(g, mid, pred)` and adds that count to every source that reaches `mid` on `pred`,
  routed through the `in` index (`^KG("in", g, mid, pred, src)`, written with the same edges). Counts still come
  from `out`; the `in` index only routes the sums. Persistent, no process-private memory.
- **ArnoAccel**: iterate `Graph.KG.Adj.NextSource(tG, node)`.
- **NKGAccelAdjacency**: `ExportAdjacency` keeps the `UseNKG()` guard and returns `ExportAdjacencyNKG()`;
  `ExportAdjacencyKG` is deleted.

## Risks

- **Multigraph KHopCount**: the kept counting branch counts distinct `out` triples, while the stored `deg`
  it replaces counted parallel edges. For single-edge graphs they agree. DEG-AUDIT V2 says this needs a decision.
- **Writers**: `^KG("deg")`/`degp` are still written; a reader-free store that is still written is advisory
  until the writer package removes the writes.
- **Uncompiled**: all ObjectScript is unverified until the lead compiles it.
