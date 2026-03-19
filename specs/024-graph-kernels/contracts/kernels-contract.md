# API Contracts: Graph Analytics Kernels

## Python API

### `IRISGraphOperators.kg_PAGERANK`
```
kg_PAGERANK(damping: float = 0.85, max_iterations: int = 20) → List[Tuple[str, float]]
```
Returns all node scores sorted descending. Scores sum to ~1.0.

### `IRISGraphOperators.kg_WCC`
```
kg_WCC(max_iterations: int = 100) → Dict[str, str]
```
Returns {node_id: component_label} for every node. Component label = min node ID.

### `IRISGraphOperators.kg_CDLP`
```
kg_CDLP(max_iterations: int = 10) → Dict[str, str]
```
Returns {node_id: community_label} for every node.

## Server-Side API

### `Graph.KG.PageRank.PageRankGlobalJson`
```
ClassMethod PageRankGlobalJson(damping As %Double = 0.85, maxIter As %Integer = 20) As %String
```
Returns JSON array: `[{"id":"node","score":0.12}, ...]`

### `Graph.KG.Algorithms.WCCJson`
```
ClassMethod WCCJson(maxIter As %Integer = 100) As %String
```
Returns JSON object: `{"node1":"comp","node2":"comp",...}`

### `Graph.KG.Algorithms.CDLPJson`
```
ClassMethod CDLPJson(maxIter As %Integer = 10) As %String
```
Returns JSON object: `{"node1":"community","node2":"community",...}`

## Cypher Procedures (stretch)

```cypher
CALL ivg.pagerank(0.85, 20) YIELD node, score
CALL ivg.wcc(100) YIELD node, component
CALL ivg.cdlp(10) YIELD node, community
```
