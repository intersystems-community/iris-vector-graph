# Research: IVG Engine Critical Fixes (206)

## A9 — BuildKG Kill ^KG

**Decision**: Replace `Kill ^KG` with five explicit kills.
**Rationale**: `Kill ^KG` destroys all subscripts; only five are rebuilt.
**Alternatives considered**: Saving/restoring temporal globals around the kill —
rejected (copies large data, race-prone). Walking only the adjacency subscripts
to kill — equivalent to the fix but more verbose.

## A12 — OR scan in bulk_delete_nodes

**Decision**: Split every OR DELETE into two sequential single-column DELETEs.
**Rationale**: IRIS SQL cannot use a single index for `WHERE s=? OR o_id=?`
when the two columns are in different indexes. Two scans each using their own
index is O(matches) vs O(table). Confirmed: measured 19 s/batch → <2 s.
**Also split**: The `rdf_reifications` subquery uses the same OR pattern —
split that too for consistency.
**Alternatives considered**: A compound `(s, o_id)` index — rejected (doubles
index size, DDL change, schema owned by users). A UNION of two indexed SELECTs
as subquery — valid but more complex than two DELETEs.

## A8.3 — PurgeBucketRange

**Decision**: Walk `^KG("tagg")` directly by bucket key; kill both `tagg` and
`bucket` subscripts; return count. Half-open semantics rejected in favour of
closed `[bucketStart, bucketEnd]` to match `PurgeBefore` bucket arithmetic and
simplify callers computing "all buckets before month M".
**Rationale**: O(range) walk; raw edges untouched by design; additive API.
**Alternatives considered**: `PurgeBefore` with ts=bucketEnd*BUCKET — rejected
because it also deletes raw edges. A SQL table for aggregates — rejected (not in
scope; would require schema migration).
