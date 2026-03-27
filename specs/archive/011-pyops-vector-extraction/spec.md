# Feature Specification: PyOps Vector Conversion Extraction

**Feature Branch**: `011-pyops-vector-extraction`  
**Created**: 2026-01-26  
**Status**: Draft  
**Input**: User description: "Extract duplicated vector conversion logic in PyOps.cls into a shared helper method"

## Clarifications

### Session 2026-01-26

- Q: How should the helper method handle empty vectors (size 0)? → A: Fail dimension validation with "Expected 768-dimensional vector, got 0"
- Q: How should the helper method handle non-numeric values in the vector array? → A: Raise ValueError immediately with message identifying the invalid element
- Q: Should the dimension constant (768) be changeable at runtime or only at compile-time? → A: Schema-first lookup (queries database using iris-vector-rag compatible formula) with compile-time fallback

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Developer Maintains Vector Conversion Logic (Priority: P1)

As a developer maintaining the Graph.KG.PyOps class, I need a single location for vector conversion logic so that when I need to modify how vectors are converted from %DynamicArray to JSON, I only change one method rather than hunting through multiple methods for duplicated code.

**Why this priority**: This is the core value of the refactoring - eliminating code duplication to reduce maintenance burden and prevent inconsistent behavior when updates are made in only some locations.

**Independent Test**: Can be fully tested by verifying that both VectorSearch and HybridSearch produce identical results before and after the refactor, and by confirming that only one method contains the conversion logic.

**Acceptance Scenarios**:

1. **Given** the PyOps class has a shared vector conversion helper method, **When** a developer searches for vector conversion logic, **Then** they find exactly one implementation location
2. **Given** VectorSearch is called with a valid 768-dimensional vector, **When** the operation completes, **Then** the results are identical to the pre-refactor behavior
3. **Given** HybridSearch is called with a valid 768-dimensional vector, **When** the operation completes, **Then** the results are identical to the pre-refactor behavior

---

### User Story 2 - Consistent Validation Across Methods (Priority: P2)

As a developer, I want vector validation to be consistent across all methods so that users receive the same error messages regardless of which method they call with invalid input.

**Why this priority**: Inconsistent error messages create confusion for users and make debugging more difficult. This is important but secondary to eliminating the core duplication.

**Independent Test**: Can be tested by calling VectorSearch and HybridSearch with invalid vectors and verifying identical error messages.

**Acceptance Scenarios**:

1. **Given** a null vector is passed to any vector-consuming method, **When** validation occurs, **Then** the same error message is returned across all methods
2. **Given** a vector with wrong dimensions is passed, **When** validation occurs, **Then** the same error message specifying the expected dimension is returned

---

### User Story 3 - Schema-First Dimension Detection (Priority: P3)

As a developer working with different embedding models (e.g., all-MiniLM-L6-v2 with 384 dims, BioBERT with 768 dims, ada-002 with 1536 dims), I want the system to automatically detect the expected dimension from the database schema so that vector validation stays consistent between index-time and search-time without manual configuration.

**Why this priority**: This extends the value of the refactoring by making the system compatible with iris-vector-rag package patterns and supporting multiple embedding models.

**Independent Test**: Can be tested by creating vector tables with different dimensions and verifying the system correctly detects and validates against each.

**Acceptance Scenarios**:

1. **Given** a vector table exists with 384-dimensional embeddings, **When** validation runs, **Then** it uses dimension 384 (detected from schema)
2. **Given** schema lookup fails, **When** validation runs, **Then** it falls back to DEFAULT_EMBEDDING_DIMENSION (768)
3. **Given** a developer calls vectorToJson with explicit expectedDim parameter, **When** validation runs, **Then** it uses the provided dimension instead of schema lookup

---

### Edge Cases

- **Empty vector (size 0)**: Fails dimension validation with error "Expected 768-dimensional vector, got 0"
- **Non-numeric values**: Raises ValueError immediately with message identifying the invalid element (e.g., "Invalid vector element at index 5: expected numeric value")
- **NaN/Infinity values**: Python's `float()` coercion accepts these; they pass validation but may cause downstream SQL/search issues. Behavior deferred to underlying IRIS SQL (no special handling in helper).
- **Nested objects in %DynamicArray**: Caught by non-numeric validation; raises ValueError with index-specific message (same as other non-numeric types).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a single internal helper method for converting %DynamicArray vectors to JSON strings
- **FR-002**: System MUST maintain backward compatibility - all existing method signatures and return values remain unchanged
- **FR-003**: VectorSearch MUST use the shared helper method for vector conversion
- **FR-004**: HybridSearch MUST use the shared helper method for vector conversion
- **FR-005**: System MUST detect expected embedding dimension from database schema (using iris-vector-rag compatible formula: `round(CHARACTER_MAXIMUM_LENGTH / 346)`), with fallback to DEFAULT_EMBEDDING_DIMENSION parameter
- **FR-006**: Validation error messages MUST be identical across all methods that validate vectors
- **FR-007**: The shared helper method MUST be marked as Internal to indicate it's not part of the public API

### Key Entities

- **Vector**: A %DynamicArray containing floating-point values representing an embedding (dimension varies by model: 384, 768, 1536, etc.)
- **VectorJSON**: The JSON string representation of the vector array, suitable for passing to SQL procedures
- **Expected Dimension**: The dimension inferred from database schema at runtime, ensuring consistency between indexed vectors and search queries

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Vector conversion logic exists in exactly 1 method (reduced from 2)
- **SC-002**: All existing tests pass without modification
- **SC-003**: Dimension detection logic exists in exactly 1 method (getExpectedDimension) with fallback constant in 1 parameter
- **SC-004**: Code review confirms no behavioral changes to public API methods
