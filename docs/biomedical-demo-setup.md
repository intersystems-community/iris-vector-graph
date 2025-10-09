# Biomedical Demo Setup Guide

This guide explains how to set up the interactive biomedical research demo with real protein interaction data from the STRING database.

## Quick Start

### 1. Start IRIS Database

```bash
# Option A: Standard IRIS Community Edition
docker-compose up -d

# Option B: ACORN-1 (pre-release with HNSW optimization - fastest)
docker-compose -f docker-compose.acorn.yml up -d
```

Verify IRIS is running:
```bash
docker ps | grep iris
```

### 2. Load STRING Protein Database

Load 10,000 human proteins with interaction data (~1 minute):

```bash
python scripts/performance/string_db_scale_test.py --max-proteins 10000
```

**What this does:**
- Downloads STRING DB files (protein info, interactions, aliases)
- Parses 10K human proteins with functions and descriptions
- Generates 768-dimensional vector embeddings for similarity search
- Creates ~37K high-confidence protein-protein interactions (score ≥400)
- Loads everything into IRIS with optimized batch inserts
- Builds HNSW vector index for <10ms similarity queries
- Runs performance benchmarks

**Data loaded:**
- 10,000 proteins (from ~20K total human proteins in STRING v12.0)
- 37,258 interactions with confidence scores
- 10,000 vector embeddings (768-dim)
- Full-text search index on protein names/functions

**Performance (on typical hardware):**
- Load time: ~48 seconds
- Graph queries: 0.39ms average
- Text search: 1.20ms average
- Vector search: ~2ms average

### 3. Configure IRIS Connection

Create `.env` file (or use existing):

```bash
IRIS_HOST=localhost
IRIS_PORT=1972          # or 21972 for ACORN-1
IRIS_NAMESPACE=USER
IRIS_USER=_SYSTEM
IRIS_PASSWORD=SYS
```

### 4. Start Demo Server

```bash
# Start server pointing to IRIS (port 8200)
PYTHONPATH=/Users/tdyar/ws/iris-vector-graph/src python -m iris_demo_server.app
```

**Or run in background:**
```bash
nohup env PYTHONPATH=src python -m iris_demo_server.app > demo_server.log 2>&1 &
```

### 5. Access Demo

Open browser to: **http://localhost:8200/bio**

## Demo Features

### Protein Search
- **Vector similarity search**: Find proteins with similar functions
- **Text search**: Search by name, gene symbol, or function description
- **Query types**: name, sequence, function
- Results show: protein ID, name, organism, similarity score

### Interactive Network Visualization
- **D3.js force-directed graph** with organism color coding
- **Node click expansion**: Click any protein to fetch neighbors
- **Zoom, pan, drag controls**
- **Visual encoding**:
  - Node size = connection count (degree)
  - Node color = organism (green for human, purple for mouse)
  - Edge color = interaction type (green=activation, red=inhibition, blue=binding)
  - Edge thickness = confidence score
- **Hover effects**: Highlights connected nodes

### Pathway Analysis
- Find shortest paths between proteins
- Graph traversal with max hops limit
- Confidence scoring for paths
- Interaction evidence from STRING DB

### Pre-configured Scenarios
- **Cancer Protein**: TP53 tumor suppressor network
- **Metabolic Pathway**: Glycolysis enzymes (GAPDH → LDHA)
- **Drug Target**: Kinase inhibitor discovery

## Data Sources

### STRING Database v12.0
- **Source**: https://string-db.org/
- **Organism**: 9606 (Homo sapiens - human)
- **Coverage**: 19,566 total human proteins
- **Interactions**: 11,759,454 protein-protein interactions
- **Evidence types**: Experimental, database, text mining, co-expression, neighborhood, fusion, co-occurrence
- **Confidence scores**: 0-1000 (we filter for ≥400 for high quality)

### Data Download Locations
Downloaded files are cached in: `/Users/tdyar/ws/graph-ai/data/string/`

Files:
- `9606.protein.info.v12.0.txt.gz` (1.9 MB) - Protein metadata
- `9606.protein.links.v12.0.txt.gz` (79.3 MB) - Interactions
- `9606.protein.aliases.v12.0.txt.gz` (18.9 MB) - Gene names/aliases

**Note**: Files are only downloaded once. Subsequent runs use cached files.

## Scaling Options

### Different Dataset Sizes

```bash
# Small test (1K proteins, ~30 seconds)
python scripts/performance/string_db_scale_test.py --max-proteins 1000

# Medium (10K proteins, ~1 minute) - RECOMMENDED
python scripts/performance/string_db_scale_test.py --max-proteins 10000

# Large (50K proteins, ~5 minutes)
python scripts/performance/string_db_scale_test.py --max-proteins 50000

# Full human proteome (20K proteins, ~2 minutes)
python scripts/performance/string_db_scale_test.py --max-proteins 20000
```

### Filter by Confidence Score

```bash
# High confidence only (score ≥700)
python scripts/performance/string_db_scale_test.py --max-proteins 10000 --min-score 700

# All interactions (score ≥150)
python scripts/performance/string_db_scale_test.py --max-proteins 10000 --min-score 150
```

## Architecture

### Backend Stack
- **Database**: InterSystems IRIS 2025.1+ with Vector Search (HNSW)
- **Web Server**: FastHTML (Python server-side rendering)
- **API**: RESTful endpoints with Pydantic validation
- **Search**: Hybrid vector + text + graph queries with RRF fusion

### Frontend Stack
- **UI Framework**: FastHTML components + TailwindCSS
- **Reactivity**: HTMX 2.0 (zero JavaScript for interactions)
- **Visualization**: D3.js v7 force-directed graphs
- **SVG Filters**: Glow effects, gradients

### Data Model
IRIS tables:
- `kg_NodeEmbeddings` - 768-dim vector embeddings with HNSW index
- `kg_Documents` - Protein metadata (name, organism, function)
- `rdf_labels` - Node type labels
- `rdf_props` - Node properties
- `rdf_edges` - Protein-protein interactions with confidence scores

## Performance Benchmarks

### STRING 10K Dataset (macOS, IRIS Docker)
- **Load time**: 48 seconds
- **Ingestion rate**: 307 proteins/sec
- **Graph traversal**: 0.39ms average (500 queries)
- **Text search**: 1.20ms average (50 queries)
- **Vector search**: ~2ms average (100 queries)
- **Hybrid search**: ~5ms average (combining all 3)

### Demo Server Performance
- **Protein search**: <100ms (including vector similarity)
- **Network expansion**: <150ms (1-hop neighbors)
- **Pathway search**: <200ms (2-5 hops)
- **Page load**: <50ms (FastHTML server-side rendering)

All well under the FR-002 requirement of <2 seconds.

## Troubleshooting

### "No results found" for searches
**Problem**: Demo is running in DEMO_MODE with only 5 hardcoded proteins.

**Solution**: Load STRING data and remove DEMO_MODE:
```bash
python scripts/performance/string_db_scale_test.py --max-proteins 10000
# Then restart server without DEMO_MODE=true
PYTHONPATH=src python -m iris_demo_server.app
```

### "Connection refused" errors
**Problem**: IRIS database not running or wrong port.

**Solution**: Check IRIS status and verify .env configuration:
```bash
docker ps | grep iris
cat .env  # Check IRIS_PORT matches docker port mapping
```

### Slow vector searches
**Problem**: HNSW index not built or ACORN not enabled.

**Solution**: Use ACORN-1 build for 100x faster vector search:
```bash
docker-compose -f docker-compose.acorn.yml up -d
```

Or rebuild indexes:
```bash
python scripts/setup_schema.py
```

### Graph not showing in browser
**Problem**: JavaScript errors or missing D3.js library.

**Solution**: Check browser console (F12). The D3.js library is loaded from CDN - check network connectivity.

## Example Queries

### Search for EGFR and Related Proteins
```
Query: "egfr"
Type: name
Top K: 10
```

Expected results: EGFR (Epidermal Growth Factor Receptor), PIK3CA, AKT1, BRAF, etc.

### Find Cancer Pathway
```
Source: ENSP00000269305 (TP53)
Target: ENSP00000344548 (CDKN1A)
Max Hops: 3
```

Expected: TP53 → MDM2 → CDKN1A pathway

### Metabolic Network Expansion
```
Protein: ENSP00000306407 (GAPDH)
Expand Depth: 2
```

Expected: Glycolysis enzymes network

## Next Steps

### Add More Organisms
Edit `string_db_scale_test.py` line 39:
```python
organism: str = "10090"  # Mouse (Mus musculus)
# or
organism: str = "4932"   # Yeast (S. cerevisiae)
```

### Increase Dataset Size
For production demos with full proteome:
```bash
python scripts/performance/string_db_scale_test.py --max-proteins 100000
```

**Warning**: 100K proteins requires:
- ~5 minutes load time
- ~2GB RAM
- Generates ~2M interactions

### Integrate Real Embeddings
Replace synthetic embeddings with real protein embeddings:
1. Use ESM-2 (protein language model) or ProtBERT
2. Generate embeddings from sequences
3. Load into `kg_NodeEmbeddings` table

### Add Custom Data
See `scripts/performance/string_db_scale_test.py` for data loading patterns.

## References

- STRING Database: https://string-db.org/
- IRIS Vector Search: https://docs.intersystems.com/iris/latest/csp/docbook/DocBook.UI.Page.cls
- D3.js Force Layout: https://d3js.org/d3-force
- FastHTML: https://fastht.ml/
- HTMX: https://htmx.org/
