# Feature Specification: E2E UX Tests and Enhancements for Demo Applications

**Feature Branch**: `008-demo-ux-e2e-tests`  
**Created**: 2025-01-18  
**Status**: Draft  
**Input**: User description: "Implement E2E UX tests and enhancements for the Biomedical and Fraud Detection demos."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Biomedical Demo E2E Validation (Priority: P1)

As a developer or product stakeholder, I want to run automated E2E tests against the Biomedical demo so that I can verify the complete user journey works correctly after any code changes, preventing regression issues.

**Why this priority**: The Biomedical demo is the primary showcase of IRIS Vector Graph capabilities. Ensuring its end-to-end functionality works correctly is critical for demonstrations, customer evaluations, and developer confidence.

**Independent Test**: Can be fully tested by running the E2E test suite and verifying that all biomedical query patterns (protein lookup, vector similarity, graph traversal) execute successfully and return expected results.

**Acceptance Scenarios**:

1. **Given** a running IRIS database with biomedical data loaded, **When** the E2E test suite executes the biomedical demo workflow, **Then** all protein queries return valid data with correct schema
2. **Given** a protein with embeddings exists, **When** the similarity search is executed via GraphQL, **Then** similar proteins are returned with similarity scores above the threshold
3. **Given** graph relationships exist between proteins, **When** the `interactsWith` resolver is queried, **Then** related proteins are returned with proper pagination
4. **Given** the API server is running, **When** the GraphQL playground is accessed, **Then** the interface loads and sample queries execute successfully

---

### User Story 2 - Fraud Detection Demo E2E Validation (Priority: P2)

As a developer or product stakeholder, I want to run automated E2E tests against a Fraud Detection demo so that I can demonstrate graph-based fraud detection patterns and verify they work correctly.

**Why this priority**: Fraud detection is a compelling use case that showcases real-time graph traversal and pattern detection capabilities. Having a working demo with E2E tests expands the project's appeal to financial services audiences.

**Independent Test**: Can be fully tested by loading fraud-specific test data, running detection queries, and verifying suspicious patterns are identified correctly.

**Acceptance Scenarios**:

1. **Given** fraud test data with known suspicious patterns is loaded, **When** the fraud detection query is executed, **Then** the system identifies the suspicious entities with appropriate confidence scores
2. **Given** a transaction network exists, **When** multi-hop graph traversal is performed, **Then** connected entities are discovered within the configured depth limit
3. **Given** behavioral embeddings are available, **When** anomaly detection via vector similarity is executed, **Then** outliers are correctly identified and ranked

---

### User Story 3 - Interactive Demo Experience Enhancement (Priority: P3)

As a demo presenter or evaluator, I want the demo applications to provide a polished, interactive experience so that I can effectively showcase the system's capabilities without encountering errors or confusing behavior.

**Why this priority**: UX enhancements make demos more compelling and professional, but the core functionality (P1, P2) must work first. Polish follows function.

**Independent Test**: Can be validated by user testing the demo scripts and GraphQL playground, confirming clear output, helpful error messages, and intuitive workflows.

**Acceptance Scenarios**:

1. **Given** a demo script is executed, **When** database connection fails, **Then** a clear, actionable error message is displayed explaining how to start the database
2. **Given** the demo is running, **When** each step completes, **Then** progress indicators and timing information are displayed
3. **Given** test data is missing, **When** the demo attempts to run, **Then** the system offers to load sample data or provides clear instructions

---

### Edge Cases

- What happens when the database contains no data? System should detect empty state and offer to load sample data.
- What happens when IRIS Vector functions are unavailable (pre-2025.1)? System should gracefully degrade and notify user of limited functionality.
- What happens when the API server port is already in use? Clear error message with instructions to stop conflicting processes.
- What happens when embeddings are missing for similarity queries? Return empty results with informational message, not an error.
- What happens when network timeouts occur during E2E tests? Tests should have appropriate timeouts and retry logic.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide an E2E test suite that validates the complete Biomedical demo workflow including database connectivity, GraphQL queries, and data integrity
- **FR-002**: System MUST provide an E2E test suite that validates the Fraud Detection demo workflow including pattern detection and graph traversal
- **FR-003**: E2E tests MUST be runnable via standard pytest commands with clear pass/fail output
- **FR-004**: Demo scripts MUST display progress indicators and timing information for each major operation
- **FR-005**: Demo scripts MUST provide clear, actionable error messages when prerequisites are not met (database down, missing data, etc.)
- **FR-006**: System MUST include sample data loading utilities for both Biomedical and Fraud Detection domains
- **FR-007**: E2E tests MUST complete within 60 seconds under normal conditions to support CI/CD integration
- **FR-008**: Demo scripts MUST work with both Docker-based IRIS and manual IRIS installations
- **FR-009**: FastHTML demo UIs MUST include architecture diagrams accessible via popups or dedicated links
- **FR-010**: System MUST provide full UX E2E tests using agent-browser/Playwright for the FastHTML demo applications

### Key Entities

- **Demo Workflow**: A sequence of operations demonstrating system capabilities (connect, query, display results)
- **E2E Test**: An automated test that validates a complete user journey from start to finish
- **Sample Dataset**: Pre-configured test data representing a realistic use case scenario
- **Test Fixture**: Reusable test data and configuration for consistent test execution

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All Biomedical demo E2E tests pass on a freshly loaded database (100% pass rate)
- **SC-002**: All Fraud Detection demo E2E tests pass on a freshly loaded database (100% pass rate)
- **SC-003**: E2E test suite completes full execution in under 60 seconds
- **SC-004**: Demo scripts display meaningful progress for each of at least 5 major operations
- **SC-005**: Error scenarios produce actionable messages that guide users to resolution
- **SC-006**: Sample data loading completes in under 30 seconds for each domain
- **SC-007**: Demo scripts work without modification on both Docker IRIS (port 1972) and ACORN-1 (port 21972)

## Assumptions

- The existing biomedical types (`Protein`, `Gene`, `Pathway`) in `examples/domains/biomedical/` serve as the foundation for biomedical E2E tests
- A fraud detection domain schema will be created following the same pattern as the biomedical domain
- The `iris-devtester` package handles dynamic port and password configuration
- Tests requiring database operations will use the live IRIS instance per project constitution
- Performance targets assume a local development environment, not CI/CD runners
