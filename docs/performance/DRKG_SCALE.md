# DRKG Biomedical-Scale Validation

**Dataset**: [DRKG — Drug Repurposing Knowledge Graph](https://github.com/gnn4dr/DRKG) (Apache-2.0)
**Scale**: 97,238 nodes (13 biomedical types) · 5,874,261 edges (107 relation types) · 400-dim TransE embeddings
**Container**: `ivg-iris` (IRIS Community 2025.1), MacBook Pro M3 Ultra, 128 GB
**Purpose**: move IVG's biomedical-scale story from *"designed for"* to *"validated at"*.

This is the largest real-world graph IVG has been loaded with end-to-end (prior
validated full-analytics scale was ER(2000); prior traversal/BuildKG scale was
LDBC SF10 ~40K nodes).

## How to reproduce

```bash
# 1. Download + extract DRKG (217 MB) into data/drkg/
curl -L -o data/drkg/drkg.tar.gz \
  https://dgl-data.s3-us-west-2.amazonaws.com/dataset/DRKG/drkg.tar.gz
tar xzf data/drkg/drkg.tar.gz -C data/drkg/

# 2. Load into ivg-iris (nodes + edges; add --embeddings for TransE vectors)
PYTHONPATH=. python scripts/load_drkg.py --embeddings

# 3. Explore via the flagship notebook
jupyter lab docs/notebooks/biomed_drkg_showcase.ipynb
```

## Load results (validated 2026-05-31)

Loaded into `ivg-iris`: **97,238 nodes + 5,499,997 edges** (94% of DRKG; the
background load hit a `<COMMUNICATION LINK ERROR>` EPIPE at 5.5M — see finding 2).
`^KG` adjacency built successfully over the full 5.5M-edge graph.

| Phase | Result |
|---|---|
| Nodes (97,238, `bulk_create_nodes`) | ✅ 2.6 s |
| Edges (5.5M, `bulk_create_edges`) | ✅ loaded; throughput degrades — finding 1 |
| `BuildKG` (^KG adjacency, 5.5M edges) | ✅ **45.9 s** (~120K edges/s) |
| `BuildNKG` (^NKG integer index, 5.5M edges) | ✅ **180.5 s** (after finding 3 fix; → 97,236 nodes / 5.5M edges) |

### Algorithm timing (5.5M-edge graph)

| Algorithm | Path | Median time |
|---|---|---|
| `degree_centrality` | ^KG `$Order` walk | **0.89 s** ✅ |
| `ClosenessGlobal` (harmonic, maxHops=2) | ^NKG server-side ObjectScript | **6.13 s** ✅ |
| `EigenvectorGlobal` | ^NKG server-side | ❌ `<MAXSTRING>` — finding 5 |
| `TriangleCountJson` | ^NKG + ^||ccAdj | CPU-bound >15 min (100% CPU) — finding 6 |
| k-core / scc / leiden | ^NKG + ^||ccAdj | not measured (gated behind triangle on shared CPU) |

`degree_centrality` (single `$Order` pass) and `ClosenessGlobal` (BFS bounded to
maxHops=2) are tractable server-side at 5.5M edges. The full-graph
intersection/iteration algorithms hit two scale limits (findings 5 & 6).

## Findings (the real value of this scale test)

**Finding 1 — bulk-edge load throughput degrades.**
`bulk_create_edges(disable_indexes=True)` (the default) rebuilds the `rdf_edges`
index per batch at O(table size). Throughput decayed ~7.5K → ~2K edges/s as the
table grew past 3M rows. **Fix shipped**: `scripts/load_drkg.py` now passes
`disable_indexes=False`. v2.0.0 docs note: for >1M-edge loads, do not use the
per-call `disable_indexes=True` default — use a single disable/rebuild bracket.

**Finding 2 — connection drops on very long loads.**
The 5.5M-edge load (≈40 min on the slow path) hit `<COMMUNICATION LINK ERROR>`
EPIPE — the IRIS connection dropped during a large operation. Large ingests
should checkpoint/reconnect, or use the faster load (finding 1 fix) so the
connection isn't held open for 40 min.

**Finding 3 — `BuildNKG` hard-required libarno. FIXED this session.**
`BuildNKG` → `Build2HopExactStats` → `NKGAccel.Build2HopExact()` raised
`<DYNAMIC LIBRARY LOAD> .../libarno_callout.so` when the arno accelerator was
absent, aborting the whole `^NKG` build on stock containers. **Fix**: wrapped the
`Build2HopExact()` call in `Try/Catch` (it is an optional arno optimization; the
pure-ObjectScript `^NKG` is already built before it, and a pure-OS 2-hop
fallback follows). Re-validated: `BuildNKG` now completes in 180.5s on 5.5M edges
with no libarno. This unblocks the server-side `^NKG` analytics path on stock
containers.

**Finding 4 — LazyKG fallback does not scale to millions of edges.**
Before finding 3 was fixed (no `^NKG`), triangle/k-core/scc/leiden fell to the
pure-Python LazyKG path, which pulls the full adjacency over the Native API and
did not complete in 8 min at 5.5M edges. With `^NKG` now buildable (finding 3),
these use the server-side ObjectScript path instead — see findings 5 & 6.

**Finding 5 — `EigenvectorGlobal` `<MAXSTRING>` at 97K nodes.**
`EigenvectorGlobal` builds its result JSON in a single ObjectScript string. At
97K nodes the string exceeds IRIS's ~3.6 MB long-string limit → `<MAXSTRING>`.
**Action (candidate spec)**: stream results to a `^||` global or chunk the JSON
(the `StoreLargeOut`/`ReadLargeOutChunk` pattern already used by `KHopNeighbors`),
or always honor `topK` truncation *before* building the string. (The Cypher path
passes `topK`, so it is less exposed; the raw classmethod with large `topK` is.)

**Finding 6 — triangle/community are CPU-bound on dense biomedical hubs.**
`TriangleCountJson` is O(V·d²) over symmetrized neighbors. DRKG has gene hubs
with thousands of edges, so triangle enumeration ran >15 min at 100% CPU on 5.5M
edges without completing. This is the workload the **arno Rust accelerator**
targets — pure ObjectScript triangle/community on dense graphs needs either arno
or a degree-capped/approximate variant for production scale.

## Honest scope statement for v2.0.0

- **Validated**: IVG ingests a real 97K-node / 5.5M-edge biomedical KG, builds
  `^KG` (46s) and `^NKG` (180s) over it, and runs degree centrality in <1s and
  harmonic closeness (2-hop) in ~6s — server-side, no Python client, no arno.
- **Fixed this session**: `BuildNKG` no longer requires libarno (finding 3) —
  the key unblock for stock-container scale.
- **Known limits at 5.5M edges**: `EigenvectorGlobal` `<MAXSTRING>` (finding 5),
  triangle/community CPU-bound without arno (finding 6), bulk-load index strategy
  (finding 1, fixed), connection longevity (finding 2).
- **Honest claim today**: *"IVG loads, indexes (^KG + ^NKG), and runs degree +
  closeness centrality on a 5.5M-edge biomedical KG server-side on a stock
  container. Eigenvector needs a large-result fix (finding 5); triangle/community
  need the arno accelerator for dense-hub graphs (finding 6)."*
- **Not yet validated**: full community/eigenvector timings at 5.5M edges (gated
  on findings 5 & 6); graphs beyond DRKG; arno-accelerated runs at this scale.
