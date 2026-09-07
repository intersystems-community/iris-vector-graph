<!-- markdownlint-disable MD013 -->

# Versioned Graph Demo

End-to-end walkthrough: named graphs, concurrent writer conflict, historical
reconstruction. Runnable against `ivg-iris-enterprise` (port 31972).

## Setup

```python
import iris.dbapi as dbapi
import threading
from iris_vector_graph import IRISGraphEngine
from iris_vector_graph.ledger import Changeset, StaleHeadError

conn = dbapi.connect(hostname="localhost", port=31972,
                     namespace="USER", username="_SYSTEM", password="SYS")
engine = IRISGraphEngine(conn, embedding_dimension=768)
engine.initialize_schema()
```

## Part 1 — Named Graphs

Multiple named graphs can coexist in the same IRIS database. The `^KG`
adjacency index partitions by graph key, so BFS and variable-length paths
stay within their graph.

```python
# Load UMLS concept hierarchy into a named graph
for concept_id, parent_id in [
    ("C0027051", "C0000000"),
    ("C0085580", "C0000000"),
    ("C0027051", "C0085580"),  # duplicate edge — idempotent
]:
    engine.create_node(concept_id, labels=["Concept"], graph="umls")
    if parent_id:
        engine.create_edge(concept_id, "ISA", parent_id, graph="umls")

engine.sync()

# Query stays within "umls" graph
result = engine.execute_cypher(
    "USE GRAPH umls MATCH (a:Concept)-[:ISA*1..3]->(b) "
    "WHERE a.id = $id RETURN b.id",
    {"id": "C0027051"}
)
print("UMLS ancestors:", [r[0] for r in result.rows])

# Import a staging snapshot, then promote by copying to default graph
engine.import_graph_ndjson("staging.ndjson", graph="staging")
# ... review, validate, then drop staging when done
engine.drop_graph("staging")
```

## Part 2 — Revision Ledger

Enable the ledger once; it captures the existing graph as the genesis revision.

```python
genesis = engine.ledger.enable()
print("Genesis revision:", genesis.revision_id)

# Commit a changeset
head = engine.ledger.head()
cs = Changeset(
    actor="demo:writer-A",
    actor_type="demo",
    message="Add equipment nodes",
    expected_head=head.revision_id,
    idempotency_key="demo-batch-001",
)
cs.create_node("pump-7", labels=["Equipment"], properties={"status": "active"})
cs.create_node("tank-2", labels=["Equipment"])
rel = cs.create_relationship("pump-7", "FEEDS", "tank-2",
                              qualifiers={"flow_rate": "120"})
cs.set_qualifier(rel, "units", "L/min")

result_A = engine.ledger.commit(cs)
print(f"Revision {result_A.revision.seq}: {result_A.revision.revision_id[:8]}...")
```

## Part 3 — Concurrent Branch Conflict

Two writers read the same head and race to commit. Exactly one wins; the
other catches `StaleHeadError` and retries.

```python
results = {}
errors = {}

def writer(name: str):
    head = engine.ledger.head()
    cs = Changeset(
        actor=f"demo:{name}",
        actor_type="demo",
        message=f"Concurrent write from {name}",
        expected_head=head.revision_id,
    )
    cs.create_node(f"node-{name}", labels=["Demo"])
    try:
        r = engine.ledger.commit(cs)
        results[name] = r.revision.seq
        print(f"{name} committed at seq={r.revision.seq}")
    except StaleHeadError:
        # Lost the race — re-read head and retry once
        head = engine.ledger.head()
        cs2 = Changeset(
            actor=f"demo:{name}",
            actor_type="demo",
            message=f"Retry from {name}",
            expected_head=head.revision_id,
        )
        cs2.create_node(f"node-{name}", labels=["Demo"])
        r = engine.ledger.commit(cs2)
        results[name] = r.revision.seq
        errors[name] = "retried"
        print(f"{name} retried and committed at seq={r.revision.seq}")

threads = [threading.Thread(target=writer, args=(f"writer-{i}",))
           for i in range(3)]
for t in threads:
    t.start()
for t in threads:
    t.join()

print("Sequence numbers:", sorted(results.values()))
print("Writers that retried:", list(errors.keys()))
```

Expected output (order varies):

```
writer-0 committed at seq=3
writer-1 retried and committed at seq=4
writer-2 retried and committed at seq=5
Sequence numbers: [3, 4, 5]
Writers that retried: ['writer-1', 'writer-2']
```

## Part 4 — History, Diffs, and Reconstruction

```python
# Page through the ledger
print("\n--- Revision history ---")
for rev in engine.ledger.history(limit=10):
    print(f"  seq={rev.seq:3d}  actor={rev.actor:<20}  {rev.message}")

# Diff: what changed between genesis and the equipment batch?
print("\n--- Diff genesis → batch ---")
for change in engine.ledger.diff(genesis.revision_id,
                                  result_A.revision.revision_id):
    print(f"  {change.op:<20} {change.entity_kind:<8} {change.entity_id}")

# Reconstruct the graph as it was after the equipment batch
print("\n--- Reconstructed graph at equipment revision ---")
snapshot = engine.ledger.reconstruct(result_A.revision.revision_id)
# snapshot is an IVGResult of NDJSON records
for row in snapshot.rows[:5]:
    print(" ", row)

# Export reconstruction to a file (can be imported as a named graph)
engine.ledger.export_reconstruction(
    result_A.revision.revision_id,
    "equipment-snapshot.ndjson"
)

# Import the snapshot into a named graph for side-by-side comparison
engine.import_graph_ndjson("equipment-snapshot.ndjson", graph="equipment-v1")

# Verify current tables match replay from genesis
report = engine.ledger.verify()
print(f"\nConsistent: {report.consistent}")
print(f"Unrecorded writes: {len(report.unrecorded_writes)}")
```

## Part 5 — Verify Live Metrics

```python
stats = engine.ledger.stats()
print(f"\nLedger stats:")
print(f"  head_seq={stats.head_seq}")
print(f"  commits_ok={stats.commits_ok}")
print(f"  rejections={stats.rejections}")

status = engine.status()
print(f"\nEngine status ledger section:")
print(status.ledger)
```

## Cleanup

```python
# Drop demo named graphs
engine.drop_graph("umls")
engine.drop_graph("equipment-v1")
```
