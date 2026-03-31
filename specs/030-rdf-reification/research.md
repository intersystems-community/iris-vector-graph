# Research: RDF 1.2 Reification

**Feature**: 030-rdf-reification | **Date**: 2026-03-31

## R1: Table Design — Option C (Reifier as Node)

**Decision**: Separate `rdf_reifications` junction table. Reifiers are regular nodes in `Graph_KG.nodes`.

**Rationale**: The arno team evaluated 3 options (A: edge_props table, B: column on rdf_edges, C: separate junction). Option C was selected because: zero changes to existing tables, Edge.cls is Final (can't modify), matches RDF 1.2 semantics (reifier is first-class entity), graph algorithms see reifiers automatically.

**Alternatives rejected**:
- Option A (rdf_edge_props): Conflates edge with metadata. No reification-of-reification.
- Option B (reification_id column on rdf_edges): Breaks UNIQUE(s,p,o_id). Changes GraphIndex signature.

## R2: Cascade Strategy

**Decision**: Manual cascade in Python `delete_reification()` and edge deletion paths. No SQL ON DELETE CASCADE.

**Rationale**: Matches IVG's existing pattern — `delete_node()` already cascades manually to `kg_NodeEmbeddings`, `rdf_edges`, `rdf_labels`, `rdf_props`. Adding `rdf_reifications` to the cascade list is consistent.

## R3: Reifier Node Convention

**Decision**: Reifier IDs default to `reif:<edge_id>`. Label defaults to `Reification`. Custom IDs and labels supported.

**Rationale**: The `reif:` prefix convention makes reifiers identifiable by pattern. Standard label enables label-based queries (`MATCH (r:Reification)`).
