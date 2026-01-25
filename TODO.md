# IRIS Vector Graph - TODO

**Last Updated**: 2026-01-25

## P0: Immediate Priorities

### Documentation
- [ ] Update README.md with current architecture
- [ ] Add query engine comparison examples (same query in Cypher/GraphQL/SQL)
- [ ] Document demo applications setup

### Cleanup
- [ ] Remove obsolete directories (`iris/`, `python/`, `biomedical/`)
- [ ] Consolidate duplicate ObjectScript files
- [ ] Clean up root-level test files

## P1: Query Engine Enhancements

### openCypher
- [ ] **Parser upgrade**: Integrate libcypher-parser for full Cypher syntax
- [ ] **Variable-length paths**: Support `*1..3` syntax with recursive CTEs
- [ ] **Query caching**: Cache AST-to-SQL translations
- [ ] **CALL procedures**: `db.index.vector.queryNodes()`, `db.stats.graph()`

### GraphQL
- [ ] **Subscriptions**: WebSocket real-time updates
- [ ] **Query complexity limits**: Depth-based DoS protection
- [ ] **Resolver caching**: TTL-based with manual invalidation

### SQL
- [ ] **Table-valued functions**: ObjectScript class implementation
- [ ] **iFind index automation**: Script for index creation

## P2: Performance & Scale

### Testing
- [ ] Million-entity scale testing
- [ ] Concurrent user load testing (100+ users)
- [ ] Memory profiling at scale

### Optimization
- [ ] Multiple embedding dimensions (384, 768, 1536)
- [ ] HNSW parameter tuning (M, efConstruction)
- [ ] Query plan analysis and optimization

## P3: Features

### Analytics
- [ ] PageRank and centrality measures
- [ ] Community detection algorithms
- [ ] Temporal graph analysis

### Integration
- [ ] Real-time data streaming
- [ ] Graph visualization UI
- [ ] External embedding model support

## P4: Production Deployment

### Security
- [ ] SSL/TLS configuration
- [ ] API authentication/authorization
- [ ] Rate limiting

### Operations
- [ ] Monitoring and alerting
- [ ] Backup/restore procedures
- [ ] High availability setup

## Completed

### 2026-01-25
- [x] Fix connection exhaustion in GraphQL API
- [x] Add `owns_connection` flag for test compatibility
- [x] Merge `008-demo-ux-e2e-tests` to main
- [x] 59 E2E + integration tests passing

### Previous
- [x] NodePK schema with FK constraints
- [x] GraphQL API with DataLoader batching
- [x] openCypher API with Cypher-to-SQL translation
- [x] HNSW vector search optimization (1.7ms)
- [x] Hybrid search with RRF fusion
- [x] Demo applications (biomedical, fraud)
- [x] E2E test infrastructure
