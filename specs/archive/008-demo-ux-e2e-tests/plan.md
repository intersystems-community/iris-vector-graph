# Implementation Plan: E2E UX Tests and Demo Enhancements

**Branch**: `008-demo-ux-e2e-tests` | **Date**: 2025-01-18 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/008-demo-ux-e2e-tests/spec.md`

## Summary

Implement comprehensive E2E test suites and UX enhancements for the Biomedical and Fraud Detection demos. The Biomedical demo already has a working foundation; the Fraud Detection demo requires new domain schema and sample data. Both need automated E2E tests with clear pass/fail output, improved progress indicators, and actionable error messages.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: pytest, FastAPI (TestClient), strawberry-graphql, iris-devtester  
**Storage**: InterSystems IRIS 2025.1+ (vector search, HNSW index)  
**Testing**: pytest with custom markers (@pytest.mark.e2e, @pytest.mark.requires_database)  
**Target Platform**: Linux/macOS (development), Docker containers  
**Project Type**: Single project with API layer  
**Performance Goals**: E2E test suite <60s, sample data loading <30s  
**Constraints**: Tests MUST use live IRIS database (Constitution II), graceful degradation for missing VECTOR functions  
**Scale/Scope**: ~50 E2E test cases across two domains

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. IRIS-Native Development | PASS | Tests use iris-devtester, embedded Python operators |
| II. Test-First with Live Database | PASS | All E2E tests require live IRIS, no mocks |
| III. Performance as a Feature | PASS | Test suite <60s, individual tests <10s |
| IV. Hybrid Search by Default | PASS | Biomedical demo uses vector+text+graph |
| V. Observability | PASS | Tests log timing, error messages actionable |
| VI. Modular Core Library | PASS | Tests use iris_vector_graph module |
| VII. Explicit Error Handling | PASS | Demo scripts provide clear error messages |
| VIII. Standardized Interfaces | PASS | Uses existing kg_* operators |

**Gate Status**: PASS - No violations requiring justification

## Project Structure

### Documentation (this feature)

```text
specs/008-demo-ux-e2e-tests/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output (Fraud domain entities)
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (sample GraphQL queries)
└── tasks.md             # Phase 2 output
```

### Source Code (repository root)

```text
# New files for this feature
tests/e2e/
├── __init__.py                         # (exists)
├── test_multi_query_engine_platform.py # (exists)
├── test_biomedical_demo.py             # NEW - Biomedical E2E tests
├── test_fraud_demo.py                  # NEW - Fraud Detection E2E tests
└── conftest.py                         # NEW - E2E-specific fixtures

examples/
├── demo_working_system.py              # (exists) - enhance with progress/errors
├── demo_biomedical.py                  # NEW - Biomedical interactive demo
├── demo_fraud_detection.py             # NEW - Fraud detection demo
└── domains/
    ├── biomedical/                     # (exists)
    └── fraud/                          # NEW - Fraud detection domain types
        ├── __init__.py
        ├── types.py                    # GraphQL types: Account, Transaction, Alert
        ├── loaders.py                  # DataLoaders for batched queries
        └── resolver.py                 # Query resolvers

scripts/
├── demo/
│   └── end_to_end_workflow.py          # (exists) - enhance
└── data/
    └── fraud_sample_data.sql           # NEW - Fraud detection sample data

sql/
└── fraud_sample_data.sql               # NEW - Sample fraud network data
```

**Structure Decision**: Extends existing single-project structure. New E2E tests go in `tests/e2e/`, new domain types in `examples/domains/fraud/`, sample data in `sql/`.

## Complexity Tracking

> No constitution violations requiring justification.

| Item | Decision | Rationale |
|------|----------|-----------|
| Fraud Domain | New domain module | Follows established biomedical pattern, reuses core infrastructure |
| Sample Data | SQL fixtures | Consistent with existing sample_data.sql pattern |
| E2E Tests | pytest suite | Extends existing test infrastructure |
