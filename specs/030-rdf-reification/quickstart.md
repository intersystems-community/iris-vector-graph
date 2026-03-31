# Quickstart: RDF 1.2 Reification

**Feature**: 030-rdf-reification

## Reify an edge with metadata

```python
# Create an edge
engine.create_node("Aspirin", labels=["Drug"])
engine.create_node("Headache", labels=["Disease"])
engine.create_edge("Aspirin", "treats", "Headache")

# Find the edge_id
cursor.execute("SELECT edge_id FROM Graph_KG.rdf_edges WHERE s='Aspirin' AND p='treats' AND o_id='Headache'")
edge_id = cursor.fetchone()[0]

# Reify it with provenance and confidence
reifier_id = engine.reify_edge(edge_id, props={
    "confidence": "0.92",
    "source": "PMID:12345",
    "assertedBy": "agent:thomas",
    "accessPolicy": "kg_read",
})
# → "reif:42"
```

## Query reifications

```python
reifs = engine.get_reifications(edge_id)
# → [{"reifier_id": "reif:42", "properties": {"confidence": "0.92", "source": "PMID:12345", ...}}]
```

## KBAC access check (graph walk)

```python
# The reifier is a regular node — use standard graph traversal
ops = IRISGraphOperators(conn)

# Walk: does any path exist from user's permission to the reifier's accessPolicy?
user_perms = ops.kg_NEIGHBORS(["user:thomas"], predicate="HAS_PERMISSION")
# → ["perm:kg_read"]

# Check if edge's reification allows this permission
for reif in engine.get_reifications(edge_id):
    if reif["properties"].get("accessPolicy") in [p for p, _ in user_perms]:
        print("Access granted")
```

## Delete a reification

```python
engine.delete_reification("reif:42")
# Removes: junction row + reifier node + reifier's props/labels
# Original edge is preserved
```
