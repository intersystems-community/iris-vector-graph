# Subgraph Isomorphism, GNNs, and Cybersecurity Threat Hunting Research

## Files

- **gnn-subgraph-isomorphism-cybersecurity.md** — Main research brief (8000 words, peer-reviewed sources, 10 sections)
- **gnn-subgraph-isomorphism-cybersecurity.provenance.md** — Source tracking and verification log
- **.plans/gnn-subgraph-cybersecurity.md** — Original research plan

## Key Takeaways

### 1. Kumo AI's k-hop + GNN Approach
- **Partially approximates** subgraph isomorphism via learned neighborhood scoring
- Reduces search steps by 98.2% vs VF2, but sacrifices completeness guarantee (may miss rare patterns)
- **Not equivalent** to exact isomorphism (which is NP-complete), but learns practical representations

### 2. Exact vs Approximate in Practice
- **Exact matching** (VF2, Cypher, SIGMA): Used for known APT signatures where precision is mandatory
- **Approximate (GNN embeddings)**: Used for anomaly detection and novel threat discovery where speed is critical
- **Production reality**: Real systems use BOTH in layers

### 3. Real Threat-Hunting Systems
| System | Approach | Focus |
|--------|----------|-------|
| HERCULE (IBM 2016) | Exact graph community detection | Multi-stage APT reconstruction |
| THREATRACE (2021) | GNN embeddings on provenance | Novel lateral movement detection |
| CyberGFM (Jan 2026) | Transformer foundation models | Unsupervised link prediction (2× AP improvement) |
| ProGQL/ProHunter (2025-26) | Hybrid (exact queries + learned mining) | Complete APT investigation |

### 4. Neo4j GDS
- **Does NOT have** native VF2 subgraph isomorphism
- **Does have** Cypher exact path patterns + GDS embeddings for hybrid scoring
- Can implement threat hunting via: exact Cypher queries → GDS anomaly scoring → filter high-risk

### 5. Graph Motifs
- Formal subset of subgraph enumeration (motifs = frequent recurring subgraphs)
- FANMOD/NetMODE for exact enumeration
- Emerging work (2025-26) uses HMMs for temporal attack motif discovery

## For IRIS-Based Threat Hunting

**Recommended 3-layer architecture:**
```
Layer 1: Exact rule matching (Cypher on iris-vector-graph RDF)
  ↓
Layer 2: Learned embeddings (k-hop GNN via intersystems-irispython)
  ↓
Layer 3: Pattern mining (motif enumeration for APT classification)
```

Choose by use case:
- Known APT → Layer 1 (high precision)
- Novel threats → Layers 2-3 (high speed, learns patterns)
- Post-breach → All layers (complete story)

## Sources

10 peer-reviewed papers + official documentation:
- Cordella et al. 2004 (VF2 algorithm)
- Jüttner & Fey 2018 (VF2++ improvements)
- Yang et al. 2025 (GNN-based exact matching)
- Ying et al. 2025 (Neural Graph Navigation)
- King et al. 2026 (CyberGFM)
- Pei et al. 2016 (HERCULE)
- SIGMA specification v2.1.0 (2025)
- Kumo AI research papers
- Neo4j GDS documentation

