# Quickstart: BM25Index (044)

## SC-001: BM25 search returns ranked results

```python
import iris
from iris_devtester import IRISContainer
from iris_vector_graph.engine import IRISGraphEngine

c = IRISContainer.attach("iris_vector_graph")
conn = iris.connect(c.get_container_host_ip(), int(c.get_exposed_port(1972)), "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn)

# Load some nodes with "name" property
engine.create_node("NCIT:C1", labels=["Concept"], properties={"name": "ankylosing spondylitis HLA-B27 associated"})
engine.create_node("NCIT:C2", labels=["Concept"], properties={"name": "rheumatoid arthritis joint inflammation"})
engine.create_node("NCIT:C3", labels=["Concept"], properties={"name": "HLA-B27 antigen"})

# Build BM25 index
result = engine.bm25_build("test", text_props=["name"])
assert result["indexed"] == 3
assert result["vocab_size"] > 0

# Search — should rank NCIT:C1 first (contains all three query terms)
results = engine.bm25_search("test", "ankylosing spondylitis HLA-B27", k=3)
assert len(results) > 0
assert results[0][0] == "NCIT:C1"  # highest score
assert results[0][1] > results[1][1]  # sorted descending

# SC-002: latency
import time, statistics
lats = []
for _ in range(12):
    t0 = time.perf_counter_ns()
    engine.bm25_search("test", "HLA-B27 antigen", k=3)
    lats.append((time.perf_counter_ns() - t0) / 1e6)
assert statistics.median(lats[2:]) < 50, f"Search too slow: {statistics.median(lats[2:]):.1f}ms"

engine.bm25_drop("test")
```

## SC-004: kg_TXT upgrade via "default" index

```python
engine.create_node("NCIT:C10", labels=["Concept"], properties={"name": "diabetes mellitus type 2 insulin resistance"})
engine.create_node("NCIT:C11", labels=["Concept"], properties={"name": "hypertension blood pressure"})

# Build "default" index — triggers kg_TXT upgrade
engine.bm25_build("default", text_props=["name"])

# kg_TXT now uses BM25 instead of LIKE
results = engine.kg_TXT("diabetes insulin", k=5)
assert len(results) > 0
scores = [r[1] for r in results]
# BM25 scores are real floats, not 0.0 or 1.0
assert all(s > 0.0 and s != 1.0 for s in scores), f"Scores look like LIKE fallback: {scores}"

engine.bm25_drop("default")
```

## SC-007: Cypher CALL ivg.bm25.search

```python
engine.bm25_build("concepts", text_props=["name"])

results = engine.execute_cypher(
    "CALL ivg.bm25.search('concepts', $query, 5) YIELD node, score RETURN node, score",
    {"query": "ankylosing spondylitis"}
)
assert len(results["rows"]) > 0
assert results["rows"][0][1] > 0.0  # score column

engine.bm25_drop("concepts")
```
