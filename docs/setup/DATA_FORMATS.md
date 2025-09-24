# Supported Data Formats for IRIS Graph-AI

## Overview

IRIS Graph-AI focuses on the **core biomedical data formats** that represent 80% of real-world usage, ensuring robust ingestion without format proliferation complexity.

## Priority 1: Essential Formats (Implement First)

### 1. **Tab-Separated Values (TSV)** ⭐⭐⭐
**Why Essential**: Universal standard, human-readable, tool-agnostic
```tsv
# Relationships (edges.tsv)
source	predicate	target	confidence	evidence_type	pubmed_id
GENE:BRCA1	encodes	PROTEIN:BRCA1	0.99	experimental	12345678
PROTEIN:BRCA1	interacts_with	PROTEIN:TP53	0.85	computational	23456789

# Entities (nodes.tsv)
entity_id	type	name	description
GENE:BRCA1	gene	BRCA1	Breast cancer gene 1
PROTEIN:BRCA1	protein	BRCA1 protein	Tumor suppressor protein
```

**Biomedical Usage**:
- **STRING database**: Protein interactions
- **Gene Ontology**: Term relationships
- **DrugBank**: Drug-target interactions
- **Custom research**: Laboratory datasets

### 2. **JSON Lines** ⭐⭐⭐
**Why Essential**: API standard, rich metadata, streaming-friendly
```jsonl
{"source": "GENE:BRCA1", "predicate": "encodes", "target": "PROTEIN:BRCA1", "confidence": 0.99, "evidence": {"type": "experimental", "pubmed": "12345678", "method": "western_blot"}}
{"source": "PROTEIN:BRCA1", "predicate": "interacts_with", "target": "PROTEIN:TP53", "confidence": 0.85, "evidence": {"type": "computational", "pubmed": "23456789", "score": 0.92}}
```

**Biomedical Usage**:
- **PubMed APIs**: Literature data
- **NCBI datasets**: Genomic annotations
- **ChEMBL**: Bioactivity data
- **Modern pipelines**: Streaming ingestion

### 3. **RDF Turtle (TTL)** ⭐⭐
**Why Important**: Semantic web standard, ontology integration
```turtle
@prefix gene: <http://example.org/gene/> .
@prefix protein: <http://example.org/protein/> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .

gene:BRCA1 rdf:type gene:Gene ;
           gene:encodes protein:BRCA1 ;
           gene:name "BRCA1" .

protein:BRCA1 protein:interacts_with protein:TP53 ;
              protein:confidence "0.85"^^xsd:decimal .
```

**Biomedical Usage**:
- **Gene Ontology**: Official distribution format
- **UniProt**: Protein annotations
- **ChEBI**: Chemical ontology
- **FAIR data**: Semantic interoperability

## Priority 2: Common Formats (Implement Second)

### 4. **GraphML** ⭐⭐
**Why Useful**: Cytoscape standard, visualization tool format
```xml
<graphml>
  <key id="confidence" for="edge" attr.name="confidence" attr.type="double"/>
  <key id="type" for="node" attr.name="type" attr.type="string"/>
  <graph id="protein_network">
    <node id="GENE:BRCA1">
      <data key="type">gene</data>
    </node>
    <edge source="GENE:BRCA1" target="PROTEIN:BRCA1">
      <data key="confidence">0.99</data>
    </edge>
  </graph>
</graphml>
```

**Biomedical Usage**:
- **Cytoscape**: Network visualization exports
- **NetworkX**: Python graph analysis
- **Gephi**: Network analysis tool
- **Research publications**: Supplementary data

### 5. **CSV with Headers** ⭐⭐
**Why Practical**: Excel compatibility, non-technical user friendly
```csv
source,predicate,target,confidence,evidence_type,pubmed_id
GENE:BRCA1,encodes,PROTEIN:BRCA1,0.99,experimental,12345678
PROTEIN:BRCA1,interacts_with,PROTEIN:TP53,0.85,computational,23456789
```

**Biomedical Usage**:
- **Excel exports**: Laboratory data
- **R/Bioconductor**: Statistical analysis
- **Simple pipelines**: Basic data exchange
- **Manual curation**: Researcher-generated data

## Formats to Avoid (Too Niche)

### ❌ **GML** (Graph Modeling Language)
- **Why Skip**: Limited biomedical adoption
- **Alternative**: GraphML serves same use cases better

### ❌ **DOT/Graphviz**
- **Why Skip**: Primarily visualization, not data exchange
- **Alternative**: Generate DOT for visualization, don't ingest it

### ❌ **GEXF** (Graph Exchange XML Format)
- **Why Skip**: Gephi-specific, limited biomedical usage
- **Alternative**: GraphML for XML-based exchange

### ❌ **Pajek NET**
- **Why Skip**: Academic tool specific
- **Alternative**: TSV for simple edge lists

### ❌ **Custom Binary Formats**
- **Why Skip**: Tool-specific, not interoperable
- **Alternative**: JSON for rich metadata, TSV for simple data

## Implementation Strategy

### Phase 1: Core Ingestion (Week 1-2)
```python
# Priority implementation order
formats = [
    'tsv',        # Most common biomedical format
    'jsonl',      # API and streaming standard
    'csv'         # Excel compatibility
]
```

### Phase 2: Advanced Support (Week 3-4)
```python
# Secondary formats for broader compatibility
advanced_formats = [
    'ttl',        # Semantic web integration
    'graphml'     # Visualization tool compatibility
]
```

## Format-Specific Optimizations

### 1. **TSV Optimization**
```python
def ingest_tsv(file_path, batch_size=10000):
    """
    Optimized TSV ingestion with:
    - Streaming processing for large files
    - Automatic type detection
    - Batch insertions for performance
    - Memory-efficient parsing
    """
    pass
```

### 2. **JSON Lines Streaming**
```python
def ingest_jsonl(file_path, schema_validation=True):
    """
    Streaming JSONL ingestion with:
    - Schema validation against biomedical standards
    - Incremental processing
    - Error handling and recovery
    - Progress tracking
    """
    pass
```

### 3. **RDF Turtle Processing**
```python
def ingest_turtle(file_path, namespace_mapping=None):
    """
    RDF Turtle ingestion with:
    - Namespace resolution
    - Ontology integration
    - SPARQL-compatible output
    - Semantic validation
    """
    pass
```

## Biomedical-Specific Considerations

### 1. **Identifier Mapping**
```python
# Handle common biomedical identifiers
identifier_patterns = {
    'ensembl': r'ENSG\d{11}',
    'uniprot': r'[A-Z][0-9][A-Z0-9]{3}[0-9]',
    'pubmed': r'PMID:\d+',
    'go_term': r'GO:\d{7}',
    'chebi': r'CHEBI:\d+',
    'mesh': r'D\d{6}'
}
```

### 2. **Evidence Integration**
```python
# Standard evidence handling across formats
evidence_schema = {
    'type': ['experimental', 'computational', 'literature'],
    'confidence': 'float[0,1]',
    'source': 'pubmed_id|doi|database',
    'method': 'experimental_method|algorithm_name',
    'date': 'iso_date'
}
```

### 3. **Quality Validation**
```python
# Biomedical data quality checks
quality_checks = [
    'identifier_format_validation',
    'relationship_type_validation',
    'confidence_range_validation',
    'evidence_completeness_check',
    'circular_reference_detection'
]
```

## Integration Examples

### 1. **STRING Database Integration**
```bash
# Download and convert STRING interactions
wget https://stringdb-static.org/download/protein.links.v11.5/9606.protein.links.v11.5.txt.gz
python scripts/converters/string_to_tsv.py --species 9606 --confidence 400
```

### 2. **Gene Ontology Integration**
```bash
# Download and convert GO annotations
wget http://geneontology.org/gene-associations/goa_human.gaf.gz
python scripts/converters/gaf_to_jsonl.py --evidence experimental
```

### 3. **PubMed Literature Integration**
```bash
# Process PubMed abstracts
python scripts/converters/pubmed_to_jsonl.py --query "cancer AND protein" --max_results 10000
```

## Performance Characteristics

### Ingestion Speed by Format

| Format | File Size | Processing Speed | Memory Usage | Best Use |
|--------|-----------|------------------|--------------|----------|
| **TSV** | 1GB | 50MB/s | 256MB | Large static datasets |
| **CSV** | 1GB | 45MB/s | 256MB | Excel compatibility |
| **JSONL** | 1GB | 35MB/s | 512MB | Rich metadata |
| **TTL** | 1GB | 25MB/s | 1GB | Ontology integration |
| **GraphML** | 1GB | 20MB/s | 2GB | Visualization tools |

### Recommended Format Selection

| Use Case | Primary Format | Secondary Format | Reason |
|----------|---------------|------------------|--------|
| **Protein interactions** | TSV | JSONL | Performance + metadata |
| **Literature mining** | JSONL | TSV | Rich annotations |
| **Ontology integration** | TTL | JSONL | Semantic standards |
| **Laboratory data** | CSV | TSV | Excel compatibility |
| **Visualization** | GraphML | TSV | Tool compatibility |

## Conversion Utilities

### Built-in Converters
```bash
# Format conversion utilities
python scripts/converters/csv_to_tsv.py input.csv output.tsv
python scripts/converters/graphml_to_jsonl.py network.graphml edges.jsonl
python scripts/converters/ttl_to_tsv.py ontology.ttl relationships.tsv
```

### Validation Tools
```bash
# Format validation before ingestion
python scripts/validators/validate_tsv.py --schema biomedical edges.tsv
python scripts/validators/validate_jsonl.py --biomedical-ids annotations.jsonl
```

## Conclusion

**Recommendation**: Implement the **top 3 formats (TSV, JSONL, CSV)** first to cover 80% of biomedical use cases, then add TTL and GraphML for specialized needs. This focused approach ensures:

1. **Broad compatibility** with existing biomedical tools
2. **High performance** through format-optimized ingestion
3. **Manageable complexity** without format proliferation
4. **Future flexibility** for emerging standards

The 80/20 rule applies perfectly here - these 5 formats will handle virtually all real-world biomedical graph ingestion scenarios while keeping the codebase maintainable.