<!-- markdownlint-disable MD013 -->

# ADR-0009 — A Temporal Edge implies a Structural Edge

**Status**: Accepted; implementation in spec 252 (2026-10-19), with ADR-0008 step 2
**Date**: 2026-10-12
**Relates to**: ADR-0002, ADR-0008

## Decision

Writing a Temporal Edge for a `(source, predicate, target)` triple also asserts a Structural Edge row for that triple in
the same graph. The row carries a temporal-origin marker. Deleting the last timestamp of the triple removes the row only
while the marker is still set; an explicit `create_edge` on the same triple clears the marker, after which no
temporal delete can remove it. Existing installs get rows, marked, backfilled from the temporal globals at upgrade.

## Context

A temporal edge written without `graph=` creates the temporal globals and a structural shadow in `^KG("out"/"in")`
with no `rdf_edges` row. The rebuild refills `out`/`in` from SQL only, so `sync()` removes that shadow: measured, the
entry went from present to absent while the temporal data stayed. The edge then vanishes from every reader of the
Adjacency Index (`MatchEdges`, centrality, BFS) although it still exists. An index driven by the edge table cannot
produce an entry that has no row.

## Consequences

- Temporal triples become visible to SQL, Cypher `MATCH` and `edge_count`: a behaviour change, so a major version.
- The marker lets exports and counts tell the two apart.
- The tested behaviour that deleting the last timestamp removes the edge is kept for temporal-only triples.
- `TemporalIndex` stays the only owner of the temporal globals (ADR-0002); it asks the Adjacency module for the row.

## Considered options

- Rebuild the shadow from the temporal globals: the index would have two sources and could not be purely
  engine-maintained.
- Take temporal edges out of the Adjacency Index: breaks the documented visibility in `MatchEdges` and centrality.
- Never remove the row: changes the tested delete behaviour.
