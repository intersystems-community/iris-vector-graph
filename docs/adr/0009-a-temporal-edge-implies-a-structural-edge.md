<!-- markdownlint-disable MD013 -->

# ADR-0009 — A Temporal Edge implies a Structural Edge

**Status**: Accepted; implemented in spec 252 (2026-10-20), with ADR-0008 step 2; amended by spec 254 (2026-10-22)
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

## Amendment (spec 254, 2026-10-22): the implication ends when the last timestamp goes

The row means "this edge exists at some time". When the last timestamp of a triple goes, by `DeleteTemporalEdge` as
before or by a retention purge (`PurgeRawBefore`, `PurgeBefore`), that is no longer true, and the row goes with it while
it still carries the marker, in every graph. Before this, a purge removed `tout`, `tin` and `edgeprop` and left the row
and its `^KG("out"/"in")` entries, so a graph with one node per event grew its adjacency without bound under retention.

- **The marker is the distinction, not a reference count.** A row an explicit write asserted is unmarked and outlives
  the purge of its timestamps. `create_edge` and, from spec 254, `bulk_create_edges` adopt a marked row (the marker
  goes); a Cypher `CREATE`/`MERGE` of a triple that already has a marked row does not yet, and is an open item. Whether
  a timestamp still holds the triple is answered by the purge itself, with one walk of the surviving timestamps, so no
  store is added and existing rows need no migration.
- **Rows first, then the raw entries.** A purge that fails part way leaves timestamps without rows, and running it again
  finishes it; the other order would leave marked rows no purge can find.
- **Nodes stay.** A purge removes edges, not nodes; a node left with no edge has degree 0.
- The bucket aggregates stay, as before: they describe the past, not the surviving raw edges.

## Considered options

- Rebuild the shadow from the temporal globals: the index would have two sources and could not be purely
  engine-maintained.
- Take temporal edges out of the Adjacency Index: breaks the documented visibility in `MatchEdges` and centrality.
- Never remove the row: changes the tested delete behaviour.
