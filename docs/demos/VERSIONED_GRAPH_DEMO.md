<!-- markdownlint-disable MD013 -->

# Versioned graph demo

For application developers who need a graph's history. The demo covers named graphs, then the revision ledger:
commits, a writer that loses a race and retries, history, a diff, a reconstruction and verification. Every block runs in order on any
namespace IVG is installed in, and every answer shown is worked by hand. The ledger API is in the
[User guide](../USER_GUIDE.md) and the [API reference](../API_REFERENCE.md).

## Setup

```python
import iris
from iris_vector_graph import IRISGraphEngine
from iris_vector_graph.ledger import Changeset, StaleHeadError

conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn, embedding_dimension=768)
engine.initialize_schema()
```

## 1. Named graphs

Graphs partition one namespace's rows; a walk in one never reaches another. A small concept hierarchy in the graph
`umls`. Writing an edge that is already there changes nothing and returns `False`:

```python
for concept in ("C0027051", "C0085580", "C0000000"):
    engine.create_node(concept, labels=["Concept"], graph="umls")
engine.create_edge("C0027051", "ISA", "C0000000", graph="umls")
engine.create_edge("C0085580", "ISA", "C0000000", graph="umls")
engine.create_edge("C0027051", "ISA", "C0085580", graph="umls")
engine.create_edge("C0027051", "ISA", "C0085580", graph="umls")
# → False
```

`C0027051`'s ancestors, one to three hops up, inside `umls` only. `C0000000` is reached twice (directly, and through
`C0085580`), so `DISTINCT` lists it once:

```python
engine.execute_cypher(
    "USE GRAPH 'umls' MATCH (a:Concept {node_id: $id})-[:ISA*1..3]->(b) "
    "RETURN DISTINCT b.node_id ORDER BY b.node_id",
    {"id": "C0027051"},
).rows
# → [['C0000000'], ['C0085580']]
```

A staging copy loaded from NDJSON into its own graph, checked, and dropped:

```python
with open("staging.ndjson", "w") as f:
    f.write('{"kind": "node", "id": "C9999999", "labels": ["Concept"]}\n')
    f.write('{"kind": "node", "id": "C0000000", "labels": ["Concept"]}\n')
    f.write('{"kind": "edge", "source": "C9999999", "predicate": "ISA", "target": "C0000000"}\n')
engine.import_graph_ndjson("staging.ndjson", graph="staging")
# → {'nodes': 2, 'edges': 1, 'temporal_edges': 0, 'skipped': 0}
```

```python
engine.execute_cypher("USE GRAPH 'staging' MATCH (n) RETURN count(n)").rows
# → [[2]]
```

```python
engine.erase_graph("staging")
engine.execute_cypher("USE GRAPH 'staging' MATCH (n) RETURN count(n)").rows
# → [[0]]
```

## 2. The revision ledger

Enabling the ledger records what the namespace already holds as the **genesis** revision, seq 1. From then on a
**changeset** is committed as one revision:

```python
genesis = engine.ledger.enable()
genesis.seq
# → 1
```

```python
cs = Changeset(actor="demo:writer", actor_type="agent", message="Add equipment",
               expected_head=engine.ledger.head().revision_id)
cs.create_node("pump-7", labels=["Equipment"], properties={"status": "active"})
cs.create_node("tank-2", labels=["Equipment"])
rel = cs.create_relationship("pump-7", "FEEDS", "tank-2", qualifiers={"flow_rate": "120"})
cs.set_qualifier(rel, "units", "L/min")
batch = engine.ledger.commit(cs)
batch.revision.seq
# → 2
```

## 3. Two writers, one head

Two writers read the same head. The first to commit wins; the second's `expected_head` is now stale, so its commit is
refused with `StaleHeadError` and nothing of it is written. It reads the head again and retries:

```python
head = engine.ledger.head().revision_id
cs_a = Changeset(actor="demo:writer-a", actor_type="agent", expected_head=head)
cs_a.create_node("node-a", labels=["Demo"])
cs_b = Changeset(actor="demo:writer-b", actor_type="agent", expected_head=head)
cs_b.create_node("node-b", labels=["Demo"])

engine.ledger.commit(cs_a).revision.seq
# → 3
```

```python
try:
    engine.ledger.commit(cs_b)
    outcome = "committed"
except StaleHeadError:
    outcome = "stale"
outcome
# → 'stale'
```

```python
cs_b.expected_head = engine.ledger.head().revision_id
engine.ledger.commit(cs_b).revision.seq
# → 4
```

A changeset with no `expected_head` is not checked against the head; one with an `idempotency_key` that was already
committed is replayed, not written twice.

## 4. History, diffs and reconstruction

`history()` answers a page (`HistoryPage`) of revisions, oldest first:

```python
[(r.seq, r.kind, r.actor) for r in engine.ledger.history(limit=10).revisions]
# → [(1, 'genesis', 'system:ledger'), (2, 'changeset', 'demo:writer'), (3, 'changeset', 'demo:writer-a'), (4, 'changeset', 'demo:writer-b')]
```

`diff(from, to)` is the net change between two revisions, one entry per entity and attribute. The nodes that came
into existence between genesis and the equipment batch:

```python
diff = engine.ledger.diff(genesis.revision_id, batch.revision.revision_id)
sorted(e.entity_id for e in diff.entries if e.entity_kind == "node" and e.attr == "")
# → ['pump-7', 'tank-2']
```

`reconstruct(revision)` replays the records up to a revision into the graph as it was then (a `GraphState`). After
the batch, `pump-7` existed and `node-a` did not yet:

```python
state = engine.ledger.reconstruct(batch.revision.revision_id)
(sorted(state.nodes["pump-7"].labels), "node-a" in state.nodes)
# → (['Equipment'], False)
```

`verify()` replays the whole ledger and compares the result with the tables. Every write since genesis went through
the ledger, so they agree:

```python
engine.ledger.verify().result
# → 'equal'
```

A reconstruction exported as NDJSON can be imported as a named graph, to compare it with the live graph side by side.
That import is written outside the ledger, so a `verify()` after it reports the difference:

```python
engine.ledger.export_reconstruction(batch.revision.revision_id, "equipment-v1.ndjson")
engine.import_graph_ndjson("equipment-v1.ndjson", graph="equipment-v1")
engine.execute_cypher(
    "USE GRAPH 'equipment-v1' MATCH (p {node_id: 'pump-7'})-[:FEEDS]->(t) RETURN t.node_id"
).rows
# → [['tank-2']]
```

## 5. The counters

`stats()` reads the counters IRIS keeps for every process. `commits_ok` counts every revision written, genesis
included; the refused commit of part 3 is a stale-head rejection:

```python
stats = engine.ledger.stats()
(stats.head_seq, stats.commits_ok, stats.rejections["rej_stale_head"])
# → (4, 4, 1)
```

`print(engine.status())` shows the same in its `Ledger:` line. To publish them, see
[Ledger metrics](../ledger-prometheus-hook.md).

## Cleanup

```python
engine.erase_graph("umls")
engine.erase_graph("equipment-v1")
```

The ledger keeps its history after an erase: erasure removes content, not revisions. Removing an erased graph's
content from the history is `ledger.redact` (ADR-0013).
