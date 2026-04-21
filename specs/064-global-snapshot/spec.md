# Spec 064: Graph Snapshot and Restore

**Branch**: `064-global-snapshot`
**Created**: 2026-04-19

## Overview

Loading a large knowledge graph (NCIT: 150K concepts, embeddings, BM25 index) takes 10+ minutes. For test fixtures, CI, and snapshot-based workflows, this is prohibitive. This spec adds `engine.save_snapshot(path)` / `engine.restore_snapshot(path)` — a portable, self-contained graph snapshot format that restores in 2-3 seconds.

The snapshot must cover ALL graph state, including the SQL tables that hold embeddings. IRIS HNSW is built on top of `kg_NodeEmbeddings` — leaving that as homework for the IRIS admin defeats the purpose.

## What a Complete Snapshot Contains

| Layer | Content | Format |
|-------|---------|--------|
| **SQL tables** | nodes, rdf_edges, rdf_labels, rdf_props, rdf_reifications, kg_NodeEmbeddings | NDJSON via SELECT |
| **Adjacency globals** | ^KG("out"/"in") | IRIS global export |
| **Index globals** | ^BM25Idx, ^IVF, ^PLAID, ^VecIdx | IRIS global export |
| **CDC globals** | ^IVG.CDC (if present) | IRIS global export |
| **Metadata** | schema version, timestamps, row counts, IRIS version | JSON header |

The combined snapshot is a `.ivg` file — a ZIP archive containing:
- `metadata.json` — header with counts and version
- `sql/nodes.ndjson`, `sql/rdf_edges.ndjson`, etc. — SQL table dumps
- `globals/KG.gof`, `globals/BM25Idx.gof`, etc. — IRIS global export files (GOF format)

## Clarifications

### Session 2026-04-19
- Q: Format for globals? → A: IRIS Global Output Format (GOF) via `^%GO` utility / `%Library.GlobalEdit` — standard IRIS portable global format, supported by all IRIS versions
- Q: How to handle kg_NodeEmbeddings VECTOR column? → A: Export as comma-separated float strings (same format TO_VECTOR accepts on import), not raw binary
- Q: Restore idempotency? → A: Restore is destructive by default — clears target tables/globals before import. Optional `merge=True` for additive restore.
- Q: Test fixture integration? → A: `IRISContainer.save_snapshot(container, path)` and `IRISContainer.restore_snapshot(container, path)` class methods for use in conftest.py — no engine instance needed for the devtester integration
- Q: Snapshot portability — can snapshot from IRIS Community restore on IRIS Enterprise? → A: Yes — GOF globals are portable across editions; SQL NDJSON is edition-agnostic
- Q: How does Python trigger GOF global export? → A: **Option B** — Python calls `_call_classmethod` to invoke `Graph.KG.Snapshot.ExportGlobals(outputDir, globalList)` ObjectScript helper, which writes GOF files to `/tmp/ivg_snapshot/` inside the IRIS container. Python then retrieves them via file transfer (docker cp in test context, or IRIS stream API in production). Import uses `Graph.KG.Snapshot.ImportGlobals(inputDir)` same pattern.
- Q: What does `merge=True` mean for conflicting rows? → A: **Option A** — UPSERT semantics: snapshot rows always overwrite existing rows. Local rows not in the snapshot are preserved. Net result = snapshot state as baseline, local additions on top. Correct for the snapshot+CDC replay pattern.
- Q: Should `snapshot_info` require an engine instance? → A: **No** — `IRISGraphEngine.snapshot_info(path)` is a `@staticmethod`. Opens the ZIP and reads `metadata.json` without any IRIS connection. `save_snapshot` and `restore_snapshot` remain instance methods.

## User Scenarios & Testing

### User Story 1 — Save and restore a graph (P1)

```python
# Save after expensive load
engine.import_rdf("ncit.ttl")
engine.save_snapshot("/data/ncit_v2026.ivg")

# Restore in seconds for testing
engine2 = IRISGraphEngine(fresh_conn)
engine2.restore_snapshot("/data/ncit_v2026.ivg")
# Graph fully restored — nodes, edges, labels, props, embeddings, indexes
```

**E2E test**: load 100 nodes + edges + embeddings → save → drop all tables → restore → assert all counts match.

### User Story 2 — Test fixture (P1)

```python
# conftest.py
@pytest.fixture(scope="session")
def ncit_graph(iris_connection):
    snapshot = "tests/fixtures/ncit_small.ivg"
    engine = IRISGraphEngine(iris_connection)
    if os.path.exists(snapshot):
        engine.restore_snapshot(snapshot)
    else:
        engine.import_rdf("tests/data/ncit_small.ttl", infer="rdfs")
        engine.save_snapshot(snapshot)
    yield engine
```

### User Story 3 — Snapshot + CDC replay (P2)

```python
engine.restore_snapshot("ncit_base.ivg")
engine.replay_changes(dirk_changes)  # from spec 063
```

### Edge Cases
- Snapshot of empty graph → valid file with zero rows, restores to empty graph
- Partial restore (globals only, skip SQL) via `layers=['globals']` parameter
- Snapshot file missing or corrupted → clear FileNotFoundError / ValueError
- VECTOR column in kg_NodeEmbeddings → export as float CSV string, import via TO_VECTOR

## Requirements

- **FR-001**: `engine.save_snapshot(path, layers=None)` saves complete graph state to `.ivg` ZIP file; `layers` defaults to all: `['sql', 'globals']`
- **FR-002**: Snapshot MUST include all SQL tables: nodes, rdf_edges, rdf_labels, rdf_props, rdf_reifications, kg_NodeEmbeddings
- **FR-003**: Snapshot MUST include all index globals: ^KG, ^BM25Idx, ^IVF, ^PLAID, ^VecIdx, ^IVG.CDC
- **FR-004**: `kg_NodeEmbeddings.emb` VECTOR column MUST be exported as comma-separated float string and imported via TO_VECTOR
- **FR-005**: `engine.restore_snapshot(path, merge=False)` restores from `.ivg` file; default destroys existing data first
- **FR-006**: Restore MUST rebuild ^KG adjacency from either globals (fast path, if included) or rdf_edges SQL (fallback via BuildKG)
- **FR-007**: `IRISGraphEngine.snapshot_info(path)` is a `@staticmethod` — returns metadata dict from ZIP without IRIS connection
- **FR-008**: Snapshots are portable — restorable on any IRIS instance with the same namespace, regardless of edition
- **FR-009**: `layers=['sql']` exports only SQL tables (no globals); `layers=['globals']` exports only globals
- **FR-010**: `Graph.KG.Snapshot.ExportGlobals(outputDir, globalListJson)` and `Graph.KG.Snapshot.ImportGlobals(inputDir)` ObjectScript ClassMethods handle GOF I/O server-side; Python retrieves/sends files via file transfer
- **FR-011**: `merge=True` uses UPSERT semantics — snapshot rows overwrite conflicts, local-only rows are preserved

## Success Criteria
- **SC-001**: Restore time for a 150K-node graph with embeddings < 30 seconds (vs 10+ minutes for full reload)
- **SC-002**: Row counts, edge counts, and embedding counts match exactly before and after snapshot cycle
- **SC-003**: `MATCH (n) RETURN count(n)` returns same count after restore as before save
- **SC-004**: Vector search returns same top-k results after restore as before save
- **SC-005**: 531+ existing tests pass with zero regressions

## Dependency Boundary

`save_snapshot()` and `restore_snapshot()` are **core engine methods** with zero dependency on `iris-devtester`. They use only:
- `intersystems-irispython` (already a core dep) for IRIS native API calls
- Python stdlib `zipfile` + `json` for the `.ivg` container format
- IRIS `%Library.GlobalEdit` / `^%GO` for global export/import

`iris-devtester` is a **test-only** library (in `dev` extras). The conftest.py fixture pattern in User Story 2 uses `IRISContainer` for container management — that's acceptable in test code. The snapshot I/O itself does not touch devtester.

This means `save_snapshot` is available to production code (e.g., scheduled backup jobs, deployment scripts) without pulling in test infrastructure.

## IRIS Tier Considerations for Embeddings

### Corrected tier capability table

| Tier | `$vectorop`/`$vector` (ObjectScript) | SQL `VECTOR` type | `EMBEDDING()` | `VECTOR_COSINE` SQL | Notes |
|------|--------------------------------------|------------------|---------------|---------------------|-------|
| Community | ✓ | ✓ | ✓ | ✓ | Free, full stack |
| IRIS Server/Enterprise/Elite/Entree | ✓ | ✗ | ✗ | ✗ | `$vectorop` works, SQL VECTOR does not |
| Enterprise | ✓ | ✓ | ✓ | ✓ | Full stack |

**Key**: `$vectorop` is in ALL tiers — IVFFlat, BM25Index, VecIndex, PLAID all work on IRIS Server/Enterprise/Elite/Entree because they use ObjectScript globals and `$vectorop`, not SQL VECTOR columns.

### What this means for snapshots

- **Community + Advanced Server**: `kg_NodeEmbeddings` exists (SQL VECTOR column) — include in snapshot, restore via `TO_VECTOR` import
- **IRIS Server/Enterprise/Elite/Entree**: `kg_NodeEmbeddings` doesn't exist — skip gracefully; all `$vectorop`-based index globals (^BM25Idx, ^IVF, ^PLAID, ^VecIdx) still valid in snapshot

### Auto-embedding on restore/replay — three paths

```python
engine = IRISGraphEngine(
    conn,
    embed_fn=None,               # Python callable (str) -> List[float] — ALL tiers
    use_iris_embedding=False,    # True: uses EMBEDDING() SQL — Community + Advanced Server only
    embedding_dimension=768,
)
```

Priority order when a new node needs embedding:
1. `use_iris_embedding=True` + Community + Advanced Server → `EMBEDDING(text)` SQL (IRIS-native model)
2. `embed_fn` provided → Python callable (works ALL tiers; stores result in kg_NodeEmbeddings on C/E tiers)
3. Neither → node added to `new_nodes_without_embeddings` in return value

`save_snapshot` records `{"has_vector_sql": true/false, "embedding_dim": 768}` in metadata.json.
`restore_snapshot` warns if snapshot has `has_vector_sql=true` but target instance lacks SQL VECTOR support.
