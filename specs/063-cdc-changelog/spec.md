# Spec 063: CDC Changelog

**Branch**: `063-cdc-changelog`
**Created**: 2026-04-19

## Overview

Opt-in change data capture for iris-vector-graph. When `cdc=True`, every write operation appends to `^IVG.CDC`, enabling change replay, audit trails, and the snapshot+replay pattern used by mindwalk/opsreview for multi-user collaboration.

## Clarifications

### Session 2026-04-19
- Q: Always-on vs opt-in? → A: Opt-in via `IRISGraphEngine(conn, cdc=True)` — doubles write cost, most users don't need it
- Q: Global name? → A: `^IVG.CDC` (library-scoped, not mindwalk-specific)
- Q: What goes in the CDC entry? → A: `^IVG.CDC(ts, seq) = $LB(op, src, pred, dst, graph_id, qualifiers_json)` where op is one of: CREATE_EDGE, DELETE_EDGE, CREATE_EDGE_TEMPORAL, BULK_EDGE, IMPORT_RDF
- Q: Sequence within same timestamp? → A: `seq` is an auto-increment counter per timestamp via `$Increment(^IVG.CDC.seq)` — handles concurrent writes
- Q: Replay semantics? → A: Idempotent — replaying CREATE_EDGE uses WHERE NOT EXISTS, DELETE_EDGE is non-fatal if edge doesn't exist
- Q: Timestamp resolution? → A: **Epoch milliseconds** (`int(time.time() * 1000)`) — `get_changes_since(ts)` accepts millis; finer resolution, fewer seq collisions
- Q: Does replay write CDC entries on target? → A: **No by default**. `replay_changes(entries, record_replay=False)` is silent. Pass `record_replay=True` to write `REPLAY_CREATE_EDGE` / `REPLAY_DELETE_EDGE` entries (distinguishable from originals) on the target engine.

## User Scenarios & Testing

### User Story 1 — Track who added what (P1)

```python
engine = IRISGraphEngine(conn, cdc=True)
engine.create_edge("Drug:X", "TREATS", "Disease:Y")
changes = engine.get_changes_since(ts_before)
# [{"ts": 1234567890, "op": "CREATE_EDGE", "src": "Drug:X", "pred": "TREATS", "dst": "Disease:Y"}]
```

**E2E test**: create edge with cdc=True; `get_changes_since(0)` returns entry; cdc=False engine has no entries.

### User Story 2 — Replay changes on fresh graph (P1)

```python
# Production: track Dirk's additions
engine_prod = IRISGraphEngine(prod_conn, cdc=True)
dirk_changes = engine_prod.get_changes_since(dirk_login_ts)

# Staging: apply same changes to fresh NCIT snapshot
engine_staging = IRISGraphEngine(staging_conn, cdc=True)
engine_staging.replay_changes(dirk_changes)
```

**E2E test**: create 3 edges with cdc; export changes; restore_snapshot + replay; assert all 3 edges present.

### User Story 3 — CDC with snapshot (P2)

```python
engine.save_snapshot("ncit_base.ivg")
# ... Dirk makes changes ...
engine.restore_snapshot("ncit_base.ivg")
engine.replay_changes(dirk_changes)
```

### Edge Cases
- cdc=False (default): zero performance impact, no writes to ^IVG.CDC
- bulk_create_edges: one CDC entry per edge (not per batch) — granular replay
- import_rdf: one CDC entry per inserted edge with op=IMPORT_RDF
- `clear_changelog(before_ts=None)`: kills entire ^IVG.CDC if no ts, else kills subscripts < before_ts

## Requirements

- **FR-001**: `IRISGraphEngine(conn, cdc=True)` enables CDC; default is `cdc=False`
- **FR-002**: With cdc=True, every successful `create_edge` MUST append `^IVG.CDC(ts, seq) = $LB("CREATE_EDGE", s, p, o, graph_id, "")`
- **FR-003**: With cdc=True, every successful `delete_edge` MUST append with op="DELETE_EDGE"
- **FR-004**: With cdc=True, `create_edge_temporal`, `bulk_create_edges`, `import_rdf` MUST all append entries
- **FR-005**: `engine.get_changes_since(ts) -> List[dict]` where `ts` is epoch milliseconds; returns all CDC entries with timestamp >= ts as list of dicts with keys: `ts`, `seq`, `op`, `src`, `pred`, `dst`, `graph_id`
- **FR-006**: `engine.replay_changes(entries, record_replay=False)` replays a list of change dicts, idempotent; when `record_replay=True` writes `REPLAY_CREATE_EDGE`/`REPLAY_DELETE_EDGE` entries to `^IVG.CDC` on the target engine
- **FR-007**: `engine.clear_changelog(before_ts=None)` deletes CDC entries
- **FR-008**: CDC writes MUST be non-fatal — if ^IVG.CDC write fails, the primary operation still succeeds with a debug log
- **FR-009**: `engine.cdc` property returns True/False

## Success Criteria
- **SC-001**: 5 consecutive create_edge calls with cdc=True → `get_changes_since(0)` returns exactly 5 entries
- **SC-002**: Replay on a fresh graph produces identical rdf_edges rows
- **SC-003**: cdc=False (default) — no entries, no performance regression on existing 531 tests

## IRIS Tier Considerations

CDC itself (`^IVG.CDC` global writes) works on ALL tiers — it uses ObjectScript globals and `$Increment`, not SQL VECTOR or EMBEDDING().

`replay_changes` may need to generate embeddings for new nodes. The approach depends on tier:
- **Community + Advanced Server**: can use `EMBEDDING()` SQL function if IRIS ai-core model is configured (`use_iris_embedding=True`), OR Python `embed_fn`
- **Standard/Advanced**: SQL VECTOR unavailable — use Python `embed_fn` only; IVFFlat/BM25/VecIndex still work (they use `$vectorop` which is available on all tiers)

`replay_changes` return value includes `new_nodes_without_embeddings` — the caller decides how to embed them based on their tier and available models.
