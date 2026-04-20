# Plan: Graph Snapshot and Restore (spec 064)

**Branch**: `064-global-snapshot` | **Date**: 2026-04-19

## Summary

Two components:
1. **`Graph.KG.Snapshot` ObjectScript class** — handles GOF global export/import server-side, writes to `/tmp/ivg_snapshot/`
2. **Python engine methods** — `save_snapshot`, `restore_snapshot`, `snapshot_info` (staticmethod) — orchestrate SQL NDJSON + global GOF into a `.ivg` ZIP file

## Constitution Check
- [x] E2E tests fail before implementation, pass after
- [x] SKIP_IRIS_TESTS defaults "false"
- [x] `snapshot_info` is `@staticmethod` — no connection needed
- [x] Zero `iris-devtester` dependency in core methods

## Architecture

```
save_snapshot(path):
  1. Dump SQL tables → NDJSON strings in Python
  2. Call Graph.KG.Snapshot.ExportGlobals("/tmp/ivg_snap/", globalListJson)
     → writes KG.gof, BM25Idx.gof, etc. to /tmp/ivg_snap/ inside IRIS
  3. Read GOF files back via IRIS stream or file transfer
  4. ZIP everything into path.ivg with metadata.json

restore_snapshot(path, merge=False):
  1. Read metadata.json from ZIP
  2. If not merge: TRUNCATE all SQL tables, Kill all globals
  3. Insert SQL rows from NDJSON (nodes first, then rdf_edges, etc.)
  4. Call Graph.KG.Snapshot.ImportGlobals("/tmp/ivg_snap/")
  5. kg_NodeEmbeddings: import with TO_VECTOR() for emb column
```

## File Transfer Pattern

IRIS can write/read files at paths it has access to. The Python engine:
1. Calls `ExportGlobals("/tmp/ivg_snap/", ...)` via classMethodVoid
2. Reads GOF file content via `classMethodValue("Graph.KG.Snapshot", "ReadFile", path)` which returns file content as string — or uses `%Library.File.ReadTextFile`
3. This avoids needing `docker cp` — works in any deployment where Python can call IRIS

For large GOF files, stream in chunks via `ReadFileChunk(path, offset, chunkSize)`.

## Files Changed

```
iris_src/src/Graph/KG/Snapshot.cls   — NEW ObjectScript class
iris_vector_graph/engine.py          — ADD save_snapshot, restore_snapshot, @staticmethod snapshot_info, embed_fn param, use_iris_embedding param
tests/unit/test_snapshot.py          — NEW E2E tests
```

## ObjectScript Snapshot Class Design

```objectscript
Class Graph.KG.Snapshot Extends %RegisteredObject {
  ClassMethod ExportGlobals(outputDir, globalListJson) As %String
    // Creates outputDir, exports each global as globalName.gof
    // Returns JSON {"files": [...], "sizes": [...]}

  ClassMethod ImportGlobals(inputDir) As %String
    // Imports all .gof files from inputDir
    // Returns JSON {"imported": N}

  ClassMethod ReadFile(filePath) As %String
    // Returns file content as string (for small files < 1MB)
    // Returns "" on error

  ClassMethod WriteFile(filePath, content)
    // Writes content string to filePath

  ClassMethod DeleteDir(dirPath)
    // Cleanup after transfer
}
```

## embed_fn / use_iris_embedding Design

```python
engine = IRISGraphEngine(
    conn,
    embed_fn=None,               # (str) -> List[float], works all tiers
    use_iris_embedding=False,    # True: EMBEDDING() SQL, Community+AdvancedServer only
    embedding_dimension=768,
)
```

After `restore_snapshot`, if `embed_fn` or `use_iris_embedding` is set, the engine auto-embeds any node in `nodes` that has no row in `kg_NodeEmbeddings`. Called once after restore, not per-node.
