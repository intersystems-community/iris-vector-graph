# Phase 2: openCypher Implementation Roadmap

**Timeline**: 12-18 months
**Status**: Design phase
**Prerequisites**: Phase 1b complete (NodePK, embedded Python, HNSW)
**Reference**: graph_analytics_roadmap.md:151-189

---

## Executive Summary

Phase 2 delivers an **openCypher query endpoint** for IRIS Vector Graph, providing intuitive graph pattern matching while leveraging IRIS's strengths (HNSW vectors, FK integrity, ACID guarantees). This positions IRIS as the only database supporting **SQL + Cypher + Vector search** with enterprise-grade data integrity.

**Key Deliverables**:
1. openCypher query parser and translator
2. SQL query generation engine
3. Query optimizer for IRIS
4. REST API endpoint (/api/cypher)
5. Hybrid Cypher+Vector extensions

**Market Positioning**:
> "The only graph database that supports SQL, Cypher, GQL, AND Gremlin with native vector search, while maintaining ACID guarantees and foreign key integrity."

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  Client Layer                                                │
│  - Web UI with Cypher query editor                         │
│  - Python client: cypher_query(query, params)              │
│  - REST API: POST /api/cypher                              │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  Query Language Layer (NEW - Phase 2)                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │ Cypher Parser│  │ GQL Parser   │  │Gremlin Parser│     │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘     │
│         └──────────────────┼──────────────────┘             │
│                            ↓                                 │
│                   ┌────────────────┐                        │
│                   │  Query Planner │                        │
│                   │  (AST → SQL)   │                        │
│                   └────────┬───────┘                        │
└────────────────────────────┼────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────┐
│  Graph Computation Engine (NEW - Phase 2)                   │
│  - SQL Query Generator                                      │
│  - Query Optimizer (IRIS-specific)                         │
│  - Execution Plan Generator                                │
│  - Result Formatter (Cypher → JSON)                        │
└─────────────────────────────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────┐
│  IRIS SQL Execution Layer (EXISTING - Phase 1)             │
│  - Vector: VECTOR_DOT_PRODUCT with HNSW index              │
│  - Graph: rdf_edges JOINs with FK validation               │
│  - Properties: rdf_props, rdf_labels                       │
│  - NodePK: Foreign key constraints                         │
└─────────────────────────────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────┐
│  IRIS Storage Layer (EXISTING)                             │
│  - Globals: ^nodes, ^rdf.edges, ^kg.NodeEmbeddings        │
│  - Indexes: PRIMARY KEY, HNSW, B-tree                     │
└─────────────────────────────────────────────────────────────┘
```

---

## Phase 2 Milestones (12-18 Months)

### Milestone 1: Parser & AST (Months 1-3)

**Objective**: Parse openCypher queries into Abstract Syntax Tree (AST)

**Deliverables**:
- [ ] Cypher parser integration (libcypher-parser or opencypher)
- [ ] AST data structures for MATCH, WHERE, RETURN, ORDER BY, LIMIT
- [ ] Unit tests for all Cypher syntax patterns
- [ ] Parser error handling with actionable messages

**Technology Stack**:
- **Parser**: [libcypher-parser](https://github.com/cleishm/libcypher-parser) (C library with Python bindings)
  - Pros: Official Neo4j parser, battle-tested, full Cypher support
  - Cons: C dependency (requires compilation)
- **Alternative**: [opencypher](https://pypi.org/project/opencypher/) (Pure Python)
  - Pros: No C dependencies, easier deployment
  - Cons: May not support all Cypher features

**Acceptance Criteria**:
```python
# Parser must handle all these patterns
test_queries = [
    "MATCH (n:Protein) RETURN n.id",
    "MATCH (a)-[r:INTERACTS_WITH]->(b) WHERE a.id = 'PROTEIN:TP53' RETURN b",
    "MATCH path = (start)-[:INTERACTS_WITH*1..3]->(end) RETURN path",
    "MATCH (p:Protein) OPTIONAL MATCH (p)-[:HAS_VARIANT]->(v) RETURN p, collect(v)",
    "CALL db.index.vector.queryNodes('embeddings', 10, $vector) YIELD node, score RETURN node"
]

for query in test_queries:
    ast = parse_cypher(query)
    assert ast is not None
    assert validate_ast(ast) == True
```

**Timeline**: 3 months
**Dependencies**: None
**Risk**: Medium (parser library maturity)

---

### Milestone 2: SQL Generator (Months 4-6)

**Objective**: Translate Cypher AST to IRIS SQL queries

**Deliverables**:
- [ ] AST → SQL translation engine
- [ ] Pattern mapping for MATCH, WHERE, RETURN
- [ ] Relationship traversal translation (1-hop, multi-hop)
- [ ] Property filter translation (rdf_props JOINs)
- [ ] Label filter translation (rdf_labels JOINs)
- [ ] OPTIONAL MATCH → LEFT JOIN translation
- [ ] UNION translation
- [ ] Integration tests comparing Cypher vs SQL results

**Core Translation Logic**:
```python
class CypherToSQLTranslator:
    def translate(self, ast: CypherQuery) -> str:
        """Translate Cypher AST to IRIS SQL."""
        return SQLBuilder() \
            .add_from_clause(ast.matches[0].nodes[0]) \
            .add_label_joins(ast.matches) \
            .add_property_joins(ast.matches) \
            .add_relationship_joins(ast.matches) \
            .add_where_clause(ast.matches) \
            .add_select_clause(ast.returns) \
            .add_order_by(ast.order_by) \
            .add_limit(ast.limit) \
            .build()
```

**Test Strategy**:
```python
def test_translation_correctness():
    """Ensure Cypher and SQL return identical results."""
    test_cases = [
        {
            "cypher": "MATCH (p:Protein {id: 'PROTEIN:TP53'}) RETURN p.id, p.name",
            "expected_sql": """
                SELECT n.node_id AS id, p.val AS name
                FROM nodes n
                INNER JOIN rdf_labels l ON n.node_id = l.s AND l.label = 'Protein'
                LEFT JOIN rdf_props p ON n.node_id = p.s AND p.key = 'name'
                WHERE n.node_id = 'PROTEIN:TP53'
            """
        },
        # ... 50+ test cases covering all patterns
    ]

    for case in test_cases:
        actual_sql = translator.translate(case["cypher"])
        cypher_results = execute_cypher(case["cypher"])
        sql_results = execute_sql(actual_sql)
        assert cypher_results == sql_results
```

**Timeline**: 3 months
**Dependencies**: Milestone 1 (Parser)
**Risk**: Low (straightforward mapping, well-documented in cypher_to_sql_translation.md)

---

### Milestone 3: Query Optimizer (Months 7-9)

**Objective**: Optimize generated SQL for IRIS query planner

**Deliverables**:
- [ ] Label filter pushdown (apply label filters in first JOIN)
- [ ] Property filter pushdown (move WHERE to JOIN ON clauses)
- [ ] Index hint injection (force HNSW for vector queries)
- [ ] JOIN reordering (start with most selective table)
- [ ] Cardinality estimation (use IRIS statistics)
- [ ] Query plan analysis tool
- [ ] Performance benchmarks (Cypher vs SQL overhead <10%)

**Optimization Examples**:

**Before Optimization**:
```sql
-- Inefficient: Filter after all JOINs
SELECT n.node_id
FROM nodes n
LEFT JOIN rdf_labels l ON n.node_id = l.s
WHERE l.label = 'Protein'  -- Scans all nodes first!
```

**After Optimization**:
```sql
-- Efficient: Filter in JOIN predicate
SELECT n.node_id
FROM nodes n
INNER JOIN rdf_labels l ON n.node_id = l.s AND l.label = 'Protein'
-- Now index idx_labels_label_s can be used immediately
```

**Optimizer Rules**:
1. **Rule: Label Pushdown** - Move label filters to JOIN ON clause
2. **Rule: Property Pushdown** - Move property filters to JOIN ON clause
3. **Rule: Index Hints** - Add `/*+ INDEX(...) */` for HNSW queries
4. **Rule: JOIN Reordering** - Start with smallest cardinality table
5. **Rule: FK Elimination** - Remove redundant FK validation JOINs

**Performance Gates**:
- Translation overhead: <10% vs direct SQL
- Simple MATCH: <1ms (same as SQL)
- 2-hop traversal: <2ms (same as SQL)
- Vector k-NN: <10ms (HNSW-optimized)

**Timeline**: 3 months
**Dependencies**: Milestone 2 (SQL Generator)
**Risk**: High (requires deep IRIS query planner knowledge)

---

### Milestone 4: REST API & Client (Months 10-12)

**Objective**: Expose Cypher endpoint via REST API

**Deliverables**:
- [ ] REST endpoint: POST /api/cypher
- [ ] Parameter binding support (named parameters: $var)
- [ ] Result streaming for large result sets
- [ ] Error handling with Cypher line/column numbers
- [ ] Python client library
- [ ] Web UI with Cypher query editor
- [ ] API documentation (OpenAPI/Swagger)

**REST API Design**:

**Endpoint**: `POST /api/cypher`

**Request**:
```json
{
  "query": "MATCH (p:Protein)-[:INTERACTS_WITH]->(target) WHERE p.id = $nodeId RETURN p.id, target.id, target.name",
  "parameters": {
    "nodeId": "PROTEIN:TP53"
  },
  "options": {
    "format": "json",  // or "table", "graph"
    "timeout": 30000,  // ms
    "explain": false   // Return query plan instead of results
  }
}
```

**Response**:
```json
{
  "columns": ["p.id", "target.id", "target.name"],
  "data": [
    ["PROTEIN:TP53", "PROTEIN:MDM2", "MDM2"],
    ["PROTEIN:TP53", "PROTEIN:BAX", "BAX"]
  ],
  "metadata": {
    "execution_time_ms": 1.23,
    "rows_returned": 2,
    "query_plan": "..."
  }
}
```

**ObjectScript Implementation**:
```objectscript
Class GraphAPI.CypherQuery Extends %CSP.REST
{

/// Execute openCypher query
ClassMethod ExecuteCypher() As %Status
{
    Try {
        // Parse JSON request
        Set tRequest = ##class(%DynamicObject).%FromJSON(%request.Content)
        Set tCypherQuery = tRequest.query
        Set tParameters = tRequest.parameters

        // Translate Cypher → SQL
        Set tSQL = ##class(Python.CypherTranslator).Translate(tCypherQuery)

        // Execute SQL
        Set tStatement = ##class(%SQL.Statement).%New()
        Set tStatus = tStatement.%Prepare(tSQL)
        If $$$ISERR(tStatus) Quit tStatus

        Set tResult = tStatement.%Execute(tParameters...)

        // Format results
        Set tResponse = ##class(%DynamicObject).%New()
        Set tResponse.columns = ##class(%DynamicArray).%New()
        Set tResponse.data = ##class(%DynamicArray).%New()

        // Add column names
        For i=1:1:tResult.%ResultColumnCount {
            Do tResponse.columns.%Push(tResult.%GetMetadata().columns.GetAt(i).colName)
        }

        // Add data rows
        While tResult.%Next() {
            Set tRow = ##class(%DynamicArray).%New()
            For i=1:1:tResult.%ResultColumnCount {
                Do tRow.%Push(tResult.%GetData(i))
            }
            Do tResponse.data.%Push(tRow)
        }

        // Add metadata
        Set tMetadata = ##class(%DynamicObject).%New()
        Set tMetadata.rows_returned = tResponse.data.%Size()
        Set tResponse.metadata = tMetadata

        Write tResponse.%ToJSON()

    } Catch ex {
        Set tError = ##class(%DynamicObject).%New()
        Set tError.error = ex.DisplayString()
        Set tError.code = ex.Code
        Write tError.%ToJSON()
    }

    Quit $$$OK
}

}
```

**Python Client**:
```python
from iris_vector_graph import CypherClient

client = CypherClient(host='localhost', port=52773, namespace='USER',
                      username='_SYSTEM', password='SYS')

# Execute Cypher query
results = client.query(
    "MATCH (p:Protein)-[:INTERACTS_WITH]->(target) WHERE p.id = $nodeId RETURN target",
    parameters={"nodeId": "PROTEIN:TP53"}
)

for row in results:
    print(row['target'])
```

**Timeline**: 3 months
**Dependencies**: Milestones 1-3 (Parser, Translator, Optimizer)
**Risk**: Low (standard REST API pattern)

---

### Milestone 5: Hybrid Cypher+Vector Extensions (Months 13-15)

**Objective**: Add custom vector search functions to Cypher

**Deliverables**:
- [ ] Custom procedure: `db.index.vector.queryNodes()`
- [ ] Custom procedure: `db.index.vector.querySimilar()`
- [ ] Vector aggregation functions
- [ ] Hybrid query examples and documentation
- [ ] Performance benchmarks for hybrid queries

**Custom Vector Procedures**:

**Procedure 1: Vector k-NN Search**
```cypher
// Find 20 proteins most similar to query vector, then expand 1-hop
CALL db.index.vector.queryNodes('protein_embeddings', 20, $queryVector)
YIELD node AS centerNode, score AS similarity

MATCH (centerNode)-[r:INTERACTS_WITH]->(neighbor)
RETURN
    centerNode.id,
    similarity,
    type(r),
    neighbor.id,
    labels(neighbor)
ORDER BY similarity DESC
```

**Procedure 2: Similar Nodes**
```cypher
// Find nodes similar to a given node by comparing embeddings
MATCH (source:Protein {id: 'PROTEIN:TP53'})
CALL db.index.vector.querySimilar(source, 10)
YIELD node AS similar, score
RETURN similar.id, similar.name, score
ORDER BY score DESC
```

**Implementation**:
```objectscript
/// Custom Cypher procedure for vector k-NN search
ClassMethod VectorQueryNodes(
    indexName As %String,
    k As %Integer,
    queryVector As %String
) As %SQL.StatementResult
{
    Set tSQL = "
        SELECT TOP ? e.id AS node_id,
               VECTOR_DOT_PRODUCT(e.emb, TO_VECTOR(?, 'DOUBLE', 768)) AS score
        FROM kg_NodeEmbeddings e
        INNER JOIN nodes n ON e.id = n.node_id
        ORDER BY score DESC
    "

    Set tStatement = ##class(%SQL.Statement).%New()
    Do tStatement.%Prepare(tSQL)
    Quit tStatement.%Execute(k, queryVector)
}
```

**Timeline**: 3 months
**Dependencies**: Milestone 4 (REST API)
**Risk**: Medium (custom procedure integration)

---

### Milestone 6: GQL & Gremlin Support (Months 16-18)

**Objective**: Add support for GQL and Gremlin query languages

**Deliverables**:
- [ ] GQL parser integration (ISO GQL standard)
- [ ] Gremlin parser integration
- [ ] Unified AST for all query languages
- [ ] Multi-language query endpoint
- [ ] Performance comparison: Cypher vs GQL vs Gremlin

**GQL Example** (ISO standard graph query language):
```gql
-- GQL syntax (similar to Cypher but with SQL-like keywords)
GRAPH MyGraph
MATCH (p:Protein WHERE p.id = 'PROTEIN:TP53')-[:INTERACTS_WITH]->(target)
RETURN p.id, target.id, target.name
```

**Gremlin Example** (Apache TinkerPop):
```groovy
// Gremlin traversal syntax
g.V().has('Protein', 'id', 'PROTEIN:TP53')
    .out('INTERACTS_WITH')
    .values('id', 'name')
```

**Unified REST Endpoint**:
```bash
POST /api/query
{
  "language": "cypher",  // or "gql", "gremlin", "sql"
  "query": "MATCH (p:Protein) RETURN p.id",
  "parameters": {}
}
```

**Timeline**: 3 months
**Dependencies**: Milestones 1-5
**Risk**: High (multiple parser integrations, standards compliance)

---

## Success Metrics

### Performance Targets

| Query Pattern | Target Time | Baseline (SQL) | Overhead |
|--------------|-------------|----------------|----------|
| Simple MATCH | <1ms | 0.29ms | <10% |
| 1-hop traversal | <1ms | 0.09ms | <10% |
| 2-hop traversal | <2ms | 1.5ms | <10% |
| Vector k-NN (k=10) | <10ms | 1.7ms (HNSW) | <15% |
| Hybrid (vector + 1-hop) | <50ms | 45ms | <10% |

### Functional Coverage

- [ ] All openCypher syntax supported (MATCH, WHERE, RETURN, etc.)
- [ ] Variable-length paths (*1..n)
- [ ] OPTIONAL MATCH (LEFT JOIN)
- [ ] Aggregation (count, collect, avg, etc.)
- [ ] UNION / UNION ALL
- [ ] Custom vector procedures
- [ ] Named parameters ($param)
- [ ] Multi-label nodes (`:Protein:Gene`)

### API Stability

- [ ] REST API versioned (/api/v1/cypher)
- [ ] Backward compatibility guarantee
- [ ] Deprecation policy (1 version notice)
- [ ] Error codes documented

---

## Testing Strategy

### Unit Tests (Parser & Translator)

```python
# Parser tests
def test_parse_simple_match():
    ast = parse_cypher("MATCH (n:Protein) RETURN n.id")
    assert len(ast.matches) == 1
    assert ast.matches[0].nodes[0].labels == ['Protein']
    assert ast.returns == ['n.id']

# Translator tests
def test_translate_relationship_traversal():
    cypher = "MATCH (a)-[r:INTERACTS_WITH]->(b) RETURN a, b"
    sql = translator.translate(cypher)
    assert "JOIN rdf_edges" in sql
    assert "WHERE p = 'INTERACTS_WITH'" in sql
```

### Integration Tests (End-to-End)

```python
@pytest.mark.requires_database
def test_cypher_query_execution():
    """Execute Cypher query and verify results."""
    # Setup test data
    setup_test_graph()

    # Execute Cypher
    response = cypher_client.query(
        "MATCH (p:Protein {id: 'PROTEIN:TP53'})-[:INTERACTS_WITH]->(t) RETURN t.id"
    )

    assert len(response.data) > 0
    assert 'PROTEIN:MDM2' in [row[0] for row in response.data]
```

### Performance Tests (Benchmarks)

```python
def test_cypher_performance_vs_sql():
    """Cypher should be within 10% of direct SQL performance."""
    cypher = "MATCH (p:Protein)-[:INTERACTS_WITH]->(t) RETURN count(t)"
    sql = "SELECT COUNT(*) FROM rdf_edges WHERE p = 'INTERACTS_WITH'"

    cypher_time = benchmark(lambda: execute_cypher(cypher))
    sql_time = benchmark(lambda: execute_sql(sql))

    overhead = (cypher_time - sql_time) / sql_time
    assert overhead < 0.10, f"Overhead {overhead*100:.1f}% exceeds 10%"
```

---

## Risk Assessment

### High Risk Items

1. **Query Optimizer Complexity** (Milestone 3)
   - **Risk**: IRIS query planner may not use indexes optimally
   - **Mitigation**: Extensive benchmarking, index hint injection, collaboration with InterSystems

2. **Multi-Language Support** (Milestone 6)
   - **Risk**: GQL and Gremlin standards evolving, multiple parsers to maintain
   - **Mitigation**: Focus on Cypher first (80% of use cases), add GQL/Gremlin as optional

3. **Performance Overhead**
   - **Risk**: Translation overhead >10% makes Cypher impractical
   - **Mitigation**: Query result caching, prepared statement caching, optimizer tuning

### Medium Risk Items

1. **Parser Library Maturity**
   - **Risk**: libcypher-parser may not support latest Cypher features
   - **Mitigation**: Test against Neo4j Cypher reference, contribute fixes upstream

2. **Custom Vector Procedures**
   - **Risk**: IRIS may not support custom Cypher procedures easily
   - **Mitigation**: Implement as SQL functions first, wrap in Cypher syntax

### Low Risk Items

1. **REST API Development** - Standard pattern, well-understood
2. **Python Client** - Thin wrapper over REST API
3. **Documentation** - Clear examples from translation spec

---

## Resource Requirements

### Team Composition

- **Senior Backend Engineer** (Full-time, 18 months)
  - Query translator implementation
  - SQL optimizer
  - Performance tuning

- **Database Engineer** (Part-time, 12 months)
  - IRIS query planner expertise
  - Index optimization
  - Performance benchmarking

- **Frontend Engineer** (Part-time, 6 months)
  - Web UI for Cypher query editor
  - Visualization of query results

- **Technical Writer** (Part-time, 6 months)
  - API documentation
  - Cypher tutorial
  - Migration guide (SQL → Cypher)

### Infrastructure

- **Development IRIS instances** (3-5 containers)
  - Parser/translator testing
  - Performance benchmarking
  - Multi-version compatibility testing

- **CI/CD Pipeline**
  - Automated translation tests (500+ test cases)
  - Performance regression detection
  - Multi-language query validation

---

## Dependencies & Prerequisites

### Phase 1 Prerequisites (COMPLETE ✅)

- ✅ NodePK with FK constraints
- ✅ IRIS embedded Python
- ✅ HNSW vector index
- ✅ RDF-style tables (nodes, rdf_edges, rdf_labels, rdf_props)
- ✅ Performance benchmarks established

### External Dependencies

- **Parser Library**: libcypher-parser or opencypher
- **Testing Framework**: pytest with IRIS fixtures
- **Documentation**: Sphinx or MkDocs
- **CI/CD**: GitHub Actions or GitLab CI

### Compatibility Requirements

- **IRIS Version**: 2025.3+ (HNSW support)
- **Python Version**: 3.10+ (for type hints, pattern matching)
- **openCypher Version**: Target Cypher 9 (Neo4j 5.x compatibility)

---

## Migration Path: SQL Users → Cypher Users

### Training Materials

**SQL to Cypher Cheat Sheet**:
```
SQL                                  Cypher
───────────────────────────────────────────────────────────
SELECT n.node_id                     MATCH (n:Protein)
FROM nodes n                         RETURN n.id
WHERE label = 'Protein'

SELECT e.s, e.o_id                   MATCH (a)-[r:INTERACTS_WITH]->(b)
FROM rdf_edges e                     RETURN a.id, b.id
WHERE p = 'INTERACTS_WITH'

SELECT COUNT(*)                      MATCH (p:Protein)
FROM nodes n                         RETURN count(p)
WHERE label = 'Protein'

Recursive CTE for 2-hop              MATCH path = (a)-[:INTERACTS_WITH*1..2]->(b)
traversal (20+ lines)                RETURN path
```

### Gradual Adoption Strategy

**Phase A**: SQL-only (current state)
**Phase B**: Both SQL and Cypher available (Phase 2 launch)
**Phase C**: Cypher recommended, SQL supported (6 months post-launch)
**Phase D**: Cypher primary, SQL for advanced use cases (12 months post-launch)

---

## Success Criteria & Definition of Done

### Phase 2 Complete When:

- [ ] All 6 milestones delivered
- [ ] 500+ Cypher translation test cases passing
- [ ] Performance overhead <10% vs direct SQL
- [ ] REST API documented and stable (v1.0)
- [ ] Python client published to PyPI
- [ ] Web UI deployed and accessible
- [ ] 10+ production use cases validated
- [ ] Documentation complete (tutorials, API reference, migration guide)
- [ ] Constitutional validation: All 8 principles satisfied

### Market Readiness:

- [ ] Competitive analysis complete (vs Neo4j, TigerGraph, Amazon Neptune)
- [ ] Unique value proposition validated: "SQL + Cypher + Vector"
- [ ] Reference customers identified and onboarded
- [ ] Performance benchmarks published

---

## Next Phase: Phase 3 (GPU Acceleration)

After Phase 2 completion, Phase 3 will add:
- GPU-accelerated graph algorithms (cuGraph)
- 100x-1000x performance improvement
- Sub-second analytics on 1M+ node graphs

**Timeline**: Months 19-36 (see graph_analytics_roadmap.md:192-216)

---

## Summary

Phase 2 delivers **openCypher query capabilities** to IRIS Vector Graph, making it the **only database** supporting:
- ✅ SQL (native IRIS)
- ✅ openCypher (Phase 2)
- ✅ Vector similarity (HNSW)
- ✅ ACID guarantees
- ✅ Foreign key integrity

**Differentiator**: No other graph database offers all five.

**Timeline**: 12-18 months, 6 milestones
**Investment**: ~2 FTE-years
**ROI**: Unique market position, 10x easier graph queries, broader user base

Ready to proceed with **Milestone 1: Parser & AST**!
