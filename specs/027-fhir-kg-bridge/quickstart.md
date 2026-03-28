# Quickstart: FHIR-to-KG Bridge Layer

**Feature**: 027-fhir-kg-bridge

## Setup

### 1. Load the ICD-10→MeSH crosswalk

Download UMLS Metathesaurus from https://www.nlm.nih.gov/research/umls/ (requires free UMLS license).

```bash
python scripts/ingest/load_umls_bridges.py --mrconso /path/to/MRCONSO.RRF
```

Output: `~50,000+ mappings loaded into Graph_KG.fhir_bridges`

### 2. Query anchors for a patient's diagnoses

```python
from iris_vector_graph.engine import IRISGraphEngine

engine = IRISGraphEngine(conn)

# Patient has pneumonia (J18.9) and Type 2 diabetes (E11.9)
anchors = engine.get_kg_anchors(icd_codes=["J18.9", "E11.9"])
# → ["MeSH:D011014", "MeSH:D003924"]  (only nodes present in BEL KG)
```

### 3. Walk the knowledge graph from those anchors

```python
from iris_vector_graph.operators import IRISGraphOperators

ops = IRISGraphOperators(conn)

# PPR from patient's disease anchors
ranked = ops.kg_PAGERANK(seed_entities=anchors, damping=0.85)
# → [("MeSH:D011014", 0.42), ("HGNC:IL6", 0.18), ("MeSH:D016207", 0.12), ...]

# Literature retrieval using top KG nodes
papers = ops.kg_KNN_VEC(label="PubMedArticle", property_name="embedding", query_vector=top_node_embedding, k=5)
```

### 4. Run the full unified pipeline (demo script)

```bash
python scripts/demo/unified_pipeline.py "ARDS COVID dexamethasone"
```

Output:
```
Step 1: FHIR search → 5 discharge summaries (52ms)
Step 2: Extract ICD codes → [J80, U07.1, H36.81] → anchors [MeSH:D012128, MeSH:D000086382]
Step 3: PPR walk → 23 ranked KG nodes (62ms)
Step 4: Literature retrieval → 5 papers (50ms)
Total: 164ms

Results:
  1. PMID:33718228 — "Dexamethasone in COVID-19 ARDS: mechanism via IL-6 pathway" (score: 0.94)
  2. PMID:32678530 — "Cytokine storm modulation..." (score: 0.87)
```

## Bridge Types (extensible)

| bridge_type | Source | Target | Status |
|-------------|--------|--------|--------|
| `icd10_to_mesh` | ICD-10-CM codes | MeSH descriptors | Phase 1 |
| `drug_to_chembl` | NDC/RxNorm codes | ChEMBL compound IDs | Future |
| `gene_to_hgnc` | Gene symbols | HGNC gene IDs | Future |
