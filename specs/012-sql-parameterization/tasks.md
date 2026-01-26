# Tasks: SQL Parameterization Security Fix

**Input**: Design documents from `/specs/012-sql-parameterization/`  
**Prerequisites**: plan.md, spec.md, research.md, quickstart.md

**Tests**: Integration tests in Docker environment verifying SQLi rejection and boundary cases per quickstart.md.

**Organization**: Tasks organized by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- All changes target single file: `iris_src/src/iris/vector/graph/GraphOperators.cls`

---

## Phase 1: Setup (Pre-Fix Baseline)

**Purpose**: Capture current behavior and verify vulnerability before fixing

- [X] T001 Record baseline kgTXT behavior with valid integer k; save output to specs/012-sql-parameterization/baseline.md
- [X] T002 Verify SQL injection vulnerability in kgTXT using malicious k string (e.g., 10; DROP TABLE dummy;--); save error/result to specs/012-sql-parameterization/baseline.md
- [X] T003 Record baseline kgKNNVEC behavior with valid integer k; append to specs/012-sql-parameterization/baseline.md
- [X] T004 Record baseline kgRRF_FUSE behavior with valid integer k; append to specs/012-sql-parameterization/baseline.md

**Checkpoint**: Vulnerability confirmed and baseline behavior documented

---

## Phase 2: Foundational (Input Validation Utility)

**Purpose**: Add the shared validation logic that all user stories depend on

**⚠️ CRITICAL**: User story implementation cannot begin until this phase is complete

- [X] T005 Implement _validate_k(k) internal helper in GraphOperators.cls: MUST handle non-numeric strings by catching ValueError and returning default k=50

**Checkpoint**: Validation utility ready for use

---

## Phase 3: User Story 1 - Secure Query Execution (Priority: P1) MVP

**Goal**: Eliminate SQL injection in kgTXT by using parameter binding

**Independent Test**: Verify kgTXT accepts valid k but rejects/neutralizes SQLi payloads

### Implementation for User Story 1

- [X] T006 [US1] Refactor kgTXT SQL query construction to use TOP ? and bind the k parameter in iris_src/src/iris/vector/graph/GraphOperators.cls
- [X] T007 [US1] Verify kgTXT neutralizes SQLi payloads: malicious 'k' MUST either be coerced to integer safely or trigger a ValueError *before* SQL execution

**Checkpoint**: User Story 1 complete - primary security vulnerability resolved

---

## Phase 4: User Story 2 - Input Validation Defense in Depth (Priority: P2)

**Goal**: Apply robust validation to the k parameter in kgTXT

**Independent Test**: Pass various invalid k values to kgTXT and verify coercion/defaulting works per spec

### Implementation for User Story 2

- [X] T008 [US2] Integrate _validate_k(k) helper into the kgTXT method in iris_src/src/iris/vector/graph/GraphOperators.cls
- [X] T009 [US2] Verify kgTXT boundary conditions (k=0, k=2000, k=None) match spec behavior

**Checkpoint**: User Story 2 complete - kgTXT has defense-in-depth validation

---

## Phase 5: User Story 3 - Consistent Query Patterns (Priority: P3)

**Goal**: Ensure consistent validation across all algorithm methods

**Independent Test**: Verify kgKNNVEC and kgRRF_FUSE apply the same limits and defaults as kgTXT

### Implementation for User Story 3

- [X] T010 [US3] Integrate _validate_k(k) helper into kgKNNVEC in iris_src/src/iris/vector/graph/GraphOperators.cls
- [X] T011 [US3] Integrate _validate_k(k) helper into kgRRF_FUSE in iris_src/src/iris/vector/graph/GraphOperators.cls (replacing/normalizing existing defaulting logic)
- [X] T012 [US3] Verify consistent error messages and limits across all three search methods

**Checkpoint**: User Story 3 complete - consistent patterns established across codebase

---

## Phase 6: Polish & Verification

**Purpose**: Final verification and documentation

- [X] T013 Run final comparison of all baseline behaviors vs refactored behaviors in GraphOperators.cls
- [X] T014 Verify SC-001: Zero SQL queries in GraphOperators.cls use f-string interpolation for dynamic values
- [X] T015 Verify SC-002: Security scan or manual review confirms no SQL injection vulnerabilities
- [X] T015.1 Verify FR-006: Ensure that ValueError messages for invalid 'k' do not contain fragments of the SQL query or internal table names
- [X] T016 Update requirements checklist in specs/012-sql-parameterization/checklists/requirements.md

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - establish baseline first
- **Foundational (Phase 2)**: Depends on Setup - BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational - core security fix
- **User Story 2 (Phase 4)**: Depends on User Story 1 - enhances `kgTXT`
- **User Story 3 (Phase 5)**: Depends on Foundational - can run parallel to US1/US2 (but targets same file)
- **Polish (Phase 6)**: Depends on all user stories complete

### Parallel Opportunities

- T001-T004 (baseline capture) can run in parallel
- T010-T011 (applying validation to other methods) can run parallel after Phase 2, but requires careful coordination as they target the same file.

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 (Capture baseline & confirm vulnerability)
2. Complete Phase 2 (Add validation helper)
3. Complete Phase 3 (Fix SQLi in `kgTXT`)
4. **STOP and VALIDATE**: Confirm security fix works

### Full Implementation

1. Phases 1-3 → MVP complete
2. Phase 4 → Enhanced validation for `kgTXT`
3. Phase 5 → Consistency across class
4. Phase 6 → Final cleanup

---

## Notes

- All changes confined to single file: `iris_src/src/iris/vector/graph/GraphOperators.cls`
- `TOP ?` syntax is confirmed supported in InterSystems IRIS
- The `k` parameter in `kgRRF_FUSE` should be validated using the same logic even if it doesn't touch SQL directly (it passes `k` to others or slices results)
