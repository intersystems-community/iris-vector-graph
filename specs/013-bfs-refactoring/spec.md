# Feature Specification: BFS Traversal Refactoring

**Feature Branch**: `013-bfs-refactoring`  
**Created**: 2026-01-26  
**Status**: Draft  
**Input**: User description: "Reduce deep nesting in BFS_JSON by extracting traversal logic into helper methods"

## Clarifications

### Session 2026-01-26

- Q: What scale of synthetic graph should be used for benchmarking? → A: Medium (10k nodes, branching 10-20) for initial tests, with scalability to Large (100k+) stress tests.
- Q: Where should benchmark results be saved? → A: Save to file (e.g., benchmarks.json in feature dir)
- Q: How should the system handle a non-existent or null srcId? → A: Return empty %DynamicArray (Safe)
- Q: How should the system handle maxHops values that are 0 or negative? → A: Treat as 0 - return empty %DynamicArray (Safe)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Developer Reads and Understands BFS Logic (Priority: P1)

As a developer new to the codebase, I need the BFS traversal logic to be structured clearly so that I can understand how graph traversal works without mentally tracking 4+ levels of nested loops and conditionals.

**Why this priority**: Code readability directly impacts maintainability. Complex nested logic is error-prone to modify and time-consuming to understand, increasing the risk of introducing bugs during future changes.

**Independent Test**: Can be tested by measuring cyclomatic complexity reduction and by having a new developer trace through the code and explain the logic within a reasonable time frame.

**Acceptance Scenarios**:

1. **Given** a developer examines BFS_JSON, **When** they trace the logic for predicate-specific traversal, **Then** they can identify the specific helper method responsible without navigating nested blocks
2. **Given** a developer examines BFS_JSON, **When** they trace the logic for all-predicates traversal, **Then** they can identify the specific helper method responsible without navigating nested blocks
3. **Given** BFS_JSON is called with valid parameters, **When** the traversal completes, **Then** results are identical to the pre-refactor behavior

---

### User Story 2 - Developer Adds New Traversal Mode (Priority: P2)

As a developer extending the knowledge graph functionality, I need the traversal logic to be modular so that I can add new traversal modes (e.g., reverse traversal, weighted traversal) without modifying the core BFS loop structure.

**Why this priority**: Extensibility enables future enhancements. With extracted helper methods, adding new traversal variants becomes straightforward.

**Independent Test**: Can be tested by simulating the addition of a new traversal helper and verifying it can be integrated without modifying existing traversal helpers.

**Acceptance Scenarios**:

1. **Given** the traversal logic is modularized, **When** a developer needs to add reverse traversal, **Then** they can create a new helper method following the existing pattern without touching other helpers
2. **Given** helper methods follow a consistent interface, **When** a new traversal mode is added, **Then** the main BFS loop can dispatch to it without structural changes

---

### User Story 3 - Consistent Object Creation Pattern (Priority: P3)

As a developer, I want the BFS_JSON method to create %DynamicObject instances directly rather than via JSON serialization so that the code is more performant and consistent with patterns used elsewhere in the codebase.

**Why this priority**: Performance optimization and consistency are valuable but secondary to the core readability improvements.

**Independent Test**: Can be tested by benchmarking object creation and verifying the method signature and return format remain unchanged.

**Acceptance Scenarios**:

1. **Given** BFS_JSON creates result objects, **When** examining the code, **Then** all objects are created via `._New()` and `._Set()` rather than `._FromJSON(json.dumps(...))`
2. **Given** the object creation pattern is changed, **When** BFS_JSON returns results, **Then** the output format is identical to before

---

### User Story 4 - Error Handling for BFS Traversal (Priority: P3)

As a system operator, I need the BFS traversal to handle errors gracefully so that invalid inputs or data issues result in clear error messages rather than silent failures or crashes.

**Why this priority**: Robustness is important but extends beyond the core refactoring scope; can be addressed incrementally.

**Independent Test**: Can be tested by passing invalid srcId values and verifying appropriate error responses.

**Acceptance Scenarios**:

1. **Given** BFS_JSON receives an invalid srcId (non-existent node), **When** traversal runs, **Then** an empty result is returned (current behavior preserved)
2. **Given** the underlying global data is corrupted, **When** traversal encounters an error, **Then** the error is caught and a meaningful message is returned

---

### User Story 5 - Performance Benchmarking (Priority: P2)

As a developer, I want to verify that the refactoring does not introduce performance regressions and ideally improves traversal speed, so that I can maintain the system's responsiveness for large graph traversals.

**Why this priority**: While readability is the primary goal, graph traversal is a performance-critical operation. Benchmarking ensures that architectural improvements don't come at the cost of execution speed.

**Independent Test**: Can be tested by running a standardized set of benchmarks (varying depth and branching factor) before and after the refactoring.

**Acceptance Scenarios**:

1. **Given** a standardized benchmark script, **When** run against the pre-refactor and post-refactor code, **Then** the results show no significant performance regression (>5%)
2. **Given** the move to direct object creation, **When** benchmarking specifically the object instantiation phase, **Then** the results show a measurable improvement in latency per step

---

### Edge Cases

- **Empty or null srcId**: Returns empty %DynamicArray without error
- **Non-positive maxHops**: Values <= 0 are treated as 0, returning an empty %DynamicArray
- How does the system handle cycles in the graph?
- How does the system perform with very large traversal results (memory issues)?
- What happens when preds contains empty strings or null values?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: BFS_JSON MUST extract predicate-specific traversal logic into a dedicated helper method
- **FR-002**: BFS_JSON MUST extract all-predicates traversal logic into a dedicated helper method
- **FR-003**: System MUST maintain backward compatibility - identical inputs produce identical outputs
- **FR-004**: Helper methods MUST be marked as Internal to indicate they are not part of the public API
- **FR-005**: Object creation MUST use direct ._New() and ._Set() pattern instead of ._FromJSON(json.dumps())
- **FR-006**: The main BFS_JSON method SHOULD have a maximum nesting depth of 3 levels
- **FR-007**: Error handling SHOULD be added for invalid inputs (srcId, maxHops)
- **FR-008**: System MUST provide a benchmarking script to measure traversal latency and throughput

### Key Entities

- **Frontier**: The current set of nodes to explore at each hop level
- **Seen Set**: Nodes already visited to prevent cycles
- **Step Object**: The output object representing one edge traversal (id, step, s, p, o)
- **Predicate Sequence**: Optional ordered list of predicates to follow at each hop

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Maximum nesting depth in BFS_JSON is reduced from 5+ to 3 or fewer
- **SC-002**: Cyclomatic complexity of BFS_JSON is reduced by at least 40%
- **SC-003**: All existing traversal tests pass without modification
- **SC-004**: Zero uses of ._FromJSON(json.dumps()) pattern in Traversal.cls
- **SC-005**: Code review confirms the refactored code is easier to understand
- **SC-006**: Benchmark results show <= 5% regression in total traversal time for depths 1-3
- **SC-007**: Object creation benchmarks show >= 20% improvement in instantiation latency
