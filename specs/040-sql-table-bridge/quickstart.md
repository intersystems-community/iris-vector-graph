# Quickstart: SQL Table Bridge (040)

## SC-001: Cypher returns identical results to direct SQL

```python
import iris
from iris_devtester import IRISContainer
from iris_vector_graph.engine import IRISGraphEngine

c = IRISContainer.attach("iris_vector_graph")
conn = iris.connect(c.get_container_host_ip(), int(c.get_exposed_port(1972)), "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn)

# Setup: create a real test table
cur = conn.cursor()
cur.execute("CREATE TABLE Bridge_Test.Patient (PatientID VARCHAR(20) PRIMARY KEY, Name VARCHAR(100), MRN VARCHAR(20))")
cur.execute("INSERT INTO Bridge_Test.Patient VALUES ('P001', 'Jane Doe', 'MRN-001234')")
cur.execute("INSERT INTO Bridge_Test.Patient VALUES ('P002', 'John Smith', 'MRN-005678')")
conn.commit()

# Register mapping
engine.map_sql_table("Bridge_Test.Patient", id_column="PatientID", label="Patient")

# Cypher query
result = engine.execute_cypher("MATCH (n:Patient) WHERE n.MRN = $mrn RETURN n.Name", {"mrn": "MRN-001234"})
assert result["rows"][0][0] == "Jane Doe"

# Verify identical to direct SQL
cur.execute("SELECT Name FROM Bridge_Test.Patient WHERE MRN = ?", ["MRN-001234"])
sql_result = cur.fetchone()
assert result["rows"][0][0] == sql_result[0]

# SC-004: verify zero writes to Graph_KG.nodes
cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id LIKE 'Patient:%'")
assert cur.fetchone()[0] == 0
```

## SC-003: Performance within 2× of direct SQL

```python
import time, statistics
WARMUP, REPS = 2, 10

def bench(fn):
    lats = []
    for _ in range(WARMUP + REPS):
        t0 = time.perf_counter_ns(); fn(); t1 = time.perf_counter_ns()
        lats.append((t1 - t0) / 1e6)
    return statistics.median(lats[WARMUP:])

cypher_lat = bench(lambda: engine.execute_cypher(
    "MATCH (n:Patient) WHERE n.MRN = $mrn RETURN n.Name", {"mrn": "MRN-001234"}
))
sql_lat = bench(lambda: [cur.execute("SELECT Name FROM Bridge_Test.Patient WHERE MRN = ?", ["MRN-001234"]), cur.fetchone()])

assert cypher_lat <= sql_lat * 2, f"Cypher {cypher_lat:.2f}ms > 2× SQL {sql_lat:.2f}ms"
```

## US4: attach_embeddings_to_table

```python
engine.attach_embeddings_to_table(
    label="Patient",
    text_columns=["Name"],
    batch_size=100,
    progress_callback=lambda n, total: print(f"{n}/{total}"),
)
# Verify embeddings stored
cur.execute("SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings WHERE id LIKE 'Patient:%'")
assert cur.fetchone()[0] == 2

# Idempotent: re-run skips existing
result = engine.attach_embeddings_to_table(label="Patient", text_columns=["Name"])
assert result["skipped"] == 2
assert result["embedded"] == 0
```
