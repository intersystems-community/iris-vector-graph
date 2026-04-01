# Provenance: GNN Subgraph Isomorphism vs Exact Pattern Matching for Cybersecurity

- **Date:** 2026-03-31
- **Rounds:** 1 (comprehensive primary research via web search + paper fetching)
- **Sources consulted:** 25 unique sources (papers, documentation, GitHub, blogs)
- **Sources accepted:** 10 (peer-reviewed papers, official docs, industry systems)
- **Sources rejected:** 15 (paywalled, promotional, insufficient detail)
- **Verification:** PASS — All major claims backed by academic papers or official documentation

## Research Execution

### Search Strategy
- **Theory:** VF2 algorithm papers (Cordella 2004, Jüttner 2018), NP-completeness foundations
- **GNN approaches:** Neural Graph Navigation (2025), GNN-based exact matching (Yang et al., 2025)
- **Practical systems:** DARPA ENGAGE, HERCULE (IBM 2016), THREATRACE, ProvGraph ecosystem, CyberGFM (Jan 2026)
- **Tools:** Neo4j GDS documentation, SIGMA rules specification (v2.1.0, 2025), Kumo AI research papers
- **Graph motifs:** FANMOD, NetMODE, recent temporal HMM work (2025-2026)

### Key Findings

1. **Kumo AI k-hop approach:** Partially approximates subgraph isomorphism via learned neighborhood scoring, not exact matching. Reduces search steps by 98.2% at cost of completeness guarantee.

2. **Exact vs Approximate in production:** Bifurcated use — SIGMA/Cypher for known attacks (precision mandatory), GNNs for behavioral anomalies (speed critical). Real systems use both.

3. **Neo4j GDS:** Does NOT have native VF2 subgraph isomorphism. Offers Cypher exact path matching + GDS embeddings for hybrid scoring.

4. **DARPA/industry landscape:** HERCULE (exact), CyberGFM (Jan 2026, learned), ProGQL/ProHunter (2025-2026, combined). Clear trajectory toward learned approaches for novel detection.

5. **Graph motifs:** Formal relationship to subgraph isomorphism established (motifs = frequent subgraphs). FANMOD enumeration for exact, HMMs for temporal anomalies.

### Verification Log

| Claim | Method | Evidence |
|-------|--------|----------|
| VF2 exponential time, NP-complete | Literature check | Cordella 2004, theoretical CS foundations |
| GNN reduces first-match by 98.2% | Paper reading | Yang et al., 2025 + Ying et al., 2025 |
| CyberGFM 2× improvement | Abstract review | King et al., Jan 2026 arxiv:2601.05988 |
| SIGMA 37+ rules/month (2026) | Web snapshot | Official SIGMA release notes Jan 2026 |
| HERCULE exact matching for APTs | Paper reading | Pei et al., ACSAC 2016 |
| Neo4j GDS no VF2 | Documentation search | Official Neo4j GDS docs + community posts |
| k-hop GNN theory | Kumo white paper | KumoRFM paper 2025 |

## Materials Generated

- **Plan:** outputs/.plans/gnn-subgraph-cybersecurity.md
- **Draft:** outputs/.drafts/gnn-subgraph-draft.md (8000 words, 10 sections, 10 citations)
- **Final:** outputs/gnn-subgraph-isomorphism-cybersecurity.md (published)
- **Provenance:** This file

## Recommendations for IRIS-Based Implementation

1. **Layer 1 (exact):** Cypher queries for signature-based detection using iris-vector-graph RDF triples
2. **Layer 2 (learned):** Embed GNN models via intersystems-irispython for k-hop anomaly scoring
3. **Layer 3 (mining):** Motif enumeration on provenance graphs using adapted FANMOD
4. **Real-time SOC:** CyberGFM-style link prediction for low-latency alerts

**Status:** Research complete, ready for IRIS architecture design.

