# Biomedical Datasets for Testing

## Overview

This IRIS Graph-AI system is designed as a **general biomedical knowledge graph platform**. While we've thoroughly tested with STRING protein data, the architecture supports diverse biomedical datasets.

## Tested Datasets

### ✅ STRING Database (Completed)
- **Type**: Protein-protein interactions
- **Scale**: 10,000+ proteins, 50,000+ interactions
- **Results**: 21.7x performance improvement with ACORN-1
- **Use Case**: Network analysis, pathway discovery

## Recommended Additional Datasets

### 🧬 PubMed Central (PMC)
- **Type**: Scientific literature abstracts
- **Scale**: Millions of abstracts with PMID links
- **Vector Use**: Text embeddings for semantic search
- **Graph Use**: Citation networks, author relationships
- **Implementation**: `pmc_scale_test.py` (existing script)

### 🧪 Gene Ontology (GO)
- **Type**: Biological process ontology
- **Scale**: 40,000+ terms, hierarchical relationships
- **Vector Use**: Term embeddings for similarity
- **Graph Use**: IS-A and PART-OF relationships
- **Data Source**: http://geneontology.org/docs/download-ontology/

### 💊 DrugBank
- **Type**: Drug-target interactions
- **Scale**: 13,000+ drugs, 5,000+ targets
- **Vector Use**: Chemical structure embeddings
- **Graph Use**: Drug-target-pathway networks
- **Data Source**: https://go.drugbank.com/

### 🔬 UniProt
- **Type**: Protein sequences and annotations
- **Scale**: 200M+ protein sequences
- **Vector Use**: Sequence embeddings
- **Graph Use**: Protein family relationships
- **Data Source**: https://www.uniprot.org/

### 🧬 ChEMBL
- **Type**: Bioactive compounds and assays
- **Scale**: 2M+ compounds, 1M+ assays
- **Vector Use**: Molecular fingerprints
- **Graph Use**: Compound-target-indication networks
- **Data Source**: https://www.ebi.ac.uk/chembl/

### 🔗 Reactome
- **Type**: Biological pathways
- **Scale**: 2,500+ pathways, 13,000+ reactions
- **Vector Use**: Pathway embeddings
- **Graph Use**: Reaction networks, regulatory cascades
- **Data Source**: https://reactome.org/

## Implementation Recommendations

### 1. PubMed Literature Analysis (High Priority)
```python
# Suggested implementation
def load_pubmed_abstracts(max_abstracts=100000):
    """
    Load PubMed abstracts with:
    - PMID as entity ID
    - Abstract text for vector embeddings
    - Citation links as graph edges
    - MeSH terms as properties
    """
    pass
```

### 2. Gene Ontology Integration (Medium Priority)
```python
def load_gene_ontology():
    """
    Load GO terms with:
    - GO IDs as entities
    - Term definitions for vectors
    - IS-A/PART-OF as relationships
    - Evidence codes as qualifiers
    """
    pass
```

### 3. Drug-Target Networks (Medium Priority)
```python
def load_drugbank_interactions():
    """
    Load drug-target data with:
    - Drug/target IDs as entities
    - Chemical/sequence embeddings
    - Interaction types as relationships
    - Binding affinities as qualifiers
    """
    pass
```

## Performance Testing Strategy

### Scalability Tests by Domain
| Dataset | Entity Count | Relationship Count | Vector Dimensions |
|---------|-------------|-------------------|-------------------|
| **STRING** | 25,000 proteins | 250,000 interactions | 768 |
| **PubMed** | 100,000 abstracts | 500,000 citations | 768 |
| **Gene Ontology** | 40,000 terms | 80,000 relations | 768 |
| **DrugBank** | 18,000 entities | 50,000 interactions | 512 |

### Benchmark Metrics
- **Data ingestion rate** (entities/second)
- **Vector search latency** (ms for top-k)
- **Graph traversal speed** (paths/second)
- **Memory efficiency** (GB per million entities)
- **Index build time** (seconds for full dataset)

## Implementation Priority

### Phase 1: Core Literature Support
1. **PubMed abstracts** - High impact for research
2. **Citation networks** - Graph traversal testing
3. **MeSH term vectors** - Semantic search validation

### Phase 2: Molecular Data
1. **Gene Ontology** - Hierarchical relationship testing
2. **UniProt sequences** - Large-scale entity testing
3. **Protein families** - Complex graph structures

### Phase 3: Drug Discovery
1. **DrugBank** - Multi-modal data (chemical + biological)
2. **ChEMBL assays** - High-dimensional relationship data
3. **Reactome pathways** - Complex pathway networks

## Expected Benefits

### Scientific Research
- **Literature discovery** across biomedical domains
- **Cross-domain connections** (genes → drugs → diseases)
- **Hypothesis generation** through graph traversal
- **Semantic search** for complex biological concepts

### Platform Validation
- **Diverse data types** prove general applicability
- **Different scales** test system limits
- **Various vector dimensions** validate flexibility
- **Complex relationships** stress graph performance

## Integration Scripts

### Suggested Directory Structure
```
scripts/biomedical/
├── pubmed_loader.py      # PubMed abstract ingestion
├── go_loader.py          # Gene Ontology terms
├── drugbank_loader.py    # Drug-target interactions
├── uniprot_loader.py     # Protein sequences
├── chembl_loader.py      # Bioactive compounds
└── benchmark_suite.py    # Cross-dataset performance
```

### Test Data Sources
- **Small samples** (1K-10K entities) for development
- **Medium datasets** (10K-100K entities) for validation
- **Large corpora** (100K+ entities) for production testing
- **Cross-references** between datasets for integration testing

## Conclusion

The IRIS Graph-AI platform's performance with STRING data demonstrates readiness for diverse biomedical applications. Expanding to PubMed literature, Gene Ontology, and drug databases will:

1. **Prove generalizability** beyond protein networks
2. **Test diverse data types** (text, sequences, chemicals)
3. **Validate cross-domain** knowledge integration
4. **Enable real research** applications

This positions the platform as a **comprehensive biomedical knowledge graph solution**, not just a protein interaction system.