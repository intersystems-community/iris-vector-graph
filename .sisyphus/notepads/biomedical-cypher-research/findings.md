# Biomedical Cypher Query Patterns — Research Findings

## Research Date
March 31, 2026

## Sources Reviewed
1. **HetioNet v1.0** — Neo4j database with 47,031 nodes (11 types) and 2.25M edges (24 types)
2. **SPOKE** (Scalable Precision Medicine Open Knowledge Engine) — 27M nodes, 53M edges, 21 node types
3. **Project Rephetio** — Drug repurposing metapath analysis
4. **Neo4j Graph Algorithms Library** (APOC, GDS)
5. **K-Paths framework** — Path reasoning for drug repurposing (arXiv:2502.13344, Feb 2025)
6. **Published papers**: Himmelstein et al. (2017), Morris et al. (2023), recent drug discovery KG surveys (2025)

---

## 1. COMMON CYPHER PATTERNS BEYOND BASIC MATCH/WHERE/RETURN

### 1.1 Metapath Queries (Multi-hop, Typed Relationships)
**Pattern**: Multi-relationship paths with semantic meaning in biomedical context.

**Example — Drug-Gene-Disease path** (HetioNet):
```cypher
// Find drugs targeting genes associated with a disease
MATCH path = (d:Drug)-[:TARGETS]-(g:Gene)-[:ASSOCIATES]-(dis:Disease)
WHERE dis.name = 'hypertension'
RETURN path
```

**Example — Extended metapath with gene interactions** (HetioNet, MS genes):
```cypher
// Disease → GWAS Gene → Protein Interaction → Gene → BiologicalProcess
MATCH path = (n0:Disease)-[e1:ASSOCIATES_DaG]-(n1)-[:INTERACTS_GiG]-(n2)-[:PARTICIPATES_GpBP]-(n3:BiologicalProcess)
WHERE n0.name = 'multiple sclerosis'
  AND 'GWAS Catalog' in e1.sources
  AND exists((n0)-[:LOCALIZES_DlA]-()-[:UPREGULATES_AuG]-(n2))
RETURN path
```

**Evidence**: https://think-lab.github.io/d/220/ (Himmelstein, HetioNet Cypher depot, 2016)

### 1.2 Degree-Weighted Path Count (DWPC) — Path Scoring with Node Normalization
**Pattern**: Aggregate paths with degree weighting to reduce bias from hub nodes.

**Core technique**:
```cypher
WITH
  [
    size((n0)-[:RELATIONSHIP]-()),
    size(()-[:RELATIONSHIP]-(n1)),
    size((n1)-[:NEXT_REL]-()),
    ...
  ] AS degrees, path
WITH
  count(path) AS path_count,
  sum(reduce(pdp = 1.0, d in degrees | pdp * d ^ -0.5)) AS DWPC
```

**Full example — GO Process enrichment for migraine**:
```cypher
MATCH path = (n0:Disease)-[:ASSOCIATES_DaG]-(n1)-[:PARTICIPATES_GpBP]-(n2:BiologicalProcess)
WHERE n0.name = 'migraine'
WITH
  [size((n0)-[:ASSOCIATES_DaG]-()),
   size(()-[:ASSOCIATES_DaG]-(n1)),
   size((n1)-[:PARTICIPATES_GpBP]-()),
   size(()-[:PARTICIPATES_GpBP]-(n2))] AS degrees, path, n2
WITH
  n2.identifier AS go_id,
  n2.name AS go_name,
  count(path) AS PC,
  sum(reduce(pdp = 1.0, d in degrees | pdp * d ^ -0.4)) AS DWPC,
  size((n2)-[:PARTICIPATES_GpBP]-()) AS n_genes
WHERE n_genes >= 5 AND PC >= 2
RETURN go_id, go_name, PC, DWPC, n_genes ORDER BY DWPC DESC LIMIT 5
```

**Why used**: Raw path counts bias toward highly-connected nodes; DWPC downweights popular nodes and rewards specificity.

**Evidence**: https://neo4j.graphgists.com/drug-repurposing-by-hetnet-relationship-prediction-a-new-hope/ 
Source: Himmelstein et al. (eLife 2017) "Systematic integration of biomedical knowledge prioritizes drugs for repurposing"

### 1.3 EXISTS Pattern Predicates — Conditional Path Existence
**Pattern**: Filter paths based on whether related patterns exist (not necessarily on the main path).

```cypher
WHERE exists((n0)-[:LOCALIZES_DlA]-()-[:UPREGULATES_AuG]-(n2))
```

This checks if the disease localizes to anatomy that upregulates the intermediate gene — without including those nodes in the main result.

**Another example — Drug-side effect relationship**:
```cypher
WHERE exists((n1:Compound)-[:CAUSES]-(n3:SideEffect)-[:CAUSES]-(n2:Compound))
  AND n1 <> n2
```

**Evidence**: Used throughout HetioNet queries; now standard Cypher 5.0+ syntax.

### 1.4 OPTIONAL MATCH with Cardinality Counting
**Pattern**: Left-outer-join style queries; count relationships even when zero.

```cypher
MATCH (n0:Drug), (n2:Disease)
OPTIONAL MATCH paths = (n0:Drug)-[:TARGETS]-(n1:Gene)-[:ASSOCIATES]-(n2:Disease)
RETURN
  n0.name AS drug,
  n2.name AS disease,
  size((n0)-[:TREATS]-(n2)) AS treatment,
  count(paths) AS path_count
ORDER BY path_count DESC, treatment DESC
```

**Use**: Compare drug-disease pairs with and without supporting paths (drug repurposing candidates).

**Evidence**: Neo4j GraphGist - Drug Repurposing (https://neo4j.graphgists.com)

### 1.5 UNION Queries — Multi-Source Integration
**Pattern**: Combine results from different relationship types or node categories.

**Biomedical example** (Clinical genome resource / SPOKE integration):
```cypher
MATCH (c:Compound)-[:BINDS_CbG]-(g:Gene)
WHERE c.name = 'aspirin'
RETURN g.name AS target, 'protein_binding' AS mechanism
UNION
MATCH (c:Compound)-[:UPREGULATES_CuG]-(g:Gene)
WHERE c.name = 'aspirin'
RETURN g.name AS target, 'transcriptional_upregulation' AS mechanism
```

**Use**: Combine multiple types of evidence (protein binding, gene regulation, pathway participation).

### 1.6 COLLECT / UNWIND for Aggregation
**Pattern**: Group multiple results and then expand them.

```cypher
MATCH (d:Disease)-[:ASSOCIATES_DaG]-(g:Gene)-[:PARTICIPATES_GpBP]-(bp:BiologicalProcess)
WITH d, collect({gene: g.name, process: bp.name}) AS gene_processes
WHERE size(gene_processes) > 5
UNWIND gene_processes AS gp
RETURN d.name AS disease, gp.gene, gp.process
```

**Use**: Identify dominant biological processes in disease gene sets.

---

## 2. CYPHER FEATURES MOST USED IN BIOMEDICAL COMMUNITY

### Ranked by frequency in published queries (2016–2026):

1. **Variable-length paths** — `[:TYPE*1..5]` or `[:TYPE*]`
   - Finding multi-hop disease mechanisms
   - Yen's K shortest paths for drug-disease connectivity

2. **Pattern predicates (EXISTS)** — Filtering intermediate relationships
   - Conditional tissue expression
   - Multi-layer network constraints

3. **Degree-weighted aggregation** — `reduce()` for scoring
   - Path significance computation
   - Hub bias correction

4. **WITH clauses for intermediate scoping** — Multi-stage filtering
   - Compute per-path metrics → aggregation → final scoring

5. **APOC procedures** — Especially:
   - `apoc.path.expandConfig()` — Complex traversals with filters
   - `apoc.algo.allShortestPaths()` — K-shortest paths
   - `apoc.index.search()` — Full-text search before traversal

6. **UNION** — Integrating multiple data sources (LINCS, KEGG, STRING, DrugBank)

7. **OPTIONAL MATCH** — Handling incomplete knowledge (common in biomedical graphs)

---

## 3. GRAPH ALGORITHMS COMMONLY APPLIED TO BIOMEDICAL KGs

### 3.1 Shortest Path & All-Shortest-Paths
**Use cases**:
- Drug-disease connectivity (minimum hops)
- Mechanism of action via gene networks
- Patient similarity paths

**Cypher**:
```cypher
MATCH (start:Drug {name: 'Aspirin'}), (end:Disease {name: 'arthritis'})
MATCH path = shortestPath((start)-[*..6]-(end))
RETURN path
```

**Why**: Shortest metapaths often encode primary mechanisms; longer paths = indirect/secondary effects.

### 3.2 K-Shortest Paths (Yen's Algorithm)
**Use cases**:
- Diverse mechanisms (top-K pathways)
- Explainability (show multiple reasoning chains)
- Multi-path drug efficacy scoring

**APOC procedure**:
```cypher
CALL apoc.algo.allShortestPaths(startNode, endNode, 'RELATIONSHIP', 3)
YIELD path
RETURN path
```

**Recent innovation (Feb 2025)**: **K-Paths framework** (arXiv:2502.13344)
- Uses diversity-aware Yen's algorithm
- Generates K diverse (not just K similar-length) shortest paths
- Feeds paths to LLMs for explainable predictions
- Reduces computation by 90% vs. full KG traversal

**Results**: Llama 70B F1 scores:
- Drug repurposing: +6.2 points
- Drug-drug interaction: +8.5 points

**Source**: https://arxiv.org/pdf/2502.13344v2.pdf

### 3.3 Community Detection (Louvain, Label Propagation)
**Use cases**:
- Disease gene modules (subgraph clustering)
- Drug similarity clusters (side effect grouping)
- Tissue-specific network communities

**GDS procedure** (Neo4j Graph Data Science):
```cypher
CALL gds.louvain.stream(graphName, {
  includeIntermediateCommunities: false
})
YIELD nodeId, communityId
RETURN nodeId, communityId
```

**Biomedical example**: Identify genes co-regulated in same disease subtype.

**APOC alternative** (Community Edition):
```cypher
CALL apoc.algo.community(numIterations, null, 'partition', 'RELATIONSHIP', 'OUTGOING', 'weight')
```

### 3.4 Centrality Algorithms (Betweenness, Pagerank, Closeness)
**Use cases**:
- **Betweenness centrality**: Identify "hub" genes / drugs (high influence on pathways)
- **PageRank**: Drug target prioritization (importance in gene network)
- **Closeness**: Genes central to disease mechanism

**GDS Betweenness**:
```cypher
CALL gds.betweenness.stream(graphName)
YIELD nodeId, score
RETURN nodeId, score ORDER BY score DESC
```

**Biomedical application**: Top betweenness nodes in protein-interaction networks often encode known drug targets.

### 3.5 Link Prediction (Similarity Algorithms)
**Biomedical use**: Predict missing drug-disease relationships.

**Common similarity metrics**:
- **Jaccard**: Common neighbors / (union of neighbors)
- **Adamic-Adar**: Weighted common neighbors (favor rare neighbors)
- **Common Neighbors**: Direct overlap in 1-hop neighborhoods

**Cypher example (Jaccard)**:
```cypher
MATCH (d1:Drug)-[:BINDS]-(g1:Gene)
MATCH (d2:Drug)-[:BINDS]-(g2:Gene)
WHERE d1 <> d2
WITH d1, d2, count(DISTINCT g1) AS d1_targets, collect(g2) AS d2_targets
MATCH (d1)-[:BINDS]-(common:Gene) WHERE common IN d2_targets
WITH d1, d2, size(collect(common)) AS intersection, d1_targets, size(d2_targets) AS d2_targets
RETURN d1.name, d2.name, toFloat(intersection) / (d1_targets + d2_targets - intersection) AS jaccard_similarity
ORDER BY jaccard_similarity DESC
```

**Newer approach**: Graph embeddings (TransE, DistMul, RotatE) trained on KG triples, then use embedding similarity for link prediction.

### 3.6 Metapath-Based Reasoning
**Pattern**: Learn which metapath types predict relationships (e.g., drug treatment).

**Principle**:
- Count path types between known drug-disease treatment pairs
- Identify which metapath types occur more in true treatments vs. random pairs
- Use top-scoring metapaths for new predictions

**Example metapaths** (HetioNet):
- `DrugTargetsGeneCausesDisease` — Direct target mechanism
- `DrugRegulatesGeneInteractsGeneAssociatesDisease` — Network neighborhood
- `DrugCausesSideEffectCausesCompoundTreatsDisease` — Side effect similarity

**Scoring**: Use DWPC (degree-weighted path count) to rank path instances.

**Source**: Himmelstein et al. (eLife 2017); Rephetio project

---

## 4. SPECIFIC QUERY PATTERNS FOR BIOMEDICAL APPLICATIONS

### 4.1 Drug Repurposing — Finding New Indications
**Goal**: Predict drugs that could treat disease X (beyond known treatment).

**Approach**: Find drugs whose target genes / side-effect profiles match disease:

```cypher
// Pattern 1: Via target genes associated with disease
MATCH path = (drug:Compound)-[:BINDS_CbG]-(gene:Gene)-[:ASSOCIATES_DaG]-(disease:Disease)
WHERE disease.name = 'Alzheimer disease'
  AND NOT (drug)-[:TREATS]-(disease)
WITH drug, disease, count(path) AS paths
RETURN drug.name, disease.name, paths
ORDER BY paths DESC

// Pattern 2: Via side effect similarity
MATCH (known_drug:Compound)-[:TREATS]-(target_disease:Disease)
MATCH (known_drug)-[:CAUSES]-(se:SideEffect)-[:CAUSES]-(candidate_drug:Compound)
WHERE target_disease.name = 'Alzheimer disease'
  AND NOT (candidate_drug)-[:TREATS]-(target_disease)
RETURN candidate_drug.name, target_disease.name
```

**Evidence**: Neo4j GraphGist - Rephetio; SPOKE neighborhood explorer.

### 4.2 Disease Mechanism Analysis — Tissue-Specific Gene Networks
**Goal**: Identify biological processes driving disease in relevant tissue.

```cypher
MATCH path = (disease:Disease)-[e1:ASSOCIATES_DaG]-(gene1:Gene)
WHERE disease.name = 'multiple sclerosis'
  AND 'GWAS Catalog' in e1.sources
WITH disease, gene1
MATCH (gene1)-[:INTERACTS_GiG]-(gene2:Gene)
WHERE exists((disease)-[:LOCALIZES_DlA]-()-[:UPREGULATES_AuG]-(gene2))
MATCH (gene2)-[:PARTICIPATES_GpBP]-(process:BiologicalProcess)
RETURN DISTINCT process.name
ORDER BY process.name
```

**Why tissue-specific**: General gene interactions are noisy; filtering by disease-affected tissue + upregulation removes false positives.

**Evidence**: HetioNet query depot; Thinklab discussion d/220

### 4.3 Patient Similarity — Genomic / Phenotypic Paths
**Goal**: Find similar patients for cohort studies / personalized medicine.

```cypher
MATCH (p1:Patient)-[:HAS_VARIANT]-(variant:Variant)-[:AFFECTS_GENE]-(gene1:Gene)
MATCH (p2:Patient)-[:HAS_VARIANT]-(variant2:Variant)-[:AFFECTS_GENE]-(gene1)
WHERE p1 <> p2
WITH p1, p2, collect(DISTINCT gene1.name) AS shared_genes
WHERE size(shared_genes) > 5
RETURN p1.id, p2.id, size(shared_genes) AS similarity_score
ORDER BY similarity_score DESC
```

(Note: Clinical genome resource / SPOKE variants not yet fully deployed in public instances; pattern from publications.)

### 4.4 Drug-Target Network — Finding Off-Targets (Safety Concern)
**Goal**: Identify unintended protein targets (could cause side effects).

```cypher
MATCH path = (drug:Compound)-[:BINDS_CbG]-(gene:Gene)-[:PARTICIPATES_GpBP]-(process:BiologicalProcess)
WHERE drug.name = 'Aspirin'
MATCH (process)-[:PARTICIPATES_GpBP]-(other_gene:Gene)
WHERE other_gene <> gene
MATCH (other_gene)-[:ASSOCIATES_DaG]-(side_effect_disease:Disease)
RETURN drug.name, gene.name, process.name, other_gene.name, side_effect_disease.name
```

---

## 5. CONCRETE EXAMPLES FROM MAJOR BIOMEDICAL KGs

### 5.1 HetioNet (Neo4j.het.io)
**Graph**: 47K nodes, 2.25M edges, 11 node types, 24 edge types

**Sample queries from published results**:

1. **GO Process enrichment for migraine** (Himmelstein, 2016):
   - Query: Disease → Gene(GWAS) → Gene(interaction) → BiologicalProcess
   - Result: Identified serotonin signaling, neuromuscular processes as top pathways
   - Time: < 5 seconds

2. **Side effect target discovery**:
   - Query: SideEffect → Compound → Gene (DWPC weighted)
   - Result: Identified NR3C1 (glucocorticoid receptor) as cause of Cushingoid
   - Confirmed in 2012 study as top predicted target

**Source**: https://think-lab.github.io/d/220/

### 5.2 SPOKE (spoke.ucsf.edu)
**Graph**: 27M nodes, 53M edges, 21 node types, 55 edge types, from 41 databases

**Query interface**: REST API (not direct Cypher, but supports pathway queries)

**Sample applications**:
- Drug repurposing (compound → protein → disease)
- Gene function annotation (gene → pathway → function)
- Synthetic lethality prediction
- Clinical trial matching

**Notable paper**: Morris et al., Bioinformatics 2023 — SPOKE metagraph design; real-world applications.

### 5.3 K-Paths Framework (2025)
**Algorithm**: Diversity-aware Yen's K-shortest paths + LLM reasoning

**Example (drug repurposing)**:
```
Query: Connect Drug(imatinib) → Disease(acute lymphoblastic leukemia)
K-Paths output: [
  Path 1: imatinib → targets ABL1 → associated-with ALL (known),
  Path 2: imatinib → targets SRC → upregulates Notch pathway → contributes-to ALL,
  Path 3: imatinib → causes immune_activation → SideEffect → known effective for ALL
]
LLM: "Imatinib treats ALL via tyrosine kinase inhibition (ABL1, SRC)"
```

**Performance**: 90% reduction in KG size while maintaining prediction accuracy.

**Source**: https://arxiv.org/abs/2502.13344

---

## 6. APOC PROCEDURES MOST USED IN BIOMEDICAL QUERIES

| APOC Procedure | Use | Biomedical Example |
|---|---|---|
| `apoc.path.expandConfig()` | Complex traversal with filters | Expand from gene, following only disease-relevant relationships |
| `apoc.algo.allShortestPaths()` | K-shortest paths | Find top 5 mechanisms connecting drug to disease |
| `apoc.algo.dijkstra()` | Weighted shortest path | Path cost = inverse confidence score |
| `apoc.algo.betweenness()` | Centrality | Identify hub genes in protein network |
| `apoc.index.search()` | Full-text pre-filter | Find disease by fuzzy name match, then traverse |
| `apoc.path.subgraphAll()` | Subgraph extraction | Extract all paths between drug-disease pair |
| `apoc.algo.community()` | Label propagation | Detect disease gene modules |

---

## 7. CHALLENGES & GOTCHAS

1. **Hub bias**: Highly-connected genes dominate path counts → use DWPC weighting
2. **Incomplete knowledge**: Missing edges are common (genes not yet studied) → use EXISTS cautiously
3. **Relationship directionality**: Some edges are directed (Gene→Disease) vs undirected (Protein-Protein Interaction) → must respect
4. **Performance**: Variable-length paths can explode (O(n^k)) → limit path length, filter early
5. **Data quality**: Older biomedical KGs have redundancy / conflicting sources → edge properties (source, confidence) essential
6. **Scalability**: GDS/Graph Algorithms require in-memory graph projection; large KGs need Enterprise Neo4j or batching

---

## 8. FUTURE DIRECTIONS (2026+)

1. **LLM integration**: Generate Cypher from natural language (Text-to-Cypher) — emerging field
2. **Temporal edges**: Adding time dimension (drug efficacy changes with resistance)
3. **Probabilistic queries**: Bayesian inference over graph
4. **Federated queries**: Query across SPOKE, HetioNet, ClinicalGenomeResource simultaneously
5. **Semantic constraints**: Express biomedical ontologies (GO, DO, SNOMED-CT) as Cypher patterns

---

## REFERENCES

1. Himmelstein, D. S., et al. (2017). "Systematic integration of biomedical knowledge prioritizes drugs for repurposing." eLife 6:e26726.
   - HetioNet v1.0; metapath-based drug repurposing; eLife publication.

2. Morris, J. H., et al. (2023). "The scalable precision medicine open knowledge engine (SPOKE): a massive knowledge graph of biomedical information." Bioinformatics 39(2):btad080.
   - SPOKE graph; 27M nodes; 41 data sources.

3. arXiv:2502.13344 (Feb 2025). "K-Paths: Reasoning over Graph Paths for Drug Repurposing and Drug Interaction Prediction."
   - Diversity-aware Yen's algorithm; LLM integration; 90% KG reduction.

4. Neo4j GraphGists — Drug Repurposing (Himmelstein, 2016)
   - https://neo4j.graphgists.com/drug-repurposing-by-hetnet-relationship-prediction-a-new-hope/

5. Thinklab Cypher Query Depot (Himmelstein, 2016)
   - https://think-lab.github.io/d/220/
   - HetioNet Cypher examples; GO enrichment; MS gene networks.

6. K-Paths arxiv (full paper)
   - https://www.arxiv.org/pdf/2502.13344v2.pdf

7. Neo4j APOC Documentation
   - https://neo4j-contrib.github.io/neo4j-apoc-procedures/

