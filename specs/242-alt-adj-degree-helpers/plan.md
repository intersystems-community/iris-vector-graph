<!-- markdownlint-disable MD013 -->

# Implementation Plan: Adjacency degree helpers (Graph.KG.Adj)

**Spec**: [spec.md](spec.md)

## Summary

Add one ObjectScript class, `Graph.KG.Adj`, that owns the three adjacency reads (Degree, NextSource, HasEdges).
Migrate the readers of `^KG("deg")`/`^KG("degp")` in Algorithms, Centrality, PageRank and Subgraph onto it. The
TraversalBFS/TraversalKHop/TraversalBuild/ArnoAccel/NKGAccelAdjacency classes belong to another package and are not
touched.

## Technical Context

- ObjectScript on IRIS 2024.1+, uncompiled in this worktree (no container for this package).
- Python 3.11 `.venv` (main checkout) for the unit tests; `PYTHONPATH=$PWD`.
- Live check is `tests/e2e/test_adj_degree_live.py`, run by the lead.

## Constitution Check

- Test-first: the source scan and the contract test were written before `Adj.cls` and the migrations.
- No container, no push, no attribution in commits.
- Files outside the package are not edited.

## Design

- `Degree(g, s, p="")`: with `p = ""`, walks `$Order(^KG("out",g,s,p))` and sums the per-target contribution; with
  a predicate, walks only that predicate. The contribution is `+$Get(^KG("out",g,s,p,o,"#"))` when the `"#"` node is
  defined, else 1. Guard `s = ""` returns 0.
- `NextSource(g, s="")`: `Quit $Order(^KG("out", g, s))`.
- `HasEdges(g, s, p="")`: `$Data` on the source node or the predicate node, as a boolean. Guard `s = ""`.
- Migrations:
  - Algorithms (WCC, CDLP) and Centrality (node enumeration, out/both degree): `NextSource`; `Degree` at the point of
    use.
  - PageRankGlobalJson: one pass over `NextSource` fills `outDeg(node) = Degree(...)` before the iteration loop.
  - PageRank.Run and Subgraph.PPRGuidedJson (seeded walks): `outDeg(node)` memoised on first visit inside the loop.
    This is a deviation from "build once before the loop": a pre-build walks every source of the graph even for a
    walk that touches a small region. The cost is still O(E) once per call.
  - Subgraph.SubgraphJson existence check: `HasEdges(pGraph, s)`.
- Comments that name the counters are reworded to name `Graph.KG.Adj`.

## Risks (for the report)

- PageRank's walk gives each distinct `(p, o)` one share while the divisor counts parallel edges, so mass leaks on
  a multigraph. This is the existing behaviour and is kept ("values unchanged"). It is recorded, not fixed.
- Centrality and Subgraph hardwire graph `0` (existing); the live test covers named graphs only through `RunJson`.
