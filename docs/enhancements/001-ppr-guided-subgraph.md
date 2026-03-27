# Enhancement Request: `kg_PPR_GUIDED_SUBGRAPH`

**Date**: 2026-03-26
**Status**: Requested
**Reference impl**: arno `src/services/graph_analytics.rs::ppr_guided_subgraph()` (2026-03-26)

---

## Problem

`kg_SUBGRAPH(seed_ids, k_hops=K)` produces D^K candidate nodes where D is average degree.
At D=10:

| k | nodes |
|---|-------|
| 2 | 100 |
| 3 | 1,000 |
| 4 | 10,000 |
| 5 | 100,000 — OOM / timeout |

k≥3 is impractical for production workloads without a pruning strategy.

---

## Solution

PPRGo (Bojchevski et al., KDD 2020) solves this by using Personalized PageRank scores
to select the ~top_k most relevant nodes *before* BFS, preventing exponential blowup.
PPR scores decay naturally with graph distance, so distant low-relevance nodes are
eliminated automatically.

---

## Proposed API

```python
ops.kg_PPR_GUIDED_SUBGRAPH(
    seed_ids: List[str],
    alpha: float = 0.15,               # teleport probability (NOT damping; damping = 1 - alpha)
    eps: float = 1e-5,                 # sparsification: keep score >= eps * max_score
    top_k: int = 50,                   # hard node cap after PPR pruning
    max_hops: int = 5,                 # BFS depth within pruned set
    edge_types: Optional[List[str]] = None,
) -> PprGuidedSubgraphData
```

```python
@dataclass
class PprGuidedSubgraphData:
    nodes: List[str]
    edges: List[dict]                     # {"src": str, "dst": str, "type": str}
    ppr_scores: List[Tuple[str, float]]   # sorted descending by score
    seed_ids: List[str]
    nodes_before_pruning: int
    nodes_after_pruning: int
```

---

## Algorithm

```
1. ppr = kg_PAGERANK(seed_entities=seed_ids, damping=1-alpha, max_iterations=50)
2. threshold = eps * ppr[0].score
3. pruned = [(n, s) for n, s in ppr if s >= threshold][:top_k]
4. subgraph = kg_SUBGRAPH(seed_ids=[n for n,_ in pruned], k_hops=max_hops,
                          edge_types=edge_types, max_nodes=top_k*2)
5. return PprGuidedSubgraphData(nodes, edges, ppr_scores=pruned, ...)
```

---

## Phase 1 MVP — Pure Python, no new ObjectScript

Zero new ObjectScript required. Builds entirely on existing `kg_PAGERANK` + `kg_SUBGRAPH`:

```python
def kg_PPR_GUIDED_SUBGRAPH(
    self,
    seed_ids: List[str],
    alpha: float = 0.15,
    eps: float = 1e-5,
    top_k: int = 50,
    max_hops: int = 5,
    edge_types: Optional[List[str]] = None,
) -> PprGuidedSubgraphData:
    ppr = self.kg_PAGERANK(
        seed_entities=seed_ids,
        damping=1.0 - alpha,
        max_iterations=50,
    )
    if not ppr:
        return PprGuidedSubgraphData(seed_ids=seed_ids)

    max_score = ppr[0][1]
    pruned = [(n, s) for n, s in ppr if s >= eps * max_score][:top_k]
    pruned_ids = [n for n, _ in pruned]

    subgraph = self.kg_SUBGRAPH(
        seed_ids=pruned_ids,
        k_hops=max_hops,
        edge_types=edge_types,
        max_nodes=top_k * 2,
    )

    return PprGuidedSubgraphData(
        nodes=subgraph.nodes,
        edges=subgraph.edges,
        ppr_scores=pruned,
        seed_ids=seed_ids,
        nodes_before_pruning=len(ppr),
        nodes_after_pruning=len(pruned),
    )
```

---

## Phase 2 — ObjectScript fast path

Add `Graph.KG.Subgraph.PPRGuidedJson` classmethod that runs PPR + BFS entirely
server-side on `^KG` globals. Eliminates Python bridge overhead and stays within
IRIS process memory. Target: <100ms for 10K-node graphs at k=5.

---

## Performance

| k | `kg_SUBGRAPH` | `kg_PPR_GUIDED_SUBGRAPH` (top_k=50) |
|---|---|---|
| 2 | ~40ms, D² nodes | ~60ms, ≤50 nodes |
| 3 | ~200ms, D³ nodes | ~70ms, ≤50 nodes |
| 5 | OOM / timeout | ~80ms, ≤50 nodes |

At k=5, D=10: plain BFS = 100K nodes; PPR-guided = 50 nodes. **~2000x reduction**.

---

## Parameters (from PPRGo paper defaults)

| Param | Default | Meaning |
|-------|---------|---------|
| `alpha` | 0.15 | Teleport probability. `damping = 1 - alpha`. Standard across APPNP, PPRGo, HippoRAG. |
| `eps` | 1e-5 | Relative sparsification threshold. Nodes with score < eps×max_score dropped. |
| `top_k` | 50 | Hard cap. PPRGo trained 12.4M nodes at top_k=50 in <2 min. |
| `max_hops` | 5 | Hard BFS depth limit within pruned set. |

**Critical**: `alpha` is teleport probability, NOT damping. Existing `kg_PAGERANK` takes
`damping`; caller must pass `damping=1-alpha`. Document this prominently.

---

## Files to Change

| File | Change |
|------|--------|
| `iris_vector_graph/operators.py` | Add `kg_PPR_GUIDED_SUBGRAPH` to `IRISGraphOperators` |
| `iris_vector_graph/models.py` | Add `PprGuidedSubgraphData` dataclass |
| `tests/unit/test_ppr_guided_subgraph.py` | Unit tests (empty seeds, top_k, nodes_after <= nodes_before) |
| `tests/integration/test_ppr_guided_e2e.py` | E2E test against live IRIS with 1K-node graph |

---

## Acceptance Criteria

- [ ] `kg_PPR_GUIDED_SUBGRAPH` added to `IRISGraphOperators`
- [ ] `PprGuidedSubgraphData` dataclass in `iris_vector_graph/models.py`
- [ ] `alpha` param with note: `damping = 1 - alpha`
- [ ] `eps` relative sparsification (not absolute)
- [ ] `top_k` hard cap enforced
- [ ] Unit: empty seeds returns empty result
- [ ] Unit: `nodes_after_pruning <= nodes_before_pruning` always
- [ ] Unit: `len(result.nodes) <= top_k` always
- [ ] E2E: 1K-node graph, k=5, returns ≤50 nodes in <200ms
- [ ] Phase 2: `Graph.KG.Subgraph.PPRGuidedJson` ObjectScript classmethod

---

## References

- PPRGo: Bojchevski et al., "Scaling Graph Neural Networks with Approximate PageRank," KDD 2020
- APPNP: Gasteiger et al., "Predict then Propagate," ICLR 2019
- arno reference impl: `src/services/graph_analytics.rs::ppr_guided_subgraph()`
