# Quickstart: openCypher Query Endpoint

**Feature**: openCypher-to-SQL translation for IRIS Vector Graph
**Audience**: Developers integrating Cypher queries into applications
**Prerequisites**: Running IRIS instance with NodePK schema, Python 3.11+

---

## Setup

### 1. Install Dependencies

```bash
# Add required dependencies
cd /Users/tdyar/ws/iris-vector-graph
uv add fastapi uvicorn opencypher pydantic

# Activate virtual environment
source .venv/bin/activate
```

### 2. Load Cypher SQL Procedures

```sql
-- Connect to IRIS SQL terminal
docker exec -it iris-acorn-1 iris session iris

-- Load custom vector search procedure for Cypher
\i sql/procedures/cypher_vector_search.sql
```

### 3. Start ASGI Server

```bash
# Start FastAPI server with uvicorn
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Server will be available at http://localhost:8000
# API docs at http://localhost:8000/docs (Swagger UI)
```

### 4. Verify Installation

```bash
# Test Cypher parser
uv run python -c "
from iris_vector_graph.cypher import parse_query
ast = parse_query('MATCH (n:Protein) RETURN n.name LIMIT 5')
print('Parser OK:', ast is not None)
"

# Test ASGI endpoint
curl -X POST http://localhost:8000/api/cypher \
  -H "Content-Type: application/json" \
  -d '{"query": "MATCH (n:Protein) RETURN count(n) AS protein_count"}'
```

Expected output:
```json
{
  "columns": ["protein_count"],
  "rows": [[1234]],
  "rowCount": 1,
  "executionTimeMs": 5.2,
  "translationTimeMs": 1.8,
  "traceId": "cypher-20251002-xyz"
}
```

---

## Example Queries

### 1. Simple Node Lookup

**Cypher**:
```cypher
MATCH (p:Protein {id: 'PROTEIN:TP53'})
RETURN p.name, p.function
```

**REST API**:
```bash
curl -X POST http://localhost:8000/api/cypher \
  -H "Content-Type: application/json" \
  -d '{
    "query": "MATCH (p:Protein {id: '\''PROTEIN:TP53'\''}) RETURN p.name, p.function"
  }'
```

**Expected Result**:
```json
{
  "columns": ["name", "function"],
  "rows": [["Tumor protein p53", "Tumor suppressor protein"]],
  "rowCount": 1,
  "executionTimeMs": 3.5,
  "translationTimeMs": 1.2,
  "traceId": "cypher-20251002-001"
}
```

**Equivalent SQL** (for reference):
```sql
SELECT p.val AS name, f.val AS function
FROM nodes n
INNER JOIN rdf_labels l ON n.node_id = l.s AND l.label = 'Protein'
INNER JOIN rdf_props id_prop ON n.node_id = id_prop.s AND id_prop.key = 'id' AND id_prop.val = 'PROTEIN:TP53'
INNER JOIN rdf_props p ON n.node_id = p.s AND p.key = 'name'
INNER JOIN rdf_props f ON n.node_id = f.s AND f.key = 'function';
```

---

### 2. Graph Traversal (2-hop)

**Cypher**:
```cypher
MATCH (p:Protein)-[:INTERACTS_WITH]->(partner:Protein)
WHERE p.id = 'PROTEIN:TP53'
RETURN partner.name, partner.id
ORDER BY partner.name
LIMIT 10
```

**REST API**:
```bash
curl -X POST http://localhost:8000/api/cypher \
  -H "Content-Type: application/json" \
  -d '{
    "query": "MATCH (p:Protein)-[:INTERACTS_WITH]->(partner:Protein) WHERE p.id = '\''PROTEIN:TP53'\'' RETURN partner.name, partner.id ORDER BY partner.name LIMIT 10"
  }'
```

**Expected Result**:
```json
{
  "columns": ["name", "id"],
  "rows": [
    ["ATM serine/threonine kinase", "PROTEIN:ATM"],
    ["Checkpoint kinase 2", "PROTEIN:CHEK2"],
    ["MDM2 proto-oncogene", "PROTEIN:MDM2"]
  ],
  "rowCount": 3,
  "executionTimeMs": 8.7,
  "translationTimeMs": 2.3,
  "traceId": "cypher-20251002-002"
}
```

---

### 3. Variable-Length Paths

**Cypher**:
```cypher
MATCH (start:Protein {id: 'PROTEIN:TP53'})-[:INTERACTS_WITH*1..3]->(end:Protein)
RETURN DISTINCT end.name, end.id
LIMIT 20
```

**REST API**:
```bash
curl -X POST http://localhost:8000/api/cypher \
  -H "Content-Type: application/json" \
  -d '{
    "query": "MATCH (start:Protein {id: '\''PROTEIN:TP53'\''})-[:INTERACTS_WITH*1..3]->(end:Protein) RETURN DISTINCT end.name, end.id LIMIT 20"
  }'
```

**Expected Result**:
```json
{
  "columns": ["name", "id"],
  "rows": [
    ["ATM serine/threonine kinase", "PROTEIN:ATM"],
    ["BRCA1 DNA repair associated", "PROTEIN:BRCA1"],
    ["Cyclin D1", "PROTEIN:CCND1"]
  ],
  "rowCount": 20,
  "executionTimeMs": 45.2,
  "translationTimeMs": 5.1,
  "traceId": "cypher-20251002-003"
}
```

---

### 4. Hybrid Vector + Graph Query

**Cypher**:
```cypher
CALL db.index.vector.queryNodes('protein_embeddings', 10, $queryVector)
YIELD node, score
MATCH (node)-[:ASSOCIATED_WITH]->(d:Disease)
RETURN node.name AS protein, d.name AS disease, score
ORDER BY score DESC
```

**Python Client**:
```python
import requests
import numpy as np

# Generate query embedding (example: random 768-dim vector)
query_vector = np.random.rand(768).tolist()

response = requests.post('http://localhost:8000/api/cypher', json={
    'query': '''
        CALL db.index.vector.queryNodes('protein_embeddings', 10, $queryVector)
        YIELD node, score
        MATCH (node)-[:ASSOCIATED_WITH]->(d:Disease)
        RETURN node.name AS protein, d.name AS disease, score
        ORDER BY score DESC
    ''',
    'parameters': {
        'queryVector': query_vector
    }
})

result = response.json()
print(f"Found {result['rowCount']} protein-disease associations")
for row in result['rows']:
    print(f"  {row[0]} → {row[1]} (score: {row[2]:.3f})")
```

**Expected Output**:
```
Found 8 protein-disease associations
  Tumor protein p53 → Lung cancer (score: 0.923)
  EGFR receptor → Glioblastoma (score: 0.891)
  BRCA1 DNA repair → Breast cancer (score: 0.876)
```

---

### 5. Parameterized Queries

**Cypher**:
```cypher
MATCH (p:Protein)
WHERE p.id = $proteinId
RETURN p.name, p.function
```

**REST API**:
```bash
curl -X POST http://localhost:8000/api/cypher \
  -H "Content-Type: application/json" \
  -d '{
    "query": "MATCH (p:Protein) WHERE p.id = $proteinId RETURN p.name, p.function",
    "parameters": {
      "proteinId": "PROTEIN:TP53"
    }
  }'
```

**Benefits**:
- SQL injection prevention (parameters are safely bound)
- Query plan caching (same translated SQL for different parameter values)
- Better performance for repeated queries

---

### 6. Aggregation Queries

**Cypher**:
```cypher
MATCH (p:Protein)-[r:INTERACTS_WITH]->(target:Protein)
RETURN p.name, count(r) AS interaction_count
ORDER BY interaction_count DESC
LIMIT 10
```

**REST API**:
```bash
curl -X POST http://localhost:8000/api/cypher \
  -H "Content-Type: application/json" \
  -d '{
    "query": "MATCH (p:Protein)-[r:INTERACTS_WITH]->(target:Protein) RETURN p.name, count(r) AS interaction_count ORDER BY interaction_count DESC LIMIT 10"
  }'
```

**Expected Result**:
```json
{
  "columns": ["name", "interaction_count"],
  "rows": [
    ["Ubiquitin C", 1432],
    ["Heat shock protein 90", 987],
    ["Tumor protein p53", 127]
  ],
  "rowCount": 10,
  "executionTimeMs": 23.4,
  "translationTimeMs": 3.2,
  "traceId": "cypher-20251002-004"
}
```

---

## Testing Your Integration

### Integration Test Template

```python
import pytest
import requests
import iris

@pytest.mark.requires_database
@pytest.mark.integration
def test_cypher_protein_lookup():
    """Test simple Cypher query against live IRIS database"""

    # Execute Cypher query via REST API
    response = requests.post('http://localhost:8000/api/cypher', json={
        'query': "MATCH (p:Protein {id: 'PROTEIN:TP53'}) RETURN p.name"
    })

    assert response.status_code == 200
    result = response.json()

    # Validate response structure
    assert 'columns' in result
    assert 'rows' in result
    assert 'rowCount' in result
    assert 'executionTimeMs' in result
    assert 'traceId' in result

    # Validate results
    assert result['columns'] == ['name']
    assert result['rowCount'] == 1
    assert result['rows'][0][0] == 'Tumor protein p53'

    # Verify performance
    assert result['executionTimeMs'] < 50  # Should be fast

    # Compare with direct SQL (validation)
    conn = iris.connect('localhost', 1972, 'USER', '_SYSTEM', 'SYS')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.val AS name
        FROM nodes n
        INNER JOIN rdf_labels l ON n.node_id = l.s AND l.label = 'Protein'
        INNER JOIN rdf_props id_prop ON n.node_id = id_prop.s
            AND id_prop.key = 'id' AND id_prop.val = 'PROTEIN:TP53'
        INNER JOIN rdf_props p ON n.node_id = p.s AND p.key = 'name'
    """)
    sql_result = cursor.fetchone()

    # Cypher and SQL should return identical results
    assert result['rows'][0][0] == sql_result[0]
```

### Performance Comparison Test

```python
@pytest.mark.requires_database
@pytest.mark.integration
def test_cypher_performance_overhead():
    """Verify Cypher execution is within 10% of SQL execution time"""
    import time

    cypher_query = "MATCH (p:Protein)-[:INTERACTS_WITH]->(t:Protein) RETURN p.name, t.name LIMIT 100"

    # Execute Cypher query
    start = time.time()
    cypher_response = requests.post('http://localhost:8000/api/cypher', json={
        'query': cypher_query
    })
    cypher_time = (time.time() - start) * 1000  # Convert to ms

    # Execute equivalent SQL query
    conn = iris.connect('localhost', 1972, 'USER', '_SYSTEM', 'SYS')
    cursor = conn.cursor()
    start = time.time()
    cursor.execute("""
        SELECT p_name.val AS p_name, t_name.val AS t_name
        FROM rdf_edges e
        INNER JOIN nodes p_node ON e.s = p_node.node_id
        INNER JOIN nodes t_node ON e.o_id = t_node.node_id
        INNER JOIN rdf_labels p_label ON p_node.node_id = p_label.s AND p_label.label = 'Protein'
        INNER JOIN rdf_labels t_label ON t_node.node_id = t_label.s AND t_label.label = 'Protein'
        INNER JOIN rdf_props p_name ON p_node.node_id = p_name.s AND p_name.key = 'name'
        INNER JOIN rdf_props t_name ON t_node.node_id = t_name.s AND t_name.key = 'name'
        WHERE e.p = 'INTERACTS_WITH'
        LIMIT 100
    """)
    cursor.fetchall()
    sql_time = (time.time() - start) * 1000

    overhead_percent = ((cypher_time / sql_time) - 1) * 100

    print(f"Cypher: {cypher_time:.1f}ms, SQL: {sql_time:.1f}ms, Overhead: {overhead_percent:.1f}%")

    # Assert <10% overhead (per spec requirement)
    assert overhead_percent < 10.0
```

---

## Troubleshooting

### Error: Parser not found

```
ImportError: No module named 'opencypher'
```

**Solution**: Install opencypher package
```bash
uv add opencypher
```

---

### Error: Syntax error in Cypher query

```json
{
  "errorType": "syntax",
  "message": "Unexpected token 'RETRUN' at line 1, column 1",
  "errorCode": "SYNTAX_ERROR",
  "suggestion": "Did you mean 'RETURN'?"
}
```

**Solution**: Check Cypher syntax, use line/column numbers to locate error

---

### Error: Undefined variable

```json
{
  "errorType": "translation",
  "message": "Undefined variable 'm' in RETURN clause",
  "errorCode": "UNDEFINED_VARIABLE",
  "suggestion": "Define 'm' in a MATCH clause before using in RETURN"
}
```

**Solution**: All variables in RETURN/WHERE must be defined in MATCH clauses

---

### Error: Query timeout

```json
{
  "errorType": "timeout",
  "message": "Query execution exceeded timeout of 30 seconds",
  "errorCode": "QUERY_TIMEOUT"
}
```

**Solution**: Increase timeout or reduce query complexity
```bash
curl -X POST http://localhost:8000/api/cypher \
  -H "Content-Type: application/json" \
  -d '{
    "query": "...",
    "timeout": 60
  }'
```

---

### Error: FK constraint violation

```json
{
  "errorType": "execution",
  "message": "Foreign key constraint violation: node 'PROTEIN:INVALID' not found",
  "errorCode": "FK_CONSTRAINT_VIOLATION"
}
```

**Solution**: Ensure all referenced nodes exist in the `nodes` table. Use NodePK migration if needed.

---

### Performance: Query is slow

**Check**:
1. Is HNSW index enabled for vector queries? (Requires ACORN-1 or IRIS 2025.3+)
2. Are labels and properties indexed? (Check `rdf_labels`, `rdf_props` indexes)
3. Is query complexity excessive? (>5 hops, Cartesian products)

**Debug**:
```bash
# Enable query metadata in response
curl -X POST http://localhost:8000/api/cypher \
  -H "Content-Type: application/json" \
  -d '{
    "query": "...",
    "enableOptimization": true
  }'

# Check queryMetadata.optimizationsApplied in response
```

---

## Next Steps

1. **Explore Syntax**: Review openCypher documentation for advanced patterns (OPTIONAL MATCH, UNION, subqueries)
2. **Optimize Queries**: Use EXPLAIN to analyze SQL execution plans
3. **Integrate with Applications**: Use Python client library for programmatic access
4. **Monitor Performance**: Track execution times via `docs/performance/cypher_benchmarks.json`
5. **Extend with Custom Procedures**: Add domain-specific Cypher procedures for specialized operations

---

**Quickstart Complete**: You can now execute Cypher queries against IRIS Vector Graph!
