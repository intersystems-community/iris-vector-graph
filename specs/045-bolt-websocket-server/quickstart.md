# Quickstart: Bolt WebSocket Server

## Start the server

```bash
IRIS_HOST=localhost IRIS_PORT=32777 IRIS_NAMESPACE=USER \
IRIS_USERNAME=test IRIS_PASSWORD=test IVG_API_KEY=ivg-local-test \
python3 -m uvicorn iris_vector_graph.cypher_api:app --host 0.0.0.0 --port 8000
```

## Connect Neo4j Browser

Open: http://localhost:8000/browser/

Connect URL: `bolt://localhost:8000` (pre-filled)
Username/Password: leave blank (or use anything/"ivg-local-test" if key set)

Run:
```cypher
MATCH (n)-[r]->(m) RETURN n, r, m LIMIT 25
```

## Connect Python driver

```python
from neo4j import GraphDatabase

driver = GraphDatabase.driver("bolt://localhost:8000", auth=("", "ivg-local-test"))
with driver.session() as s:
    result = s.run("MATCH (n) RETURN count(n) AS c")
    print(result.single()["c"])
driver.close()
```

## Connect LangChain

```python
from langchain_community.graphs import Neo4jGraph

graph = Neo4jGraph(
    url="bolt://localhost:8000",
    username="",
    password="ivg-local-test",
)
print(graph.query("MATCH (n)-[r]->(m) RETURN count(r) AS edges"))
```

## Test with curl (HTTP API still works)

```bash
curl -X POST http://localhost:8000/api/cypher \
  -H "X-API-Key: ivg-local-test" \
  -H "Content-Type: application/json" \
  -d '{"query": "MATCH (n) RETURN count(n) AS c"}'
```
