# Feature Specification: Class Responsibility Clarity

**Feature Branch**: `014-class-responsibility-clarity`  
**Created**: 2026-01-26  
**Status**: Draft  
**Input**: User description: "Clarify and document the distinct responsibilities between GraphOperators and PyOps classes"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Developer Chooses Correct Class for New Method (Priority: P1)

As a developer adding new knowledge graph functionality, I need clear documentation explaining when to add methods to GraphOperators vs PyOps so that I don't inadvertently create redundant implementations or put code in the wrong location.

**Why this priority**: Without clear guidance, developers will continue creating overlapping functionality, compounding the existing problem. This is the root cause that must be addressed first.

**Independent Test**: Can be tested by presenting a new developer with a hypothetical feature requirement and verifying they correctly identify which class should contain the implementation.

**Acceptance Scenarios**:

1. **Given** class-level documentation exists for both GraphOperators and PyOps, **When** a developer reads the documentation, **Then** they can articulate the distinct purpose of each class
2. **Given** a new vector-based search feature needs to be implemented, **When** a developer references the documentation, **Then** they know exactly which class should contain the core algorithm vs the service adapter
3. **Given** a developer searches for "where to add graph methods", **When** they find the documentation, **Then** it explicitly explains the decision criteria

---

### User Story 2 - Auditor Understands System Architecture (Priority: P2)

As a system auditor or architect reviewing the codebase, I need the relationship between GraphOperators and PyOps to be clear so that I can evaluate the system's design and identify potential issues without extensive code archaeology.

**Why this priority**: Architecture clarity is important for maintenance and onboarding, but is secondary to preventing future duplication.

**Independent Test**: Can be tested by asking an external reviewer to describe the system architecture after reading only the class documentation.

**Acceptance Scenarios**:

1. **Given** the class documentation describes the layered architecture, **When** an auditor reviews it, **Then** they understand which class is the algorithm layer vs the service adapter layer
2. **Given** a method exists in both classes with similar names, **When** documentation is consulted, **Then** the distinction (e.g., raw algorithm vs validated API) is clear

---

### User Story 3 - Developer Navigates Existing Functionality (Priority: P3)

As a developer debugging or enhancing existing functionality, I need method-level documentation explaining the purpose and usage context of each method so that I can quickly understand what code does without tracing through implementations.

**Why this priority**: Method documentation is valuable for ongoing maintenance but extends beyond the core architectural clarity issue.

**Independent Test**: Can be tested by timing how quickly a developer can understand the purpose of a randomly selected method using only the documentation.

**Acceptance Scenarios**:

1. **Given** each public method has documentation, **When** a developer reads the method signature and docs, **Then** they understand its purpose without reading the implementation
2. **Given** PyOps.VectorSearch and GraphOperators.kgKNNVEC both exist, **When** documentation is read, **Then** the developer understands why both exist and which to call from where

---

### Edge Cases

- What happens when a method logically belongs in both classes?
- How should shared utilities be organized if both classes need them?
- What if a new method doesn't fit the existing responsibility model?
- How should deprecated methods in the wrong class be handled?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: GraphOperators.cls MUST have a class-level comment block describing its role as the core algorithm implementation layer
- **FR-002**: PyOps.cls MUST have a class-level comment block describing its role as the service adapter layer with input validation
- **FR-003**: Each public method in both classes MUST have InterSystems-style documentation (/// comments) describing purpose, parameters, and return values
- **FR-004**: Documentation MUST explicitly state when to use GraphOperators methods vs PyOps methods
- **FR-005**: The documentation MUST explain the relationship between overlapping methods (e.g., kgKNNVEC vs VectorSearch)
- **FR-006**: A README or design document SHOULD be created explaining the overall architecture of the Graph.KG package

### Key Entities

- **GraphOperators**: Core algorithm implementations for graph operations (vector search, text search, RRF fusion, graph traversal)
- **PyOps**: Service adapter layer providing validated, user-friendly APIs that delegate to GraphOperators or Traversal
- **Graph.KG.Service**: REST endpoint dispatcher that calls PyOps methods
- **Graph.KG.Traversal**: Graph traversal implementations using IRIS globals

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of public classes have class-level documentation blocks
- **SC-002**: 100% of public methods have method-level documentation
- **SC-003**: A new developer can correctly identify where to add a hypothetical new method based on documentation alone
- **SC-004**: Documentation explicitly addresses the GraphOperators/PyOps relationship and when to use each
- **SC-005**: Architecture documentation exists in a discoverable location (README or design doc)
