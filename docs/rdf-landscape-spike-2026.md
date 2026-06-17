# RDF/SPARQL/OWL Landscape Technical Spike — June 2026

## 1. RDF 1.2 / RDF-star Status

### Specification Maturity

W3C RDF & SPARQL Working Group (charter 2025-05-01 through 2027-04-30).

As of April 7, 2026 two specs reached **Candidate Recommendation Snapshot**:

- <https://www.w3.org/TR/2026/CR-rdf12-concepts-20260407/>
- <https://www.w3.org/TR/2026/CR-rdf12-semantics-20260407/>

Remaining syntax specs (Turtle, N-Triples, N-Quads, TriG, RDF/XML) remain at
Working Draft as of Q1 2025. Original Q3 2025 Rec target has slipped ~1 year.

### Key Changes from RDF 1.1

Core construct is the **triple term** `<<( s p o )>>` — a triple-as-value,
never asserted by itself. Final terminology (broke earlier community group
implementations):

- **Triple term**: `<<( s p o )>>` — unasserted
- **Reifier**: an IRI or blank node related via `rdf:reifies`
- **Asserted triple**: conventional triple in the graph
- **Annotation syntax**: `{| p o |}` — sugar that simultaneously asserts and reifies

The separation of asserted vs. unasserted triples is the major divergence from
the 2021 community draft.

### Implementation Status

| Store              | Status                | Notes                                                |
| ------------------ | --------------------- | ---------------------------------------------------- |
| Oxigraph           | 100% conformance      | 34/34 evaluation, 63/63 syntax; Rust; reference impl |
| Apache Jena        | Partial, updating     | Jena 5.x tracking 1.2 draft changes                  |
| GraphDB (Ontotext) | Yes, 9.x+             | SPARQL-star since 9.x                                |
| Stardog            | Yes, PG mode          | Listed in WG implementations                         |
| Eclipse RDF4J      | Yes                   | Listed in WG implementations                         |
| Blazegraph         | Partial, PG mode only | Effectively unmaintained                             |
| Virtuoso           | Not listed            | No conformance report submitted                      |
| Amazon Neptune     | Partial               | Own "edge properties" model; not RDF 1.2 aligned     |
| AllegroGraph       | PG mode, in-progress  | Listed in CG implementations                         |

Authoritative conformance: <https://w3c.github.io/rdf-star/reports/>

---

## 2. SPARQL 1.2 / SPARQL-star Status

All SPARQL 1.2 documents remain at **Working Draft** as of mid-2026:

- SPARQL 1.2 Query Language: WD 2025-12-28 — <https://www.w3.org/TR/2025/WD-sparql12-query-20251228/>
- SPARQL 1.2 Update: WD 2025-08-14
- Protocol, Federated Query, Service Description, results formats: all WD

None have reached Candidate Recommendation. Original Q4 2025 Rec target has
slipped; realistically 2026-2027.

### Key New SPARQL 1.2 Syntax (RDF-star patterns)

```sparql
-- Reifying triple shorthand
SELECT ?person ?authority {
  << ?person :jobTitle "Designer" >> :accordingTo ?authority .
}

-- Named reifier
SELECT ?person ?authority {
  << ?person :jobTitle "Designer" ~ :id >> :accordingTo ?authority .
}

-- Annotation syntax (asserts + reifies simultaneously)
SELECT ?p ?auth ?date {
  ?p :jobTitle ?title {| :accordingTo ?auth; :recorded ?date |} .
}
```

The `TRIPLE()` function constructs triple terms programmatically. Beyond
triple-term support, SPARQL 1.2 is primarily errata fixes.

---

## 3. OWL Reasoning Landscape

### DL Profile Practical Matrix

| Profile    | Reasoners                         | Scale                          | Notes                                               |
| ---------- | --------------------------------- | ------------------------------ | --------------------------------------------------- |
| OWL 2 EL   | ELK                               | Very large TBoxes (10M+ nodes) | SNOMED CT; polynomial                               |
| OWL 2 RL   | RDFox, Jena rules, OWL-RL         | Graph-DB scale                 | Datalog-expressible; materializes into triple store |
| OWL 2 DL   | HermiT, Pellet/Openllet, Konclude | ~100K individuals max          | Tableau-based; impractical for large ABoxes         |
| OWL 2 Full | None (undecidable)                | —                              | Not used in production                              |

### Reasoner Status

**ELK 0.4.x** — Workhorse for large biomedical EL ontologies. Processed
CaLiGraph (10M+ nodes) in ~3.5 hours; HermiT and Pellet timed out at 10K
nodes on same benchmark (ESWC 2023).

**HermiT** — Only reasoner with full OWL 2 DL support. Used in Protégé.
Does not scale beyond ~100K individuals.

**RDFox (Oxford Semantic Technologies)** — The standout gaining enterprise traction:

- In-memory, parallel, incremental Datalog; OWL 2 RL + SWRL + SHACL
- 2-3 million inferences/second
- Confirmed enterprise clients: Samsung, Festo, Aibel
- Available on AWS Marketplace (March 2026)
- Key differentiator: incremental reasoning — data changes trigger
  incremental re-derivation, not full reload

**Relevance to IVG**: IVG's current forward-chaining rules (subClassOf,
subPropertyOf, domain/range, equivalentClass, inverseOf, sameAs,
transitive/symmetric) correspond exactly to **OWL 2 RL / RDFS entailment
regime** — the maximally practical fragment at graph-DB scale.

---

## 4. SHACL vs ShEx

### Adoption (June 2026 Community Survey, arXiv:2606.03502)

SHACL is clearly dominant. ShEx maintains relevant user base but is secondary.

**Top SHACL validators by usage:**

- **PySHACL** — top overall; Python/rdflib; SHACL Core + SPARQL + AF; github: rdflib/pyshacl
- **Apache Jena SHACL** — JVM leader
- **TopBraid EDG** — commercial; most common enterprise authoring
- **rdf-validate-shacl (Zazuko)** — JavaScript; industry-heavy

**SHACL 1.2** Working Draft: <https://www.w3.org/TR/2025/WD-shacl12-core-20251117/>
Adds node expressions, result annotation customization, Turtle output for reports.

**xpSHACL** (VLDB 2025 Workshop) — explainable SHACL violations via LLM+RAG.

**Who wins where:**

- Enterprise: SHACL (TopBraid EDG)
- Python/data engineering: PySHACL
- Healthcare/HL7: ShEx still relevant for FHIR R5 shapes; SHACL growing
- Government: SHACL (DCAT-AP validation, EU Interoperability Test Bed)

---

## 5. Neo4j Neosemantics (n10s)

**n10s 5.20.x** (June 2024) for Neo4j 5.x. Enterprise only — not on Aura.

Feature set:

- Import/export: Turtle, N-Triples, JSON-LD, RDF/XML, TriG, N-Quads, Turtle-star, TriG-star
- Namespace management and URI shortening
- OWL/SKOS/RDFS ontology import
- SHACL validation compiled to Cypher (`n10s.validation.shacl.viewCypher()`)
- Semantic similarity: path similarity, Leacock-Chodorow, Wu-Palmer
- Inference procedures: `n10s.inference.labels()`, `n10s.inference.class_[outgoing|incoming]_rels`,
  `n10s.inference.rel_[source|target]_classes`

**What n10s provides that pure RDF stores don't:**

1. Cypher-over-RDF: imported RDF becomes queryable with Cypher pattern matching
2. Dual model: native PG data and imported RDF coexist, queryable with one language
3. On-demand RDF export: any Cypher result streams out as RDF via `n10s.rdf.export.cypher`
4. SHACL-as-Cypher: validation runs through Neo4j's native query engine

---

## 6. Agentic / LLM-Era Developments

### GraphRAG

**Microsoft GraphRAG** (Edge et al., arXiv:2404.16130, April 2024):
LLM extracts entity-relationship graph → Leiden community detection →
LLM-generated community summaries → summaries augment context at query time.
GitHub: `microsoft/graphrag` v2.7.0 (October 2025). Key advantage: "global
sensemaking" questions vs. vanilla RAG.

### Text2SPARQL vs Text2Cypher — Cypher is Winning

**CypherBench** (arXiv:2412.18702, December 2024): 11 large-scale PGs, 7.8M
entities, 10K+ questions. GPT-4o: 60.18% execution accuracy. Explicitly argues
Cypher is more LLM-friendly than SPARQL.

**SynthCypher** (arXiv:2412.12612, December 2024): 29.8K text2Cypher instances;
fine-tuning LLaMA-3.1-8B on SynthCypher: 40% improvement on text2Cypher.

**Practical signal: if IVG adds SPARQL, the LLM NL-query integration story
gets harder, not easier.**

### Ontology-Aware Agents

**"Representing Agentic Tools in KGs for Structure-Aware Tool Discovery"**
(OpenReview 2025):

- Lightweight OWL ontology for MCP tools: classes `Server`, `Tool`,
  `Capability`, `Parameter`; `hasRequiredInput` vs `hasInput` as first-class
- RDF KG built from real MCP server schemas, queried with SPARQL for filtering
- Benchmark: MCP-Atlas (258 tasks, 269 tools)
- Key finding: KG-augmented tool discovery most valuable under tool overload

### MCP + RDF / Provenance

**PROV-AGENT** (arXiv:2508.02866, August 2025, ORNL): Extends W3C PROV-O with
MCP concepts; captures AI agent interactions as provenance; cross-facility
evaluation (edge, cloud, HPC). Closest thing to a published MCP+RDF integration.

**PROV-STAR** (FOIS 2024, University of Twente): Extends PROV-O for RDF-star/
SPARQL-star; intercepts SPARQL/Update queries as provenance middleware; tracks
changes at triple level; restores KG state via single SPARQL query. Directly
relevant to IVG's temporal edge work.

### Emerging Standards

**LinkML** — YAML schema format generating JSON Schema, SQL, RDF, OWL, SHACL,
Python from one source. Biolink Model is built in LinkML. GitHub: linkml/linkml.

**KGX (Knowledge Graph eXchange)** — serialization standard + Python CLI for
Biolink Model-compliant KGs. Supports Neo4j, RDF stores, TSV, JSON, OBOGraph,
SSSOM. GitHub: biolink/kgx. Standard in biomedical informatics.

**Biolink Model** — high-level schema connecting biological concepts; used by
NCATS Translator, ROBOKOP, SPOKE, RTX-KG2. The domain ontology standard KGX
serializes.

---

## 7. Practical Gaps (What Practitioners Actually Need)

### FHIR/Healthcare

FHIR RDF is specified but not deployed: HAPI FHIR, Vonk, and other production
FHIR servers have not implemented it. Clinical informatics teams wanting to link
FHIR data with other RDF datasets are blocked. (Still true 2025.)

FHIR RDF usability pain: BNodes for literal values, awkward extension
representation, long predicate names. R5 ShEx validation revealed multiple spec
issues (Issues 70, 114-121 in HL7 FHIR RDF issue tracker).

**FHIR-Hopper** (OpenReview 2025) — neuro-symbolic agent for clinical QA —
shows LLMs struggle with flattened FHIR JSON because graph structure is
destroyed. Graph-native FHIR retrieval requires a graph DB layer.

### Biomedical KGs

No graph metadata standards (equivalent to MIAME for genomic datasets). KGX/
Biolink fragmentation: RDF-native stores (SPARQL, no Biolink) vs. PG stores
(Biolink-compliant via KGX, no SPARQL). No single system handles both.

### Enterprise KGs

Open World Assumption impedance: SPARQL's OWA produces unexpected empty results.
Teams building operational apps consistently prefer Cypher's CWA.

No cross-model querying in Neptune: RDF+SPARQL and PG+Cypher engines cannot
query across the boundary. Hard operational limitation.

LLM-generated SPARQL quality: substantially lower accuracy than Cypher.

SHACL performance at scale: validating multi-million-triple graphs is slow in
all current tools. Top unmet need per 2026 community survey.

Neptune schema constraints: none beyond ID uniqueness. Any SHACL, type checking,
or cardinality enforcement must be done at the application layer.

---

## 8. The Neptune Gap — "Node-Level Semantic Query Support"

Five distinct capabilities Neo4j + n10s has that Neptune lacks:

**1. Multiple labels per node**
Neptune's PG model supports single labels per node. Neo4j supports multiple
labels. Cypher multi-label intersection queries (`(n:Person:Employee)`) have no
clean equivalent in Neptune's Cypher layer.

**2. OWL/RDFS inference accessible inside Cypher**
n10s provides `n10s.inference.labels()` etc. — semantically inferred labels and
relationships enumerable inside Cypher queries. Neptune has no equivalent. You
cannot write a Neptune Cypher query that returns all instances of class X and
all subclasses of X without pre-materializing the inference externally.

**3. Single query surface over RDF and PG data**
Neptune explicitly states: "If you've loaded RDF data into Neptune using SPARQL,
your querying options are more limited. In this case, you can only query your
data using SPARQL." Neo4j + n10s provides a single Cypher interface over both
native PG data and imported RDF/OWL content.

**4. APOC procedures**
Neptune does not support APOC. APOC includes procedures for dynamic ontology
traversal, path finding with semantic constraints, and schema operations used for
semantic query patterns.

**5. The fundamental model difference**
RDF's atomic unit is the triple; Neo4j's atomic unit is the node or relationship.
A node exists independently of its relationships in Neo4j. "Node-level semantic
query support" is semantic predicates (class membership, inferred label,
inherited property type) as first-class node pattern constraints in Cypher.
SPARQL must express all entity semantics as triple patterns.

This is not about GQL. It is specifically about Cypher-over-semantics via n10s
that Neptune's architecture cannot replicate because its two engines don't share
a query layer.

---

## IVG Gap Analysis

| Capability                               | IVG Status | Notes                                                |
| ---------------------------------------- | ---------- | ---------------------------------------------------- |
| RDF 1.2 triple terms / annotation syntax | Partial    | Qualifiers JSON exists; not `rdf:reifies` vocabulary |
| SPARQL 1.1/1.2                           | Missing    | Largest interop gap; but LLM trend favors Cypher     |
| RDF export (Turtle, N-Quads, JSON-LD)    | Missing    | n10s `rdf.export.cypher` is the pattern              |
| OWL 2 RL forward-chaining                | Present    | Matches practical production maximum                 |
| SHACL Core validation                    | Missing    | PySHACL + rdflib is the Python-native path           |
| Multi-label nodes + inference in Cypher  | Present    | IVG matches n10s inference story                     |
| PROV-O / temporal provenance             | Partial    | Temporal edges exist; not PROV-O vocabulary          |
| KGX/Biolink export                       | Missing    | Relevant for FHIR/biomedical audiences               |
