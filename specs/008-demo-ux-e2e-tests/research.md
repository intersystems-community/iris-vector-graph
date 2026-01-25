# Research: E2E UX Tests and Demo Enhancements

**Feature**: 008-demo-ux-e2e-tests  
**Date**: 2025-01-18

## Research Tasks

### 1. E2E Test Infrastructure Patterns

**Question**: What existing E2E test patterns should be followed?

**Finding**: The project has a well-established E2E test infrastructure:
- `tests/e2e/test_multi_query_engine_platform.py` - Comprehensive cross-engine tests
- `tests/conftest.py` - Shared fixtures with `iris_connection` module-scoped fixture
- Uses FastAPI `TestClient` for API testing
- Custom pytest markers: `@pytest.mark.e2e`, `@pytest.mark.requires_database`
- Automatic test data cleanup with fixture-based setup/teardown

**Decision**: Follow existing patterns exactly. Use `iris_connection` fixture, TestClient for API tests, test ID prefixes for cleanup.

**Rationale**: Consistency with existing infrastructure reduces learning curve and maintenance burden.

---

### 2. Fraud Detection Domain Schema

**Question**: What entities should the Fraud Detection domain include?

**Finding**: Based on industry fraud detection patterns:
- **Account**: Financial account with attributes (type, status, creation_date)
- **Transaction**: Money transfer between accounts (amount, timestamp, type)
- **Device**: Device fingerprints used for login/transactions
- **Alert**: Fraud alert with confidence score
- Graph relationships: Account→Transaction, Account→Device, Transaction→Alert

**Decision**: Create minimal viable domain with Account, Transaction, and Alert entities. Device entity deferred to future enhancement.

**Rationale**: Minimum viable demo that showcases graph traversal and pattern detection. Can be expanded later.

**Alternatives Considered**:
- Full AML (Anti-Money Laundering) schema - too complex for demo
- Simple transaction-only schema - insufficient to show graph power

---

### 3. Sample Data for Fraud Demo

**Question**: What sample data patterns demonstrate fraud detection effectively?

**Finding**: Effective fraud demos need:
- Ring topology: Accounts connected in cycles (money laundering pattern)
- Star topology: Single account with many connections (mule account pattern)
- Temporal patterns: Rapid transactions in short timeframes
- Behavioral embeddings: Vector representations for anomaly detection

**Decision**: Create 50-100 accounts with 200-500 transactions forming identifiable patterns. Include pre-computed embeddings for similarity search.

**Rationale**: Small enough for fast loading (<30s) but large enough to demonstrate meaningful patterns.

---

### 4. Demo Script UX Improvements

**Question**: What UX improvements are needed for demo scripts?

**Finding**: Current demo scripts (`demo_working_system.py`, `end_to_end_workflow.py`) have:
- Basic print statements with emoji indicators
- Timing information for some operations
- Basic error handling with stack traces

**Decision**: Implement:
1. Rich/colorful console output with progress bars
2. Structured error messages with "Next Steps" guidance
3. Auto-detection of database state with helpful prompts
4. Consistent timing display format

**Rationale**: Professional demo experience for customer evaluations and conference demos.

**Alternatives Considered**:
- Web-based demo UI - too much scope for this feature
- Jupyter notebooks - less suitable for command-line demos

---

### 5. GraphQL Integration for Fraud Domain

**Question**: How should Fraud types integrate with existing GraphQL infrastructure?

**Finding**: The biomedical domain provides a clear pattern:
- Types in `examples/domains/biomedical/types.py` extend `Node` interface
- Resolvers in `examples/domains/biomedical/resolver.py`
- DataLoaders in `examples/domains/biomedical/loaders.py`
- Registration in `api/gql/schema.py`

**Decision**: Mirror biomedical structure exactly in `examples/domains/fraud/`. Register fraud types alongside biomedical types in schema.

**Rationale**: Consistency enables easy comparison and learning from existing implementation.

---

### 6. Test Performance Requirements

**Question**: How to ensure E2E test suite completes in <60 seconds?

**Finding**: Current test file `test_multi_query_engine_platform.py` has ~15 tests. Key factors:
- Database connection setup: ~1-2s (module-scoped fixture amortizes this)
- Individual test: 50-500ms depending on complexity
- Test data setup/teardown: ~100ms per test

**Decision**: 
- Use module-scoped fixtures where possible
- Batch related tests to share setup
- Target 20-30 biomedical tests, 15-20 fraud tests
- Skip slow vector operations if HNSW unavailable

**Rationale**: Enables CI/CD integration without excessive wait times.

---

## Summary

All research questions resolved. No NEEDS CLARIFICATION markers remain.

| Topic | Decision |
|-------|----------|
| Test Infrastructure | Follow existing patterns in tests/e2e/ |
| Fraud Domain | Account, Transaction, Alert entities |
| Sample Data | 50-100 accounts, 200-500 transactions |
| Demo UX | Rich console, structured errors, auto-detection |
| GraphQL Integration | Mirror biomedical domain structure |
| Performance | Module-scoped fixtures, 50 tests max |
