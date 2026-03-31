# Contracts: RDF 1.2 Reification

**Feature**: 030-rdf-reification | **Date**: 2026-03-31

## Contract 1: Schema DDL

```sql
CREATE TABLE IF NOT EXISTS Graph_KG.rdf_reifications (
    reifier_id VARCHAR(256) %EXACT NOT NULL,
    edge_id BIGINT NOT NULL,
    CONSTRAINT pk_reifications PRIMARY KEY (reifier_id),
    CONSTRAINT fk_reif_node FOREIGN KEY (reifier_id) REFERENCES Graph_KG.nodes(node_id),
    CONSTRAINT fk_reif_edge FOREIGN KEY (edge_id) REFERENCES Graph_KG.rdf_edges(edge_id)
);
CREATE INDEX idx_reif_edge ON Graph_KG.rdf_reifications (edge_id);
```

## Contract 2: reify_edge()

```python
def reify_edge(self, edge_id: int, reifier_id: str = None,
               label: str = "Reification", props: dict = None) -> Optional[str]:
```
**Input**: edge_id (BIGINT), optional reifier_id, label, properties dict
**Behavior**: Verify edge exists → create reifier node → add label → insert junction row → store props
**Output**: reifier_id string, or None on failure

## Contract 3: get_reifications()

```python
def get_reifications(self, edge_id: int) -> List[dict]:
```
**Input**: edge_id (BIGINT)
**Output**: `[{"reifier_id": "reif:42", "properties": {"confidence": "0.92", ...}}, ...]`

## Contract 4: delete_reification()

```python
def delete_reification(self, reifier_id: str) -> bool:
```
**Input**: reifier_id string
**Behavior**: Delete junction row → delete reifier's props → delete reifier's labels → delete reifier node
**Output**: True on success

## Contract 5: Cascade on edge deletion

When an edge is deleted (via `delete_node()` cascade or direct), the deletion path must:
1. Find all reifier_ids for the deleted edge: `SELECT reifier_id FROM rdf_reifications WHERE edge_id = ?`
2. For each reifier: `delete_reification(reifier_id)`
3. Delete junction rows: `DELETE FROM rdf_reifications WHERE edge_id = ?`
