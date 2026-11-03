<!-- markdownlint-disable MD013 MD060 -->

# Draft: adjacency index sync, revised (not filed)

Status: **DRAFT, not filed, kept local.** Revises the request in
`productivity-framework/specs/083-deferred-graph-sync/ivg-enhancement-request.md`
after checking it against this repo (2026-10-05). Host-specific figures are
removed. Nothing here has been sent to `intersystems-community/iris-vector-graph`.

## What the original request got wrong or left out

1. **Its one named reader does not read the index.** `kg_NEIGHBORS` compiles a
   Cypher `MATCH` to SQL on `rdf_edges` (`_engine/algorithms.py:600-625`). The
   readers of the Adjacency Index are the BFS fast path behind single-source
   variable-length Cypher, `kg_PPR`/PageRank, and the Arno paths. The caller's
   variable-length queries (`query_builder.py`, `related_to|depends_on*1..n`) are
   the only consumers found in that repo, and they traverse different predicates
   from the `MENTIONED_IN` edges the ingest flush writes. Whether those edges exist
   on the production instance was not checked (that container is not this
   project's).
2. **An incremental writer already exists.** `bulk_ingest_edges` writes the SQL row
   and the live index in one transaction. Prototype on `ivg-iris-enterprise`
   (scratch namespace, 100k nodes, 225k edges, `^KG` and `^NKG` built):

   | Call (1,000 new edges)               | Time   | `^KG` current | `^NKG` current | BFS sees them |
   | ------------------------------------ | ------ | ------------- | -------------- | ------------- |
   | `bulk_ingest_edges(auto_sync=False)` | 0.48 s | yes           | yes            | yes           |
   | `bulk_create_edges(auto_sync=False)` | 1.29 s | **no**        | no             | no            |
   | ...then `BuildKG` to catch up        | 3.6 s  | yes           | no             | yes           |

   `^NKG` stayed current because its skeleton existed (`WriteAdjacency` maintains it
   only then). The token `^KG("__version")` moved once per edge.

3. **The outage is three steps, not one.** `engine.sync()` is `BuildKG`,
   `BuildNKG` and `Build2HopStats`; `BuildNKG` empties `^NKG` too.
4. **Partial builds are not the cheap win.** At 1/21 of the reported shape
   (100k nodes, 100k labels, 700k props, 225k edges), `BuildKG` took 4.7 s: edges
   3.6 s (77%), props 1.1 s (23%), labels about 0. `BuildNKG` took 2.4 s and
   `Build2HopStats` 0.2 s. Extrapolated linearly to 21.5M rows that is about 155 s
   against 250-290 s reported, so the scaling is not linear and the split may shift
   at full size. Skipping props would save about a quarter of `BuildKG`. Synthetic
   props were short strings; longer values would raise the share.

## New finding

Re-ingesting edges that already exist through `bulk_ingest_edges` is correct
(returns 0, no duplicate rows) but took **39.8 s for 1,000 edges** (about 40 ms
each) on a 225k-edge table, against 0.48 s when new. The per-edge existence
`SELECT` or the node registration ahead of it is the likely cost; not profiled.
A writer that is called again for chunks it already has would pay this.

## Residual asks (if anything is filed)

1. **A rebuild that never shows an empty index.** Build into a shadow subscript and
   swap, or at least keep the old index readable until the new one is complete.
   This also removes the 50,000-edge transaction caveat (`BuildKG` commits its
   `Kill` at the first chunk). Independent of any one caller's workload.
2. **Make `bulk_create_edges(auto_sync=False)` keep the live index**, or document
   loudly that it does not. Today it is the fast SQL-only path and leaves the index
   stale with no signal.
3. **Document which writes maintain the index.** The table in `CONTEXT.md` §
   Adjacency Index is the start: live write (`create_edge`, `bulk_ingest_edges`,
   ledger commits, FHIR sync) versus none (raw SQL, Cypher DML,
   `bulk_create_edges(auto_sync=False)`).
4. **Speed up the duplicate check in `BulkIngestEdgesSQL`.**

Dropped from the original: delta sync (the live writer is the delta), and partial
builds (props are a quarter of `BuildKG`, not most of it).

## Consequence for the caller's spec 083

If the caller switches its edge flush to `bulk_ingest_edges` (mapping
`source_id`/`target_id` to `s`/`o`; that function reads `s`/`p`/`o`, `source`/
`target`, not `source_id`), the index is current after every flush, and the marker
and scheduled rebuild shrink to the writers that bypass it (raw SQL, restore).
Open before that: the 40 ms-per-existing-edge cost on re-ingest, and whether the
production graph is one where a `Build*` outage has any reader at all.
