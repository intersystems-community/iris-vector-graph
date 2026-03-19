# Research: kg_SUBGRAPH

**Phase 0 Output** | **Date**: 2026-03-19

## Decision Log

### 1. Embedding Retrieval Path

**Decision**: Server-side ObjectScript returns graph structure (nodes, edges, properties, labels) from `^KG` globals. Python fetches embeddings via SQL for returned node IDs.

**Rationale**: Keeps ObjectScript traversal hot path pure `$ORDER`/`$GET`. Embedding fetch is one SQL `IN` query — fast, testable independently. Future iterations can migrate embeddings to server-side `&sql()` (negligible overhead since SQL is globals underneath).

**Alternatives considered**: (A) Server-side fetches everything including embeddings via `&sql()` — rejected for v1 simplicity. (C) Two separate server-side methods — rejected as unnecessary complexity.

### 2. ObjectScript BFS Pattern

**Decision**: Use the proven `BFSFast` pattern from `Traversal.cls` — frontier-based `$Order` iteration with `seen` deduplication.

**Rationale**: `BFSFast` is battle-tested at ~1.84M TEPS. `SubgraphJson` uses the same `$Order(^KG("out",node,p))` iteration but additionally collects edge triples, properties, and labels for each visited node.

**Alternatives considered**: SQL-based extraction via multi-hop JOINs — rejected because SQL round-trips add 10-50ms per query and IRIS lacks recursive CTEs.

### 3. Label Collection Strategy

**Decision**: For each collected node, iterate `$Order(^KG("label",""))` checking `$Data(^KG("label",labelName,nodeId))` — or use embedded SQL `SELECT label FROM rdf_labels WHERE s = ?` per node.

**Rationale**: `^KG` stores labels as `^KG("label", labelName, nodeId)` which is indexed by label first, not by node. Reverse lookup (node → labels) requires either scanning all labels or using SQL. Since label count is small (typically <20 distinct labels), scanning is fast. Alternative: during `BuildKG`, also store `^KG("nodelabels", nodeId, label) = ""` for O(degree) lookup per node.

**Alternatives considered**: Adding a `^KG("nodelabels",...)` index — deferred to avoid modifying BuildKG in this spec. SQL fallback is adequate for v1.

### 4. JSON Return Format

**Decision**: Single JSON object with `nodes`, `edges`, `properties`, `labels` arrays/maps.

**Rationale**: Matches the `SubgraphData` dataclass fields 1:1. Built via `%DynamicObject`/`%DynamicArray` → `%ToJSON()` — proven pattern from `PageRank.RunJson`.

### 5. Safety Limit Implementation

**Decision**: `maxNodes` parameter checked during BFS frontier expansion. When `nodeCount >= maxNodes`, stop adding new nodes to the next frontier. Complete current hop for nodes already in frontier.

**Rationale**: Simple, predictable. Callers know they'll get at most `maxNodes` nodes. Completing the current hop avoids partial neighborhoods.
