# Feature Specification: Incremental Node Embedding (embed_nodes)

**Feature Branch**: `041-embed-nodes`
**Created**: 2026-04-03
**Status**: Draft
**Priority**: P0 — MindWalk demo blocker

---

## Overview

`store_embeddings()` embeds a list of nodes you hand it explicitly. When a graph has 200K+ nodes (NCIT, KG8), you need to embed only the nodes that haven't been embedded yet — or only nodes matching a filter (e.g., only NCIT nodes, not KG8). You also need feedback during a 3-minute operation.

This feature adds `embed_nodes(where=, text_fn=, batch_size=, progress_callback=)` — a first-class incremental embedding method that reads from `Graph_KG.nodes` + `rdf_props`, applies a user-supplied text builder, checks which nodes are already in `kg_NodeEmbeddings`, skips them (unless `force=True`), and emits progress.

**The MindWalk use case**: NCIT (200K nodes) and KG8 (50K nodes) are both loaded into Graph_KG. Embed only new KG8 nodes without re-embedding NCIT:

```python
engine.embed_nodes(
    where="node_id NOT LIKE 'NCIT:%'",
    text_fn=lambda nid, props: props.get("name", nid),
    progress_callback=lambda n, total: print(f"{n}/{total}"),
)
```

---

## User Scenarios & Testing

### User Story 1 — Incremental embedding with where clause (P1)

```python
# Only embed nodes not yet in kg_NodeEmbeddings
engine.embed_nodes(
    where="node_id NOT LIKE 'NCIT:%'",
    text_fn=lambda nid, props: f"{props.get('name', nid)} {props.get('synonyms', '')}",
    batch_size=100,
)
```

**Acceptance Scenarios**:
1. After calling `embed_nodes(where="node_id NOT LIKE 'NCIT:%'")`, all non-NCIT nodes have embeddings in `kg_NodeEmbeddings`; NCIT nodes remain unchanged.
2. Re-running `embed_nodes` on same nodes → 0 new embeddings (idempotent by ID).
3. `embed_nodes(force=True)` re-embeds all nodes matching `where`.
4. `embed_nodes(where=None)` embeds all unembedded nodes in `Graph_KG.nodes`.

### User Story 2 — Progress callback (P1)

```python
engine.embed_nodes(
    text_fn=lambda nid, props: props.get("name", nid),
    progress_callback=lambda n, total: print(f"{n}/{total}"),
    batch_size=500,
)
```

**Acceptance Scenarios**:
1. `progress_callback(n, total)` is called after every batch.
2. `total` is the count of nodes to embed (filtered by `where`, minus already-embedded if not `force`).
3. A batch of 500 nodes triggers exactly one callback call.

### User Story 3 — Custom text builder (P2)

```python
engine.embed_nodes(
    text_fn=lambda nid, props: " | ".join([
        props.get("name", ""),
        props.get("definition", ""),
        " ".join(props.get("synonyms", []) if isinstance(props.get("synonyms"), list) else []),
    ]),
)
```

**Acceptance Scenarios**:
1. `text_fn` is called with `(node_id: str, props: dict)` where `props` is the merged `rdf_props` dict for that node.
2. If `text_fn` returns `""` or `None`, the node is skipped (no embedding stored, no error).
3. Default `text_fn=None` uses `node_id` as the embedding text.

---

## Requirements

| ID | Requirement |
|----|-------------|
| FR-001 | `embed_nodes(where=None, text_fn=None, batch_size=500, force=False, progress_callback=None) -> dict` MUST exist on `IRISGraphEngine` |
| FR-002 | `where` MUST be a SQL WHERE fragment applied to `Graph_KG.nodes.node_id` (e.g., `"node_id LIKE 'NCIT:%'"`) or None (all nodes) |
| FR-003 | Before embedding, already-embedded node IDs MUST be checked in `kg_NodeEmbeddings`; existing entries skipped unless `force=True` |
| FR-004 | `text_fn(node_id, props_dict) -> str` MUST be called to build embedding text; if None, node_id is used |
| FR-005 | `props_dict` MUST contain the merged `rdf_props` for the node (key → val mapping) |
| FR-006 | `progress_callback(n_done, n_total)` MUST be called after each batch if provided |
| FR-007 | Return value MUST be `{"embedded": int, "skipped": int, "total": int, "errors": int}` |
| FR-008 | `embed_nodes` MUST require `embedding_dimension` to be set on the engine |
| FR-009 | Nodes where `text_fn` returns None or "" MUST be skipped (counted in "skipped") |
| FR-010 | SQL injection via `where` MUST be blocked: `where` is validated with `sanitize_identifier`-equivalent checks or rejected if it contains semicolons/comments |

### Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NFR-001 | For 10K nodes, `embed_nodes` MUST complete in under 10 minutes at 100 nodes/min embedding throughput |
| NFR-002 | Memory usage MUST be O(batch_size), not O(total_nodes) |

---

## Out of Scope

- Embedding nodes from mapped SQL tables (use `attach_embeddings_to_table` from spec 040)
- Multi-threaded/async embedding
- Custom embedding dimension per-call (uses engine's configured dimension)

---

## Design Notes

The `where` clause is intentionally a raw SQL fragment for flexibility. Security note: it's applied as `WHERE node_id <user_fragment>` with no parameterization of the fragment itself — validated against a whitelist of allowed SQL operators/patterns at call time (no semicolons, no comments, no subqueries).

The `text_fn` signature matches `load_networkx`'s node iteration pattern, making it a natural extension of the existing load pipeline.
