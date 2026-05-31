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

## Load results

| Phase | Result |
|---|---|
| Nodes (97,238, via `bulk_create_nodes`) | ✅ ~2.6 s |
| Edges (5,874,261, via `bulk_create_edges`) | ✅ loads cleanly; throughput notes below |
| `engine.sync()` (^KG + ^NKG build) | recorded on completion |

### Throughput finding (actionable)

`bulk_create_edges(disable_indexes=True)` — the **default** — drops and rebuilds
the `rdf_edges` index **per batch**. Rebuild cost is O(current table size), so
per-batch throughput **degrades as the table grows**: ~7.5K edges/s at 100K
loaded, decaying toward ~2.3K edges/s past 3M loaded. For multi-million-edge
loads this is the dominant cost.

**Recommended pattern for large loads** (now the default in `scripts/load_drkg.py`):
pass `disable_indexes=False` on every batch (keep indexes live, pay incremental
index maintenance) **or** bracket the whole load in a single
disable→load-all→rebuild cycle. Either avoids the O(N) per-batch rebuild.

> v2.0.0 docs note: for >1M-edge ingestion, do not rely on the per-call
> `disable_indexes=True` default — it is tuned for small/medium batches. Use a
> single disable/rebuild bracket around the full load.

## Algorithm timings (97K nodes / 5.87M edges)

> _Filled in on full-load completion. Methodology: 3 runs, median; `top_k=20`;
> betweenness `sample_size=500`; via the Python engine API against `ivg-iris`._

| Algorithm | API call | Median time |
|---|---|---|
| Degree centrality | `engine.degree_centrality(direction='both')` | _pending_ |
| Betweenness (sampled) | `engine.betweenness_centrality(sample_size=500)` | _pending_ |
| Closeness (harmonic) | `engine.closeness_centrality()` | _pending_ |
| Eigenvector | `engine.eigenvector_centrality()` | _pending_ |
| Leiden (tiered) | `engine.leiden_communities()` | _pending_ |
| Triangle count | `engine.triangle_count()` | _pending_ |
| SCC | `engine.strongly_connected_components()` | _pending_ |
| K-core | `engine.k_core_decomposition()` | _pending_ |

## Honest scope statement for v2.0.0

- **Validated**: IVG loads and indexes a real 97K-node / 5.87M-edge biomedical KG
  and runs the full graph-analytics suite on it (timings above).
- **Known limitation**: the default bulk-edge path's per-batch index rebuild does
  not scale to millions of edges — use the disable-once pattern (documented).
- **Not yet validated**: graphs beyond DRKG scale (e.g. PrimeKG ~8M edges,
  full-corpus literature graphs). DRKG is a representative biomedical KG, not the
  upper bound.
