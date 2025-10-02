# openCypher to SQL Translation Specification

**Date**: 2025-10-02
**Status**: Phase 2 Design Document
**Context**: openCypher query endpoint for IRIS Vector Graph
**References**: Phase 2 roadmap (graph_analytics_roadmap.md:151-189)

---

## Executive Summary

This document specifies how openCypher queries translate to IRIS SQL, leveraging the existing NodePK schema with foreign key constraints. The translation preserves IRIS's strengths (HNSW vector search, FK integrity, ACID guarantees) while providing the intuitive graph query syntax users expect.

**Key Insight**: Our RDF-style tables (`nodes`, `rdf_edges`, `rdf_labels`, `rdf_props`) map naturally to property graph concepts, making Cypher translation straightforward.

---

## Data Model Mapping: SQL Tables → Property Graph

### Schema Translation

| SQL Table | Property Graph Element | Cypher Representation |
|-----------|----------------------|----------------------|
| `nodes.node_id` | Node identifier | `(n {id: "PROTEIN:123"})` |
| `rdf_labels.label` | Node label/type | `(:Protein)`, `(:Gene)` |
| `rdf_props.key, val` | Node properties | `{name: "p53", function: "tumor suppressor"}` |
| `rdf_edges.p` | Relationship type | `-[:INTERACTS_WITH]->` |
| `rdf_edges.s, o_id` | Edge endpoints | `(source)-[rel]->(target)` |
| `rdf_edges.qualifiers` | Edge properties | `{confidence: 0.95, source: "STRING-DB"}` |
| `kg_NodeEmbeddings.emb` | Vector property | `{embedding: VECTOR(...)}` |

### Example Data Transformation

**SQL Representation**:
```sql
-- nodes table
node_id = "PROTEIN:TP53"

-- rdf_labels table
s = "PROTEIN:TP53", label = "Protein"

-- rdf_props table
s = "PROTEIN:TP53", key = "name", val = "p53"
s = "PROTEIN:TP53", key = "function", val = "tumor suppressor"

-- rdf_edges table
s = "PROTEIN:TP53", p = "INTERACTS_WITH", o_id = "PROTEIN:MDM2"
```

**Cypher Representation**:
```cypher
(:Protein {id: "PROTEIN:TP53", name: "p53", function: "tumor suppressor"})
  -[:INTERACTS_WITH]->
(:Protein {id: "PROTEIN:MDM2"})
```

---

## Core Translation Patterns

### Pattern 1: Simple Node Match

**Cypher Query**:
```cypher
MATCH (n:Protein)
RETURN n.id, n.name
LIMIT 10
```

**SQL Translation**:
```sql
SELECT DISTINCT n.node_id AS id, p.val AS name
FROM nodes n
INNER JOIN rdf_labels l ON n.node_id = l.s
LEFT JOIN rdf_props p ON n.node_id = p.s AND p.key = 'name'
WHERE l.label = 'Protein'
LIMIT 10
```

**Translation Rules**:
- `MATCH (n:Label)` → `JOIN rdf_labels WHERE label = 'Label'`
- `n.property` → `JOIN rdf_props WHERE key = 'property'`
- `RETURN n.id` → `SELECT node_id`

---

### Pattern 2: Relationship Traversal (1-hop)

**Cypher Query**:
```cypher
MATCH (a:Protein)-[r:INTERACTS_WITH]->(b:Protein)
WHERE a.id = 'PROTEIN:TP53'
RETURN a.id, type(r), b.id, b.name
```

**SQL Translation**:
```sql
SELECT
    n1.node_id AS a_id,
    e.p AS relationship_type,
    n2.node_id AS b_id,
    p2.val AS b_name
FROM nodes n1
INNER JOIN rdf_labels l1 ON n1.node_id = l1.s AND l1.label = 'Protein'
INNER JOIN rdf_edges e ON n1.node_id = e.s
INNER JOIN nodes n2 ON e.o_id = n2.node_id
INNER JOIN rdf_labels l2 ON n2.node_id = l2.s AND l2.label = 'Protein'
LEFT JOIN rdf_props p2 ON n2.node_id = p2.s AND p2.key = 'name'
WHERE n1.node_id = 'PROTEIN:TP53'
  AND e.p = 'INTERACTS_WITH'
```

**Translation Rules**:
- `(a)-[r:TYPE]->(b)` → `JOIN rdf_edges WHERE p = 'TYPE'`
- `type(r)` → `e.p`
- Each node reference → `JOIN nodes` (FK validation)
- Directed edge → `e.s = source AND e.o_id = target`

---

### Pattern 3: Multi-Hop Traversal (Variable Length Paths)

**Cypher Query**:
```cypher
MATCH path = (start:Protein {id: 'PROTEIN:TP53'})-[:INTERACTS_WITH*1..3]->(end)
RETURN start.id, length(path), end.id
```

**SQL Translation** (using recursive CTE):
```sql
WITH RECURSIVE PathTraversal AS (
    -- Base case: direct neighbors (1 hop)
    SELECT
        e.s AS start_id,
        e.o_id AS end_id,
        1 AS path_length,
        e.s || '->' || e.o_id AS path
    FROM rdf_edges e
    WHERE e.s = 'PROTEIN:TP53'
      AND e.p = 'INTERACTS_WITH'

    UNION ALL

    -- Recursive case: extend paths (up to 3 hops)
    SELECT
        pt.start_id,
        e.o_id AS end_id,
        pt.path_length + 1,
        pt.path || '->' || e.o_id
    FROM PathTraversal pt
    INNER JOIN rdf_edges e ON pt.end_id = e.s
    INNER JOIN nodes n ON e.o_id = n.node_id  -- FK validation
    WHERE pt.path_length < 3
      AND e.p = 'INTERACTS_WITH'
      AND pt.path NOT LIKE '%' || e.o_id || '%'  -- Cycle detection
)
SELECT DISTINCT start_id, path_length, end_id
FROM PathTraversal
ORDER BY path_length, end_id
```

**Translation Rules**:
- `*1..3` → Recursive CTE with depth limit
- Cycle detection → Path string contains check
- FK validation → `JOIN nodes` at each hop

---

### Pattern 4: Aggregation and Grouping

**Cypher Query**:
```cypher
MATCH (p:Protein)-[:PARTICIPATES_IN]->(pathway:Pathway)
RETURN pathway.name, count(p) AS protein_count
ORDER BY protein_count DESC
LIMIT 10
```

**SQL Translation**:
```sql
SELECT
    p_pathway.val AS pathway_name,
    COUNT(DISTINCT n_protein.node_id) AS protein_count
FROM nodes n_protein
INNER JOIN rdf_labels l_protein ON n_protein.node_id = l_protein.s
    AND l_protein.label = 'Protein'
INNER JOIN rdf_edges e ON n_protein.node_id = e.s
    AND e.p = 'PARTICIPATES_IN'
INNER JOIN nodes n_pathway ON e.o_id = n_pathway.node_id
INNER JOIN rdf_labels l_pathway ON n_pathway.node_id = l_pathway.s
    AND l_pathway.label = 'Pathway'
LEFT JOIN rdf_props p_pathway ON n_pathway.node_id = p_pathway.s
    AND p_pathway.key = 'name'
GROUP BY p_pathway.val
ORDER BY protein_count DESC
LIMIT 10
```

**Translation Rules**:
- `count(p)` → `COUNT(DISTINCT node_id)`
- `ORDER BY` → Direct SQL `ORDER BY`
- Aggregation requires `GROUP BY` on all non-aggregated columns

---

## Advanced Patterns

### Pattern 5: OPTIONAL MATCH (LEFT JOIN)

**Cypher Query**:
```cypher
MATCH (p:Protein)
OPTIONAL MATCH (p)-[:HAS_VARIANT]->(v:Variant)
RETURN p.id, p.name, collect(v.id) AS variants
```

**SQL Translation**:
```sql
SELECT
    n.node_id AS id,
    p_name.val AS name,
    STRING_AGG(n_variant.node_id, ',') AS variants
FROM nodes n
INNER JOIN rdf_labels l ON n.node_id = l.s AND l.label = 'Protein'
LEFT JOIN rdf_props p_name ON n.node_id = p_name.s AND p_name.key = 'name'
LEFT JOIN rdf_edges e ON n.node_id = e.s AND e.p = 'HAS_VARIANT'
LEFT JOIN nodes n_variant ON e.o_id = n_variant.node_id
LEFT JOIN rdf_labels l_variant ON n_variant.node_id = l_variant.s
    AND l_variant.label = 'Variant'
GROUP BY n.node_id, p_name.val
```

**Translation Rules**:
- `OPTIONAL MATCH` → `LEFT JOIN`
- `collect(v.id)` → `STRING_AGG()` or JSON aggregation
- NULL handling preserved

---

### Pattern 6: WHERE Filters and Property Comparisons

**Cypher Query**:
```cypher
MATCH (p:Protein)-[r:INTERACTS_WITH]->(target)
WHERE p.confidence > 0.8
  AND target.type = 'receptor'
RETURN p.id, r.score, target.id
```

**SQL Translation**:
```sql
SELECT
    n_p.node_id AS p_id,
    e.qualifiers AS r_score,  -- Assuming score in qualifiers JSON
    n_target.node_id AS target_id
FROM nodes n_p
INNER JOIN rdf_labels l_p ON n_p.node_id = l_p.s AND l_p.label = 'Protein'
INNER JOIN rdf_props prop_confidence ON n_p.node_id = prop_confidence.s
    AND prop_confidence.key = 'confidence'
INNER JOIN rdf_edges e ON n_p.node_id = e.s AND e.p = 'INTERACTS_WITH'
INNER JOIN nodes n_target ON e.o_id = n_target.node_id
INNER JOIN rdf_props prop_type ON n_target.node_id = prop_type.s
    AND prop_type.key = 'type'
WHERE CAST(prop_confidence.val AS NUMERIC) > 0.8
  AND prop_type.val = 'receptor'
```

**Translation Rules**:
- Property filters → `JOIN rdf_props` with `WHERE` clause
- Type conversions → `CAST(val AS type)`
- Edge properties → Access `qualifiers` JSON field

---

### Pattern 7: UNION (Combining Results)

**Cypher Query**:
```cypher
MATCH (p:Protein {id: 'PROTEIN:TP53'})
RETURN p.id, 'direct' AS type
UNION
MATCH (p:Protein {id: 'PROTEIN:TP53'})-[:INTERACTS_WITH]->(neighbor)
RETURN neighbor.id, 'neighbor' AS type
```

**SQL Translation**:
```sql
SELECT n.node_id AS id, 'direct' AS type
FROM nodes n
INNER JOIN rdf_labels l ON n.node_id = l.s AND l.label = 'Protein'
WHERE n.node_id = 'PROTEIN:TP53'

UNION

SELECT n2.node_id AS id, 'neighbor' AS type
FROM nodes n1
INNER JOIN rdf_labels l1 ON n1.node_id = l1.s AND l1.label = 'Protein'
INNER JOIN rdf_edges e ON n1.node_id = e.s AND e.p = 'INTERACTS_WITH'
INNER JOIN nodes n2 ON e.o_id = n2.node_id
WHERE n1.node_id = 'PROTEIN:TP53'
```

**Translation Rules**:
- Cypher `UNION` → SQL `UNION` (removes duplicates)
- Cypher `UNION ALL` → SQL `UNION ALL` (keeps duplicates)

---

## Hybrid Queries: Cypher + Vector Search

### Pattern 8: Vector k-NN with Graph Expansion (Custom Function)

**Cypher Query** (with custom vector function):
```cypher
CALL db.index.vector.queryNodes('protein_embeddings', 20, $queryVector)
YIELD node, score

MATCH (node)-[r:INTERACTS_WITH]->(neighbor)
RETURN
    node.id,
    score,
    type(r),
    neighbor.id,
    labels(neighbor) AS neighbor_labels
ORDER BY score DESC
```

**SQL Translation**:
```sql
WITH VectorKNN AS (
    -- Vector k-NN search using HNSW index
    SELECT TOP 20
        e.id AS node_id,
        VECTOR_DOT_PRODUCT(e.emb, TO_VECTOR(?, 'DOUBLE', 768)) AS score
    FROM kg_NodeEmbeddings e
    INNER JOIN nodes n ON e.id = n.node_id
    INNER JOIN rdf_labels l ON n.node_id = l.s AND l.label = 'Protein'
    ORDER BY score DESC
)
SELECT
    knn.node_id,
    knn.score,
    e.p AS relationship_type,
    n_neighbor.node_id AS neighbor_id,
    l_neighbor.label AS neighbor_label
FROM VectorKNN knn
LEFT JOIN rdf_edges e ON knn.node_id = e.s AND e.p = 'INTERACTS_WITH'
LEFT JOIN nodes n_neighbor ON e.o_id = n_neighbor.node_id
LEFT JOIN rdf_labels l_neighbor ON n_neighbor.node_id = l_neighbor.s
ORDER BY knn.score DESC
```

**Translation Rules**:
- Custom vector procedure → CTE with `VECTOR_DOT_PRODUCT`
- `YIELD node, score` → CTE columns
- Subsequent `MATCH` → `JOIN` on CTE results

---

### Pattern 9: Filtered Vector Search by Label

**Cypher Query**:
```cypher
CALL db.index.vector.queryNodes('embeddings', 10, $vector)
YIELD node, score
WHERE 'Protein' IN labels(node)
  AND score > 0.8
RETURN node.id, node.name, score
```

**SQL Translation**:
```sql
SELECT
    e.id AS node_id,
    p.val AS name,
    VECTOR_DOT_PRODUCT(e.emb, TO_VECTOR(?, 'DOUBLE', 768)) AS score
FROM kg_NodeEmbeddings e
INNER JOIN nodes n ON e.id = n.node_id
INNER JOIN rdf_labels l ON n.node_id = l.s AND l.label = 'Protein'
LEFT JOIN rdf_props p ON n.node_id = p.s AND p.key = 'name'
WHERE VECTOR_DOT_PRODUCT(e.emb, TO_VECTOR(?, 'DOUBLE', 768)) > 0.8
ORDER BY score DESC
LIMIT 10
```

**Translation Rules**:
- Label filter → `JOIN rdf_labels WHERE label = 'Protein'`
- Score filter → `WHERE VECTOR_DOT_PRODUCT(...) > threshold`
- HNSW index automatically used by IRIS query optimizer

---

## Implementation Strategy

### Phase 2a: Parser and AST (Months 1-3)

**Use existing Cypher parser**:
- [libcypher-parser](https://github.com/cleishm/libcypher-parser) (C library, Python bindings)
- [opencypher](https://pypi.org/project/opencypher/) (Pure Python)

**AST Representation**:
```python
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class CypherNode:
    variable: str
    labels: List[str]
    properties: dict

@dataclass
class CypherRelationship:
    variable: str
    type: str
    direction: str  # '->', '<-', '-'
    properties: dict

@dataclass
class CypherMatch:
    nodes: List[CypherNode]
    relationships: List[CypherRelationship]
    where: Optional[str]

@dataclass
class CypherQuery:
    matches: List[CypherMatch]
    returns: List[str]
    order_by: Optional[List[str]]
    limit: Optional[int]
```

---

### Phase 2b: SQL Generator (Months 4-6)

**Translation Engine**:
```python
class CypherToSQLTranslator:
    """Translate Cypher AST to IRIS SQL queries."""

    def translate(self, ast: CypherQuery) -> str:
        """Main entry point: AST → SQL string."""
        sql_parts = []

        # Build FROM clause (nodes + labels)
        sql_parts.append(self._build_from_clause(ast.matches))

        # Build JOIN clauses (relationships)
        sql_parts.append(self._build_join_clause(ast.matches))

        # Build WHERE clause (filters + properties)
        sql_parts.append(self._build_where_clause(ast.matches))

        # Build SELECT clause (returns)
        sql_parts.append(self._build_select_clause(ast.returns))

        # Build ORDER BY, LIMIT
        if ast.order_by:
            sql_parts.append(self._build_order_by(ast.order_by))
        if ast.limit:
            sql_parts.append(f"LIMIT {ast.limit}")

        return '\n'.join(sql_parts)

    def _build_from_clause(self, matches: List[CypherMatch]) -> str:
        """Generate FROM clause with nodes table."""
        # Start with first node
        first_node = matches[0].nodes[0]
        return f"FROM nodes {first_node.variable}"

    def _build_join_clause(self, matches: List[CypherMatch]) -> str:
        """Generate JOINs for labels, properties, edges."""
        joins = []

        for match in matches:
            # Add label JOINs
            for node in match.nodes:
                for label in node.labels:
                    joins.append(
                        f"INNER JOIN rdf_labels l_{node.variable} "
                        f"ON {node.variable}.node_id = l_{node.variable}.s "
                        f"AND l_{node.variable}.label = '{label}'"
                    )

            # Add property JOINs
            for node in match.nodes:
                for key in node.properties:
                    joins.append(
                        f"LEFT JOIN rdf_props p_{node.variable}_{key} "
                        f"ON {node.variable}.node_id = p_{node.variable}_{key}.s "
                        f"AND p_{node.variable}_{key}.key = '{key}'"
                    )

            # Add relationship JOINs
            for rel in match.relationships:
                source, target = self._get_relationship_endpoints(rel, match)
                joins.append(
                    f"INNER JOIN rdf_edges {rel.variable} "
                    f"ON {source}.node_id = {rel.variable}.s "
                    f"AND {rel.variable}.p = '{rel.type}'"
                )
                joins.append(
                    f"INNER JOIN nodes {target} "
                    f"ON {rel.variable}.o_id = {target}.node_id"
                )

        return '\n'.join(joins)
```

---

### Phase 2c: Query Optimizer (Months 7-9)

**Optimization Rules**:

1. **Label Pushdown**: Apply label filters early
   ```sql
   -- Before: Filter after all JOINs
   FROM nodes n JOIN rdf_edges e ... WHERE l.label = 'Protein'

   -- After: Filter in first JOIN
   FROM nodes n
   INNER JOIN rdf_labels l ON n.node_id = l.s AND l.label = 'Protein'
   ```

2. **Property Filter Pushdown**: Move WHERE conditions to JOIN predicates
   ```sql
   -- Before: Filter after JOIN
   LEFT JOIN rdf_props p ON n.node_id = p.s
   WHERE p.key = 'name'

   -- After: Filter in JOIN
   LEFT JOIN rdf_props p ON n.node_id = p.s AND p.key = 'name'
   ```

3. **Index Hint Injection**: Force HNSW usage for vector queries
   ```sql
   SELECT /*+ INDEX(kg_NodeEmbeddings HNSW_NodeEmb) */ ...
   ```

4. **JOIN Reordering**: Start with most selective table
   - Small cardinality → Start here
   - Large cardinality → Join later

---

### Phase 2d: REST Endpoint (Months 10-12)

**API Design**:
```objectscript
/// openCypher Query Endpoint
Class GraphAPI.CypherQuery Extends %CSP.REST
{

/// Execute openCypher query
ClassMethod ExecuteCypher() As %Status
{
    // Parse request body
    Set tRequest = %request.Content
    Set tCypherQuery = tRequest.query
    Set tParams = tRequest.parameters  // Named parameters

    // Call Python translator
    Set tSQL = ##class(CypherTranslator).Translate(tCypherQuery)

    // Execute SQL with parameters
    Set tStatement = ##class(%SQL.Statement).%New()
    Set tStatus = tStatement.%Prepare(tSQL)
    Set tResult = tStatement.%Execute(tParams...)

    // Format results as JSON
    Set tResponse = ##class(%DynamicArray).%New()
    While tResult.%Next() {
        Set tRow = ##class(%DynamicObject).%New()
        // ... populate row ...
        Do tResponse.%Push(tRow)
    }

    Write tResponse.%ToJSON()
    Quit $$$OK
}

}
```

**REST Endpoint**:
```bash
POST /api/cypher
Content-Type: application/json

{
  "query": "MATCH (p:Protein)-[:INTERACTS_WITH]->(target) WHERE p.id = $nodeId RETURN p, target",
  "parameters": {
    "nodeId": "PROTEIN:TP53"
  }
}
```

---

## Performance Considerations

### Query Plan Analysis

**Cypher Query**:
```cypher
MATCH (p:Protein {id: 'PROTEIN:TP53'})-[:INTERACTS_WITH*1..2]->(target)
RETURN target.id
```

**Execution Plan**:
1. **Index Seek**: Use PRIMARY KEY index on `nodes.node_id` (0.29ms)
2. **Label Filter**: Use `idx_labels_s_label` index (0.1ms)
3. **Edge Traversal**: Use `idx_edges_s_p` index for first hop (0.09ms)
4. **Recursive Traversal**: CTE for second hop (0.5-1ms)
5. **FK Validation**: Automatic via existing FK constraints (no overhead!)

**Total**: <2ms for 2-hop query

### Optimization Techniques

1. **Materialized Adjacency Lists**: Precompute for hot paths
2. **Query Result Caching**: Cache frequent pattern results
3. **Partition-Aware Routing**: Route queries to relevant partitions
4. **Batch Parameter Execution**: Execute multiple similar queries in batch

---

## Testing Strategy

### Unit Tests: Translation Correctness

```python
def test_simple_match_translation():
    cypher = "MATCH (n:Protein) RETURN n.id LIMIT 10"
    translator = CypherToSQLTranslator()
    sql = translator.translate(cypher)

    assert "FROM nodes n" in sql
    assert "JOIN rdf_labels" in sql
    assert "WHERE label = 'Protein'" in sql
    assert "LIMIT 10" in sql
```

### Integration Tests: Query Results

```python
def test_cypher_query_matches_sql():
    """Ensure Cypher and SQL return identical results."""
    cypher = "MATCH (p:Protein {id: 'PROTEIN:TP53'}) RETURN p.id, p.name"

    # Execute via Cypher endpoint
    cypher_results = execute_cypher(cypher)

    # Execute equivalent SQL directly
    sql = "SELECT n.node_id, p.val FROM nodes n ..."
    sql_results = execute_sql(sql)

    assert cypher_results == sql_results
```

### Performance Tests: Benchmark Against SQL

```python
def test_cypher_performance_acceptable():
    """Cypher translation overhead should be <10% vs direct SQL."""
    cypher = "MATCH (p:Protein)-[:INTERACTS_WITH]->(t) RETURN count(t)"

    cypher_time = benchmark_cypher(cypher)
    sql_time = benchmark_sql(equivalent_sql)

    overhead = (cypher_time - sql_time) / sql_time
    assert overhead < 0.10, f"Cypher overhead {overhead*100:.1f}% exceeds 10%"
```

---

## Summary

### What We Get

✅ **Intuitive graph queries**: Natural pattern matching syntax
✅ **Leverages existing schema**: No data migration needed
✅ **Preserves IRIS strengths**: HNSW vectors, FK integrity, ACID
✅ **Hybrid queries**: Combine Cypher + vector search
✅ **Performance**: <10% overhead vs direct SQL

### Implementation Complexity

- **Parser**: Low (use existing libraries)
- **AST → SQL**: Medium (straightforward mapping)
- **Optimizer**: High (requires deep IRIS knowledge)
- **REST API**: Low (standard ObjectScript pattern)

### Timeline: 12 months (Phase 2)

- Months 1-3: Parser + AST
- Months 4-6: SQL generator
- Months 7-9: Query optimizer
- Months 10-12: REST API + testing

**Next Steps**: Create Phase 2 roadmap and parser prototype design!
