# Production Handoff: IRIS Vector Graph

## Executive Summary

The IRIS Vector Graph system has been enhanced with **"Compound Engineering"** principles, transforming it from a simple graph database into an intelligent memory system. This implementation leverages InterSystems IRIS 2025.1+ to deliver unified vector similarity, graph traversal, and lexical search.

Key improvements include:
-   **Knowledge Compounding**: Integrated support for storing agent memories, successful reasoning strategies, and reflections.
-   **Extreme Performance**: HNSW-optimized vector search (~1.7ms latency) and bounded graph hops (<1ms).
-   **Hybrid Retrieval**: Seamless fusion of vector and graph constraints using IRIS-native stored procedures.

---

## Architectural Decisions

### 1. Unprefixed Schema Strategy
The core graph tables (`nodes`, `rdf_edges`, `rdf_labels`, `rdf_props`, `docs`) use standard SQL names without the `kg_` prefix.
-   **Rationale**: This ensures maximum compatibility with standard SQL tools and simplifies the Cypher-to-SQL translation layer.
-   **Implementation**: Schema is configurable via `IRIS_SCHEMA` (defaults to `User`).

### 2. ObjectScript Primary PPR (Persistent Programming Resources)
The system uses IRIS ObjectScript classes (`iris_src/src/`) for core logic and data persistence, integrated with Embedded Python.
-   **Rationale**: Leveraging IRIS-native persistence provides the best performance and consistency.
-   **Integration**: Python clients interact with these classes via the `iris` module or REST API.

### 3. Bidirectional Performance Optimization
Operations are optimized for both directions of the graph (S->O and O->S) using secondary indexes on `rdf_edges`, ensuring that relationship traversal is always O(1) or O(log N).

---

## Current State of the Test Suite

The system maintains a high bar for quality through automated testing:
-   **Live Database Enforcement**: All integration and E2E tests run against a managed IRIS container via `iris-devtester`.
-   **Test Coverage**: Includes unit tests for Python clients, integration tests for SQL operators, and E2E tests for the GraphQL and openCypher APIs.
-   **Performance Benchmarking**: Integrated performance tests verify that HNSW and graph query latencies remain within production targets.

Refer to `tests/TESTING.md` for detailed instructions on running and extending the test suite.

---

## Next Steps for Ops Team

1.  **Production Hardening**:
    -   Review and adjust the default HNSW parameters (`M`, `efConstruction`) in `sql/schema.sql` based on actual production data distribution.
    -   Configure IRIS resource limits (memory, CPU) to handle projected graph scale.
2.  **Monitoring Integration**:
    -   Expose IRIS performance metrics to your organization's monitoring system (e.g., Prometheus/Grafana).
    -   Set alerts for HNSW index degradation or high query latency.
3.  **Deployment Automation**:
    -   Integrate `scripts/deploy_production.py` into your CI/CD pipeline for automated schema updates and role management.
4.  **Scaling Plan**:
    -   Evaluate the move to ACORN-1 optimized IRIS builds if vector search volume exceeds 10,000 queries per second.
