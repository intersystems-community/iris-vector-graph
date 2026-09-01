# Research: TemporalIndex API Gaps

## Phase 0 — resolved questions and decisions

## SHA1 availability in IRIS 2024.1+

**Decision**: Use `$SYSTEM.Encryption.SHA1Hash(value)` directly.

**Rationale**: Already used in `UpdateHLL` (TemporalIndex.cls:88). The
comment on that line reads "SHA1 hash verified available in IRIS 2025.1"
but the class targets IRIS 2024.1+; the method exists in 2024.1 as well —
`UpdateHLL` would be broken without it. No additional verification needed.

**Alternatives considered**: `%MessageDigest` (more verbose, slower), pure
ObjectScript SHA1 (unacceptable complexity). Both rejected.

## `^KG("labelset")` subscript safety

**Decision**: Use `^KG("labelset", hash)` as the canonical storage subscript.

**Rationale**: The spec confirms opsreview already writes here and IVG does
not. Adopting it as the IVG-owned subscript means opsreview's existing data
is immediately compatible — no migration needed. The `Purge()` method kills
`^KG("tout")`, `^KG("tin")`, `^KG("bucket")`, `^KG("edgeprop")`, `^KG("tagg")`
but not `^KG("labelset")`. Adding `PurgeRawBefore` must also not touch it
(FR-016). `PurgeBefore` must also not touch it (FR-016).

**Alternatives considered**: A separate global `^KGLS` — rejected, adds
global fragmentation. A SQL table — rejected, violates no-schema-change constraint.

## `_call_classmethod` bridge pattern for new methods

**Decision**: Follow the existing pattern in `iris_sql_store.py:723-754` exactly.

**Rationale**: `write_temporal_edge` calls `self._call_classmethod("Graph.KG.TemporalIndex", "InsertEdge", ...)`. New methods follow the same pattern. All parameters are passed as strings; IRIS coerces them. `PurgeRawBefore` returns an integer (edge count) — cast via `int(str(...))`.

**Alternatives considered**: Direct `conn.cursor().execute(...)` with embedded ObjectScript — not supported. A new store abstraction layer — rejected (Principle V: no unnecessary abstractions).

## Python wrapper location

**Decision**: `purge_raw_before`, `intern_label_set`, `resolve_label_set` go in `temporal.py` (TemporalMixin). `create_edge_temporal` and `bulk_create_edges_temporal` updated in place.

**Rationale**: `purge_before` already lives in `temporal.py:141`. `intern_label_set` / `resolve_label_set` are edge-related concerns that belong alongside temporal edge creation. The store-layer methods go in `iris_sql_store.py` and the protocol additions go in `store_protocol.py`, matching the layering of all other temporal methods.

## TSUNIT parameter implementation strategy

**Decision**: Add `Parameter TSUNIT As %String = ""` and a computed
`Parameter BUCKETMS As %Integer = 300000`. All `\ ..#BUCKET` uses become a
ternary: `$Select(..#TSUNIT="ms": ts \ ..#BUCKETMS, 1: ts \ ..#BUCKET)`.

**Rationale**: ObjectScript `Parameter` values are compile-time constants —
`BUCKETMS = BUCKET * 1000` can be derived as a constant. This avoids
runtime multiplication on every insert while keeping the expression readable.

**Alternatives considered**: Runtime method call to get bucket divisor —
rejected (unnecessary overhead on hot path). A separate subclass for ms mode
— rejected (Principle V).

## Bucket-boundary fix in PurgeBefore and PurgeRawBefore

**Decision**: Change `Kill ^KG("tagg", bucket)` to only kill buckets
strictly less than `tsEnd \ BUCKETDIV` (the bucket *containing* tsEnd is
kept). Add a `maxSafeBucket` variable: `maxSafeBucket = (tsEnd \ BUCKETDIV) - 1`.

**Rationale**: Current code kills the bucket containing `tsEnd`, destroying
aggregates for edges newer than `tsEnd` within the same bucket. The fix
only kills buckets `< maxSafeBucket`. This matches the `<` semantics
documented in the spec (FR-005, FR-021).

## Integration test setup

**Decision**: Use `ivg-iris-enterprise` container exclusively (port 31972).
Test file uses `IVG_TEST_CONTAINER=ivg-iris-enterprise` and the existing
`enterprise_iris_connection` fixture pattern from `tests/conftest.py`.

**Rationale**: Per CLAUDE.md memory: NEVER use community container
(port 21972) — MaxServerConn=1 causes license failures. Enterprise container
is the project standard for all integration tests.
