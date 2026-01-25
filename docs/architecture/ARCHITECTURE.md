# IRIS Graph-AI Architecture

## Overview

The IRIS Graph-AI system is built entirely within InterSystems IRIS, leveraging ACORN-1 optimization for exceptional performance in biomedical knowledge graph applications.

## Core Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Client Layer                         │
├─────────────────────────────────────────────────────────┤
│  REST API       │  GraphQL API    │  Python Scripts    │
│  (%CSP.REST)    │  (Legacy)       │  (Performance)     │
├─────────────────────────────────────────────────────────┤
│                 Business Logic Layer                    │
├─────────────────────────────────────────────────────────┤
│  Embedded Python │  ObjectScript   │  SQL Procedures   │
│  (PyOps.cls)     │  (Service.cls)  │  (operators.sql)  │
├─────────────────────────────────────────────────────────┤
│                   Data Layer                           │
├─────────────────────────────────────────────────────────┤
│  RDF Tables      │  Vector Store   │  Text Search      │
│  (rdf_*)         │  (HNSW+ACORN-1) │  (iFind)          │
├─────────────────────────────────────────────────────────┤
│              InterSystems IRIS Database                 │
└─────────────────────────────────────────────────────────┘
```

## Data Model

### Graph Structure (RDF-Style)
```sql
-- Entity classification
rdf_labels(s VARCHAR(256), label VARCHAR(128))

-- Entity properties
rdf_props(s VARCHAR(256), key VARCHAR(128), val VARCHAR(4000))

-- Relationships
rdf_edges(edge_id BIGINT, s VARCHAR(256), p VARCHAR(128),
          o_id VARCHAR(256), qualifiers VARCHAR(4000))
```

### Vector Storage
```sql
-- High-performance vector embeddings
kg_NodeEmbeddings(node_id INT, id VARCHAR(256),
                  emb VECTOR(DOUBLE, 768))

-- HNSW index with ACORN-1 optimization
CREATE INDEX kg_NodeEmbeddings_HNSW ON kg_NodeEmbeddings(emb)
AS HNSW(M=16, efConstruction=200, Distance='COSINE')
OPTIONS {"ACORN-1":1}
```

### Text Search
```sql
-- Document storage for hybrid search
kg_Documents(doc_id INT, node_id INT, txt VARCHAR(1000000))
```

## Key Components

### 1. IRIS-Native REST API (`%CSP.REST`)
- **Path**: `/kg/*` endpoints
- **Methods**: POST vectorSearch, hybridSearch, metaPath
- **Format**: JSON request/response
- **Performance**: Direct IRIS processing, no external app server

### 2. Embedded Python Operations
- **File**: `iris_src/src/Graph/KG/PyOps.cls`
- **Functions**: Vector operations, graph traversal, data processing
- **Benefits**: In-database computation, optimal memory usage

### 3. HNSW Vector Index with ACORN-1
- **Dimensions**: 768 (OpenAI text-embedding-ada-002 compatible)
- **Algorithm**: Hierarchical Navigable Small World
- **Optimization**: ACORN-1 for 2,278x faster index building
- **Distance**: Cosine similarity

### 4. Performance Testing Framework
- **STRING Database**: Real biomedical protein interaction data
- **Scale**: 10,000+ proteins, 50,000+ interactions
- **Benchmarks**: Latency, throughput, scalability analysis
- **Comparison**: Community Edition vs ACORN-1

## Data Flow

### Vector Search Operation
```
1. Client Request (JSON) → REST Endpoint
2. REST → Embedded Python → Vector Processing
3. Python → SQL: VECTOR_COSINE(emb, TO_VECTOR(?))
4. HNSW Index → ACORN-1 Optimized Search
5. Results → JSON Response
```

### Graph Traversal Operation
```
1. Client Request → REST Endpoint
2. REST → SQL Procedure → Graph Query
3. SQL: SELECT FROM rdf_edges WHERE s = ? AND p = ?
4. Index Lookup → Result Set
5. Results → JSON Response
```

### Hybrid Search Operation
```
1. Vector Search → Top-K Results
2. Text Search → Top-K Results
3. RRF Fusion → Combined Ranking
4. Graph Filters → Final Results
```

## Performance Characteristics

### ACORN-1 Optimizations
- **Index Building**: 2,278x faster than standard IRIS
- **Vector Operations**: Optimized HNSW algorithm
- **Memory Usage**: Efficient vector storage and retrieval
- **Query Performance**: Sub-millisecond graph operations

### Scalability Features
- **Horizontal**: Multiple IRIS instances with sharding
- **Vertical**: Large memory allocation for vector indexes
- **Concurrent**: Multi-user support with connection pooling
- **Data Volume**: Millions of entities and relationships

## Security Architecture

### Authentication & Authorization
- **IRIS Security**: Built-in user management
- **SSL/TLS**: Encrypted connections for production
- **API Security**: Request validation and rate limiting

### Data Protection
- **Validated Inputs**: SQL injection prevention
- **Vector Validation**: Format and dimension checking
- **Access Control**: Database-level permissions

## Deployment Architecture

### Development Environment
```yaml
services:
  iris-community:
    image: intersystemsdc/iris-community:latest
    ports: ["1973:1972", "52773:52773"]
```

### Production Environment (ACORN-1)
```yaml
services:
  iris-acorn:
    image: docker.iscinternal.com/intersystems/iris:2025.3.0EHAT.127.0
    ports: ["1973:1972", "52774:52773"]
    volumes: ["./iris.key:/usr/irissys/mgr/iris.key"]
```

## Integration Points

### External Data Sources
- **STRING Database**: Protein interaction networks
- **PubMed Central**: Scientific literature
- **UniProt**: Protein information
- **Gene Ontology**: Biological classifications

### Client Applications
- **Research Tools**: Biomedical analysis software
- **Web Applications**: Interactive knowledge exploration
- **API Clients**: Programmatic access to graph data
- **Analytics Platforms**: Data science environments

## Monitoring & Observability

### Performance Metrics
- **Latency**: Query response times by operation type
- **Throughput**: Requests per second, entities per second
- **Resource Usage**: Memory, CPU, disk I/O
- **Index Performance**: HNSW search efficiency

### Health Checks
- **Database**: IRIS instance status and connectivity
- **Indexes**: HNSW index integrity and performance
- **Data Quality**: Entity count and relationship consistency
- **API Endpoints**: REST service availability

## Future Architecture Considerations

### Scaling Strategies
- **Read Replicas**: Multiple IRIS instances for query distribution
- **Sharding**: Horizontal partitioning by entity type or domain
- **Caching**: Redis or similar for frequently accessed data
- **CDN**: Static content delivery for documentation/assets

### Advanced Features
- **Real-time Updates**: Streaming data ingestion
- **Graph Analytics**: PageRank, community detection
- **ML Integration**: Model training and inference
- **Multi-tenancy**: Isolated environments per organization