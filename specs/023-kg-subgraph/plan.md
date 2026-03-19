# Implementation Plan: kg_SUBGRAPH

**Branch**: `023-kg-subgraph` | **Date**: 2026-03-19 | **Spec**: [spec.md](spec.md)

---

## Summary

Add `kg_SUBGRAPH` — a single-call k-hop bounded subgraph extraction method that collects all nodes, edges, properties, labels, and (optionally) embeddings within k hops of seed nodes. Server-side pure ObjectScript over `^KG` for structure extraction; Python-side SQL for embedding fetch. Returns a `SubgraphData` dataclass. Addresses the "extraction vs query" gap identified by Kumo AI.

---

## Technical Context

**Language/Version**: Python 3.11 + ObjectScript (IRIS 2025.1+)
**Primary Dependencies**: `intersystems-irispython`, `iris-devtester` (test only), `numpy` (optional, for tensor output)
**Storage**: InterSystems IRIS — `Graph_KG` schema, `^KG` global (adjacency index)
**Testing**: `pytest`; unit tests with `unittest.mock`; e2e tests via `iris-devtester` container
**Container**: `iris_vector_graph` (verified from `docker-compose.yml`)
**Performance Goals**: 2-hop extraction on 10K-node graph (avg degree 10) in <100ms
**Constraints**: Backward-compatible; no new dependencies beyond numpy; all existing tests must pass
**Scale/Scope**: Typical subgraphs: 10-1000 nodes. Safety cap: max_nodes=10000 default.

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Principle I (Library-First)**: All changes within `iris_vector_graph/` and `iris_src/`. ✅
**Principle II (Compatibility-First)**: `kg_SUBGRAPH()` is additive. No existing signatures changed. ✅
**Principle III (Test-First)**: Tests written BEFORE implementation for each phase. ✅
**Principle IV (Integration & E2E Testing for IRIS)**:
- [x] Dedicated container `iris_vector_graph` (from `docker-compose.yml`)
- [x] Explicit e2e test phase covering all P0/P1 user stories
- [x] `SKIP_IRIS_TESTS` defaults to `"false"` in all new test files
- [x] No hardcoded ports; resolved via `IRISContainer.attach("iris_vector_graph").get_exposed_port(1972)`
**Principle V (Simplicity)**: Single ObjectScript method + single Python method + dataclass. No new abstractions. ✅
**Principle VI (Grounding)**: Container name `iris_vector_graph` verified from `docker-compose.yml`. Schema `Graph_KG` verified from `engine.py`. ✅

---

## Project Structure

### Documentation

```text
specs/023-kg-subgraph/
├── spec.md              # Feature specification
├── plan.md              # This file
├── research.md          # Phase 0: research findings
├── data-model.md        # Phase 1: SubgraphData model
├── quickstart.md        # Phase 1: usage examples
├── checklists/
│   └── requirements.md  # Spec quality checklist
└── contracts/
    └── subgraph-contract.md  # API contract
```

### Source Code

```text
iris_vector_graph/
├── operators.py         # MODIFY: add kg_SUBGRAPH(), kg_SUBGRAPH_TENSORS() (stretch)
└── models.py            # NEW: SubgraphData dataclass

iris_src/src/Graph/KG/
└── Subgraph.cls         # NEW: pure ObjectScript SubgraphJson

iris_vector_graph/cypher/
└── translator.py        # MODIFY: add ivg.subgraph procedure (stretch)

tests/
├── unit/
│   └── test_subgraph.py        # NEW: unit tests
└── e2e/
    └── test_subgraph_e2e.py    # NEW: e2e tests against live IRIS
```

**Structure Decision**: Follows existing library layout. `models.py` is new but minimal (one dataclass). `Subgraph.cls` follows the `PageRank.cls` / `Traversal.cls` pattern.

---

## Phase 0: Research

No NEEDS CLARIFICATION items remain — all technical decisions are resolved:

1. **Embedding retrieval path**: Decided in clarification session — server-side returns structure from `^KG`; Python fetches embeddings via SQL. Future iterations may move embeddings to server-side `&sql()`.
2. **ObjectScript BFS pattern**: Proven in `Traversal.cls` `BFSFast` — frontier-based `$Order` iteration with `seen` deduplication. `SubgraphJson` will use the same pattern but collect edges + properties + labels instead of just edge paths.
3. **JSON return format**: Proven in `PageRank.RunJson` — `%DynamicObject` / `%DynamicArray` → `%ToJSON()`. `SubgraphJson` returns `{"nodes":[...],"edges":[...],"properties":{...},"labels":{...}}`.
4. **Safety limits**: `BFSFast` already has `maxHops`. `SubgraphJson` adds `maxNodes` — check `$Increment(nodeCount)` against limit before adding to frontier.

---

## Phase 1: Design

### SubgraphData Model (`iris_vector_graph/models.py`)

```python
@dataclass
class SubgraphData:
    nodes: List[str]
    edges: List[Tuple[str, str, str]]  # (source, predicate, target)
    node_properties: Dict[str, Dict[str, str]]
    node_labels: Dict[str, List[str]]
    node_embeddings: Dict[str, List[float]]  # empty if not requested
    seed_ids: List[str]
```

### SubgraphJson ObjectScript Method

```
Graph.KG.Subgraph.SubgraphJson(seedJson, maxHops, edgeTypesJson, maxNodes) → JSON string

Input:
  seedJson: '["PMID:630","PROTEIN:TP53"]'
  maxHops: 2  (default)
  edgeTypesJson: '["MENTIONS"]' or '' (all types)
  maxNodes: 10000 (default)

Output JSON:
{
  "nodes": ["A","B","C"],
  "edges": [{"s":"A","p":"REL","o":"B"}, ...],
  "properties": {"A": {"key1":"val1"}, ...},
  "labels": {"A": ["Gene","Protein"], ...}
}
```

Algorithm: Level-synchronous BFS over `^KG("out",...)` with:
- `seen(node)=""` for deduplication
- `nodeCount` checked against `maxNodes` before frontier expansion
- Edge type filter: if `edgeTypesJson` provided, only traverse matching predicates
- Property/label collection: read `^KG("prop",node,key)` and `^KG("label",label,node)` for each collected node

### Python kg_SUBGRAPH Method

```python
def kg_SUBGRAPH(self, seed_ids, k_hops=2, edge_types=None,
                include_properties=True, include_embeddings=False,
                max_nodes=10000) -> SubgraphData:
```

Execution path:
1. Try `_call_classmethod(conn, 'Graph.KG.Subgraph', 'SubgraphJson', ...)` — primary
2. Parse JSON → populate `SubgraphData`
3. If `include_embeddings`: one SQL query `SELECT id, emb FROM Graph_KG.kg_NodeEmbeddings WHERE id IN (?,...)`
4. Fallback: Python-side BFS via `kg_NEIGHBORS` + property/label SQL queries

### API Contract

```
kg_SUBGRAPH(seed_ids, k_hops=2, edge_types=None, include_properties=True, include_embeddings=False, max_nodes=10000)
  → SubgraphData

Preconditions:
  - seed_ids: non-empty list of strings (empty → empty SubgraphData)
  - k_hops: integer ≥ 0
  - max_nodes: integer > 0

Postconditions:
  - nodes contains all reachable nodes within k_hops (capped by max_nodes)
  - edges contains all edges between subgraph nodes
  - No duplicate nodes or edges
  - Seeds not in graph silently excluded
  - node_embeddings populated only if include_embeddings=True
```

---

## Implementation Phases

### Phase 1: Tests First (RED)

Create all test files. Tests must fail initially.

**Unit tests** (`tests/unit/test_subgraph.py`):
- `test_subgraph_data_fields` — SubgraphData has expected attributes
- `test_kg_subgraph_method_exists` — method on IRISGraphOperators
- `test_empty_seeds_returns_empty` — kg_SUBGRAPH([]) returns empty SubgraphData
- `test_json_parsing` — mock SubgraphJson response, verify parsing
- `test_edge_type_filter_in_json` — verify edgeTypesJson constructed correctly
- `test_embeddings_fetched_separately` — mock to verify SQL query for embeddings

**E2E tests** (`tests/e2e/test_subgraph_e2e.py`):
- `test_chain_graph_2hop` — A->B->C->D, seed A, k=2 → {A,B,C}
- `test_chain_graph_1hop` — same, k=1 → {A,B}
- `test_multi_seed_union` — seeds [A,D], k=1 → union
- `test_edge_type_filter` — mixed types, filter to MENTIONS only
- `test_max_nodes_cap` — dense hub, max_nodes=10
- `test_nonexistent_seed` — silently excluded
- `test_k_hops_zero` — seed nodes only, no edges
- `test_cyclic_graph` — A->B->A, no duplicates
- `test_properties_included` — verify node properties in result
- `test_labels_included` — verify node labels in result
- `test_embeddings_included` — include_embeddings=True, verify vectors
- `test_embeddings_excluded_by_default` — verify empty when not requested
- `test_server_side_matches_fallback` — compare ObjectScript vs Python results

### Phase 2: ObjectScript SubgraphJson (FR-007, FR-010)

Create `iris_src/src/Graph/KG/Subgraph.cls` — pure ObjectScript, follows `PageRank.RunJson` pattern:
- Parse seedJson via `%DynamicArray.%FromJSON()`
- BFS loop: frontier → `$Order(^KG("out",node,p))` → collect edges → expand
- Edge type filter: skip predicates not in allowed set
- `maxNodes` check before adding to frontier
- Collect properties via `$Order(^KG("prop",node,key))`
- Collect labels via `$Order(^KG("label",label,node))` (reverse: iterate labels checking each node)
- Build `%DynamicObject` result → `%ToJSON()`

### Phase 3: Python kg_SUBGRAPH Method (FR-001 through FR-006)

Add to `IRISGraphOperators`:
- Primary path: `_call_classmethod` → `SubgraphJson` → parse JSON
- Embedding fetch: SQL `SELECT id, emb FROM Graph_KG.kg_NodeEmbeddings WHERE id IN (...)`
- Fallback: Python-side BFS using `kg_NEIGHBORS` + SQL for properties/labels
- Return `SubgraphData` dataclass

### Phase 4: SubgraphData Model

Create `iris_vector_graph/models.py` with `SubgraphData` dataclass.

### Phase 5: Verify E2E

- Deploy `Subgraph.cls` to test container
- Run all e2e tests
- Verify server-side path used (not fallback)
- Performance timing assertion on 10K-node graph

### Phase 6: Stretch Goals (P2)

- `kg_SUBGRAPH_TENSORS` → convert SubgraphData to numpy arrays
- `ivg.subgraph` Cypher procedure in translator.py

### Phase 7: Final Validation

- `pytest tests/unit/ tests/e2e/` — all green
- `ruff check .` — no new lint errors
- Verify existing tests pass (no regressions)

---

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| `SubgraphJson` label collection slow (iterate all labels × all nodes) | Medium | Collect labels per-node via `$Order(^KG("label","",node))` — but ^KG stores labels as `^KG("label",label,node)`. May need reverse lookup. Fallback: use `&sql()` for labels. |
| Embedding SQL `IN` clause too large for 10K nodes | Low | Chunk into 500-node batches (same pattern as `kg_NEIGHBORS`) |
| `_call_classmethod` bridge fails (same as PPR) | Low | Already proven working in v1.10.2. Fallback exists. |
| Performance: 2-hop on high-degree graph exceeds 100ms | Medium | `maxNodes` cap prevents explosion. Timing test uses realistic graph (avg degree 10, not 1000). |

---

## Files Changed

```
iris_vector_graph/models.py                        # NEW: SubgraphData dataclass
iris_vector_graph/operators.py                     # ADD: kg_SUBGRAPH(), kg_SUBGRAPH_TENSORS() (stretch)
iris_src/src/Graph/KG/Subgraph.cls                 # NEW: pure ObjectScript SubgraphJson
iris_vector_graph/cypher/translator.py             # ADD: ivg.subgraph procedure (stretch)
tests/unit/test_subgraph.py                        # NEW: unit tests
tests/e2e/test_subgraph_e2e.py                     # NEW: e2e tests
```
