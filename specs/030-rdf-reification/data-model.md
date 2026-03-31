# Data Model: RDF 1.2 Reification

**Feature**: 030-rdf-reification | **Date**: 2026-03-31

## New Table: Graph_KG.rdf_reifications

```
rdf_reifications
├── reifier_id: VARCHAR(256) %EXACT    PK, FK → nodes.node_id
├── edge_id: BIGINT                    FK → rdf_edges.edge_id
```

**Indexes**:
- PK on `reifier_id` (covers reifier lookups)
- `idx_reif_edge` on `edge_id` (covers "find all reifications for this edge")

**FK Constraints**:
- `fk_reif_node`: `reifier_id` → `Graph_KG.nodes(node_id)` (SQL-level)
- `fk_reif_edge`: `edge_id` → `Graph_KG.rdf_edges(edge_id)` (SQL-level)

No ON DELETE CASCADE — manual cascade matches IVG pattern.

## How Reifiers Fit in the Graph

```
Graph_KG.nodes:           [reif:42]  ← regular node
Graph_KG.rdf_labels:      [reif:42, "Reification"]
Graph_KG.rdf_props:       [reif:42, "confidence", "0.92"]
                          [reif:42, "source", "PMID:12345"]
                          [reif:42, "accessPolicy", "kg_read"]
Graph_KG.rdf_reifications: [reif:42, 42]  ← links reifier to edge 42
Graph_KG.rdf_edges:        [42, "Aspirin", "treats", "Headache"]
```

The reifier is a regular node — PageRank, BFS, Cypher all see it. Its properties are standard rdf_props. The only new thing is the junction row linking it to the edge it annotates.

## No Changes to Existing Tables

`nodes`, `rdf_edges`, `rdf_labels`, `rdf_props`, `kg_NodeEmbeddings`, `fhir_bridges` — all unchanged.
