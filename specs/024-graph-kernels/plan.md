# Implementation Plan: Graph Analytics Kernels

**Branch**: `024-graph-kernels` | **Date**: 2026-03-19 | **Spec**: [spec.md](spec.md)

---

## Summary

Add three whole-graph analytics kernels — Global PageRank, WCC, CDLP — as pure ObjectScript methods over `^KG` globals with Python wrappers on `IRISGraphOperators`. All three share the same iterative `$ORDER` loop pattern proven in PPR (v1.15.0, 62ms on 143K nodes).

---

## Technical Context

**Language/Version**: Python 3.11 + ObjectScript (IRIS 2025.1+)
**Primary Dependencies**: `intersystems-irispython`, `iris-devtester` (test only)
**Storage**: InterSystems IRIS — `Graph_KG` schema, `^KG` global (adjacency index)
**Testing**: `pytest`; unit tests with `unittest.mock`; e2e tests via `iris-devtester` container
**Container**: `iris-vector-graph-main` (verified from `tests/conftest.py:153`)
**Performance Goals**: PageRank <500ms, WCC <1s, CDLP <1s on 10K-node graph
**Constraints**: Backward-compatible; no new dependencies; all existing tests must pass

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Principle I (Library-First)**: All changes within `iris_vector_graph/` and `iris_src/`. ✅
**Principle II (Compatibility-First)**: Three new methods, additive only. ✅
**Principle III (Test-First)**: All tests written before implementation. ✅
**Principle IV (E2E Testing)**:
- [x] Container `iris-vector-graph-main` (from `tests/conftest.py:153`)
- [x] Explicit e2e test phase covering all P0/P1 user stories
- [x] `SKIP_IRIS_TESTS` defaults to `"false"`
- [x] No hardcoded ports
**Principle V (Simplicity)**: Three methods in one .cls file + three Python wrappers. ✅
**Principle VI (Grounding)**: Container name verified from conftest. Schema `Graph_KG` verified from engine.py. ✅

---

## Project Structure

```text
iris_src/src/Graph/KG/
├── PageRank.cls         # MODIFY: add PageRankGlobalJson
└── Algorithms.cls       # NEW: WCCJson, CDLPJson

iris_vector_graph/
└── operators.py         # MODIFY: add kg_PAGERANK(), kg_WCC(), kg_CDLP()

tests/
├── unit/
│   └── test_graph_kernels.py      # NEW
└── e2e/
    └── test_graph_kernels_e2e.py  # NEW
```

---

## Phase 0: Research

No unknowns — all algorithms are textbook and the pattern is proven.

### Decision 1: Global PageRank
**Decision**: Add `PageRankGlobalJson` to `PageRank.cls`. Same code as `RunJson` but uniform initialization (1/N for all nodes) instead of seed-biased. Same early termination.
**Rationale**: Reuses 95% of existing code. Only initialization differs.

### Decision 2: WCC Algorithm
**Decision**: Iterative label propagation. Each node starts with own ID. Each iteration: adopt minimum label among self + all neighbors (out + in). Converge when no changes.
**Rationale**: Natural fit for `$ORDER` pattern. Simpler than union-find over globals.

### Decision 3: CDLP Algorithm
**Decision**: Iterative label propagation. Each node starts with own ID. Each iteration: adopt most frequent label among neighbors. Ties broken by smallest label. Converge when no changes.
**Rationale**: Standard LDBC Graphalytics CDLP definition. Deterministic tie-breaking.

### Decision 4: File organization
**Decision**: New `Graph.KG.Algorithms` class for WCC and CDLP. PageRank stays in `PageRank.cls`.
**Rationale**: Algorithms.cls becomes home for future kernels (LCC, SSSP).

---

## Phase 1: Design

### ObjectScript: PageRankGlobalJson
Same as RunJson but:
- Enumerate ALL nodes via `$Order(^KG("deg", node))`
- Initialize `ranks(node) = 1/nodeCount`
- Teleport: uniform `(1-alpha)/nodeCount` to ALL nodes
- Same convergence check + early termination

### ObjectScript: WCCJson
```
Initialize: labels(node) = node for all nodes
Repeat up to maxIter:
  changed = 0
  For each node in $Order(labels):
    minLabel = labels(node)
    // Check outgoing neighbors
    For p,o in $Order(^KG("out",node,p,o)): minLabel = MIN(minLabel, labels(o))
    // Check incoming neighbors
    For p,s in $Order(^KG("in",node,p,s)): minLabel = MIN(minLabel, labels(s))
    If minLabel < labels(node): newLabels(node) = minLabel, changed++
    Else: newLabels(node) = labels(node)
  If changed = 0: break (converged)
  Kill labels, Merge labels = newLabels
Return: JSON object {"node":"component_label", ...}
```

### ObjectScript: CDLPJson
```
Initialize: labels(node) = node for all nodes
Repeat up to maxIter:
  changed = 0
  For each node in $Order(labels):
    Kill counts
    // Count neighbor labels (out + in)
    For p,o in $Order(^KG("out",node,p,o)): counts(labels(o))++
    For p,s in $Order(^KG("in",node,p,s)): counts(labels(s))++
    // Find most frequent (ties: smallest label)
    bestLabel = labels(node), bestCount = 0
    For lbl in $Order(counts, 1, cnt):
      If cnt > bestCount || (cnt = bestCount && lbl < bestLabel):
        bestLabel = lbl, bestCount = cnt
    If bestLabel != labels(node): changed++
    newLabels(node) = bestLabel
  If changed = 0: break
  Kill labels, Merge labels = newLabels
Return: JSON object {"node":"community_label", ...}
```

### Python API

All three follow the same pattern:
1. Try `_call_classmethod` → parse JSON
2. Fallback: Python-side iteration using `kg_NEIGHBORS(direction="both")`

---

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| WCC slow on 143K nodes | Low | O(iterations × edges); converges in O(diameter) |
| CDLP oscillates | Medium | max_iterations cap; convergence = no label changes |
| ^KG("in") missing entries | Low | BuildKG populates both; verified in Traversal.cls |

---

## Files Changed

```
iris_src/src/Graph/KG/PageRank.cls         # ADD: PageRankGlobalJson
iris_src/src/Graph/KG/Algorithms.cls       # NEW: WCCJson, CDLPJson
iris_vector_graph/operators.py             # ADD: kg_PAGERANK(), kg_WCC(), kg_CDLP()
tests/unit/test_graph_kernels.py           # NEW
tests/e2e/test_graph_kernels_e2e.py        # NEW
```
