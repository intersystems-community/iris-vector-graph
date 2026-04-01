# Research Plan: GNN Subgraph Isomorphism vs Exact Pattern Matching for Cybersecurity

## Questions
1. **Kumo AI approximation:** Does k-hop sampling + GNN approximate subgraph isomorphism? What are the theoretical guarantees vs. accuracy trade-offs?
2. **Algorithm comparison:** Exact (VF2, exponential-time) vs. approximate (embedding-based, polynomial). When does each apply?
3. **Industry practice:** What do real cybersecurity threat hunters actually use — exact pattern matching (SIGMA, Cypher) or learned embeddings?
4. **DARPA/research systems:** Concrete evidence from CyberGraph, HERCULE, THREATRACE, ProvGraph — which approaches do they employ?
5. **Neo4j GDS:** Does it have native subgraph isomorphism? How does it compare to GNN-based solutions?
6. **Graph motifs:** Definition, relation to subgraph isomorphism, practical threat-hunting equivalence?

## Strategy
- **Dimension 1 (Theory):** Graph algorithms, complexity theory, approximation guarantees
  - Source: papers on VF2, subgraph matching complexity, GNN expressiveness
  - Researcher: lead + theorist
- **Dimension 2 (GNN & Embeddings):** How learned representations approximate structural patterns
  - Source: GNN papers, Graph2Vec, GraphSAINT, Kumo AI tech reports
  - Researcher: researcher-gnn
- **Dimension 3 (DARPA/Industry Systems):** Real threat-hunting systems — implementations, architectures
  - Source: academic papers (CyberGraph, HERCULE, THREATRACE, ProvGraph), GitHub repos, documentation
  - Researcher: researcher-systems
- **Dimension 4 (Neo4j GDS & Practical Tools):** Neo4j, TigerGraph, ArangoDB capabilities + SIGMA/Cypher rules
  - Source: product documentation, benchmarks, SIGMA rule format
  - Researcher: researcher-tools
- **Dimension 5 (Graph Motifs):** Definition, motif-finding algorithms, relation to threat hunting
  - Source: motif papers (Milo et al., FANMOD), cybersecurity applications
  - Researcher: researcher-motifs

## Acceptance Criteria
- [ ] Kumo AI approach explained with evidence (white papers, code, or academic citations)
- [ ] VF2 vs. GNN trade-offs quantified with examples (time complexity, accuracy)
- [ ] At least 2 threat-hunting systems analyzed for exact vs. approximate choices
- [ ] Neo4j GDS capabilities confirmed or denied with specific documentation
- [ ] Graph motifs formally defined and linked to subgraph isomorphism
- [ ] Practical recommendation derived for IRIS-based threat-hunting system

## Task Ledger
| ID | Owner | Task | Status | Output |
|---|---|---|---|---|
| T1 | lead | Theory: VF2, subgraph isomorphism complexity, approximation bounds | todo | gnn-subgraph-theory.md |
| T2 | researcher-gnn | GNN expressiveness, k-hop sampling, Kumo AI approach | todo | gnn-subgraph-gnn-embeddings.md |
| T3 | researcher-systems | DARPA systems: CyberGraph, HERCULE, THREATRACE, ProvGraph | todo | gnn-subgraph-systems.md |
| T4 | researcher-tools | Neo4j GDS, TigerGraph, SIGMA rules, exact vs. approximate in practice | todo | gnn-subgraph-tools.md |
| T5 | researcher-motifs | Graph motif detection, motif-finding algorithms, threat hunting | todo | gnn-subgraph-motifs.md |

## Verification Log
| Claim | Method | Status | Evidence |
|---|---|---|---|
| VF2 is exponential-time, subgraph isomorphism is NP-complete | literature check | pending | Cordella et al. |
| GNN-based matching loses provable correctness guarantees | theory + code inspection | pending | GNN papers + implementation |
| CyberGraph/HERCULE/THREATRACE use embedding-based or exact matching | paper scan | pending | specific systems papers |
| Neo4j GDS lacks native subgraph isomorphism | documentation search | pending | Neo4j docs |
| Graph motifs ⊂ subgraph isomorphism (motifs are frequent subgraphs) | definition check | pending | Milo et al. |

## Decision Log
(Updated as workflow progresses)
