# Enhancement: RDF 1.2 Reification for KBAC

**Date**: 2026-03-31
**Status**: Requested (from arno team)
**Source**: `arno/specs/034-niche-knowledge-graph/ivg-reification-request.md` (975 lines)
**Effort**: ~3 days estimated
**W3C Reference**: RDF 1.2 Concepts Working Draft, 28 March 2026

---

## Problem

Edge metadata lives in the `qualifiers` JSON column — opaque to SQL, not indexable,
not graph-traversable. This blocks KBAC (Knowledge-Based Access Control) where
authorization decisions need to be graph-walkable entities.

## What Reification Adds

Statements *about* edges become first-class triples:

```
:Aspirin :treats :Headache .                          # the edge
:reif_1  rdf:reifies <<( :Aspirin :treats :Headache )>> .
:reif_1  :confidence 0.92 .                           # metadata about the edge
:reif_1  :source "PubMed:12345" .
:reif_1  :accessPolicy :perm_kg_read .                # KBAC
```

This metadata is now queryable via standard graph traversal (PageRank, BFS, Cypher).

## Schema Change

**One new table** — zero changes to existing tables:

```sql
CREATE TABLE Graph_KG.rdf_reifications (
    reif_id VARCHAR(256) PRIMARY KEY,
    edge_id BIGINT NOT NULL,           -- FK → rdf_edges.edge_id
    predicate VARCHAR(128) NOT NULL,
    value VARCHAR(64000),
    value_type VARCHAR(64) DEFAULT 'literal'
)
```

## Implementation Scope

| Component | Change |
|-----------|--------|
| `Graph_KG.rdf_reifications` | New SQL table |
| `Graph.KG.Reification.cls` | New ObjectScript class |
| `engine.py` | `create_reification()`, `get_reifications()`, `delete_reification()` |
| `schema.py` | DDL for new table |
| `^KG` globals | Optional: `^KG("reif", edgeId, predicate)` for graph-traversable metadata |
| Cypher translator | Phase 2: `MATCH ()-[r]->() WHERE r.confidence > 0.9` via reification join |

## What Does NOT Change

- `rdf_edges` table — untouched
- `Edge.cls` — untouched
- `GraphIndex` functional index — untouched
- `^KG` adjacency structure — untouched (reification is additive)

## Use Cases

1. **KBAC**: `(:reif, accessPolicy, perm:kg_read)` → graph-walk authorization
2. **Provenance**: `(:reif, source, "PMID:12345")` → track where edges came from
3. **Confidence**: `(:reif, confidence, 0.92)` → queryable edge weights
4. **Audit**: `(:reif, assertedBy, agent:thomas)` → who added this edge

## Timeline

Not for READY talk. Post-READY feature — estimated ~3 days with the arno team's
detailed spec as input. Full implementation guide is in the arno repo (975 lines,
includes DDL, ObjectScript class, Python API, migration path, test plan).
