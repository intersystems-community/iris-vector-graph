# Tasks: PyOps Vector Conversion Extraction

**Input**: Design documents from `/specs/011-pyops-vector-extraction/`  
**Prerequisites**: plan.md, spec.md, data-model.md, quickstart.md

**Tests**: No automated tests requested. Manual validation via IRIS terminal per quickstart.md.

**Organization**: Tasks organized by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- All changes target single file: `iris_src/src/Graph/KG/PyOps.cls`

---

## Phase 1: Setup (Pre-Refactoring Baseline)

**Purpose**: Capture current behavior before making changes

- [X] T001 Record baseline VectorSearch behavior with valid 768-dim vector; save output to specs/011-pyops-vector-extraction/baseline.md
- [X] T002 Record baseline HybridSearch behavior with valid 768-dim vector; append to specs/011-pyops-vector-extraction/baseline.md
- [X] T003 Record baseline error behavior with null vector for both methods; append to specs/011-pyops-vector-extraction/baseline.md
- [X] T004 Record baseline error behavior with wrong-dimension vector for both methods; append to specs/011-pyops-vector-extraction/baseline.md

**Checkpoint**: Baseline behavior documented for before/after comparison

---

## Phase 2: Foundational (Parameter & Helper Method)

**Purpose**: Add the shared infrastructure that all user stories depend on

**⚠️ CRITICAL**: User story implementation cannot begin until this phase is complete

- [X] T005 Add `Parameter EMBEDDING_DIMENSION = 768;` at class level in iris_src/src/Graph/KG/PyOps.cls
- [X] T006 Add internal helper method `_vectorToJson` with null validation in iris_src/src/Graph/KG/PyOps.cls
- [X] T007 Add dimension validation using EMBEDDING_DIMENSION parameter in `_vectorToJson` in iris_src/src/Graph/KG/PyOps.cls
- [X] T008 Add non-numeric element validation with index-specific error in `_vectorToJson` in iris_src/src/Graph/KG/PyOps.cls
- [X] T009 Add JSON conversion logic to `_vectorToJson` in iris_src/src/Graph/KG/PyOps.cls
- [X] T010 Mark `_vectorToJson` as `[ Internal ]` in method signature in iris_src/src/Graph/KG/PyOps.cls

**Checkpoint**: Helper method complete and ready for integration

---

## Phase 3: User Story 1 - Single Location for Vector Conversion (Priority: P1) MVP

**Goal**: Eliminate code duplication by having VectorSearch and HybridSearch use the shared helper

**Independent Test**: Verify VectorSearch and HybridSearch produce identical results to baseline, and only one method contains conversion logic

### Implementation for User Story 1

- [X] T011 [US1] Update VectorSearch to call `_vectorToJson(vec)` instead of inline conversion in iris_src/src/Graph/KG/PyOps.cls
- [X] T012 [US1] Remove duplicated vector conversion code (lines 15-19) from VectorSearch in iris_src/src/Graph/KG/PyOps.cls
- [X] T013 [US1] Update HybridSearch to call `_vectorToJson(vec)` instead of inline conversion in iris_src/src/Graph/KG/PyOps.cls
- [X] T014 [US1] Remove duplicated vector conversion code (lines 43-48) from HybridSearch in iris_src/src/Graph/KG/PyOps.cls
- [X] T015 [US1] Verify VectorSearch produces identical results to baseline (T001)
- [X] T016 [US1] Verify HybridSearch produces identical results to baseline (T002)

**Checkpoint**: User Story 1 complete - code duplication eliminated, behavior unchanged

---

## Phase 4: User Story 2 - Consistent Validation Messages (Priority: P2)

**Goal**: Ensure identical error messages from both methods for same invalid input

**Independent Test**: Call VectorSearch and HybridSearch with invalid vectors, verify identical error messages

### Implementation for User Story 2

- [X] T017 [US2] Verify null vector error message is identical from VectorSearch and HybridSearch
- [X] T018 [US2] Verify wrong-dimension error message is identical from VectorSearch and HybridSearch
- [X] T019 [US2] Test with non-numeric element and verify error includes index number
- [X] T020 [US2] Compare error messages to baseline (T003, T004) - confirm new messages are consistent

**Checkpoint**: User Story 2 complete - validation messages unified

---

## Phase 5: User Story 3 - Configurable Dimension Constant (Priority: P3)

**Goal**: Verify dimension is defined in exactly one location and can be changed easily

**Independent Test**: Change EMBEDDING_DIMENSION value and verify all validation uses new value

### Implementation for User Story 3

- [X] T021 [US3] Verify only one definition of dimension constant exists in iris_src/src/Graph/KG/PyOps.cls
- [X] T022 [US3] Temporarily change EMBEDDING_DIMENSION to 384 and verify error messages reflect new value
- [X] T023 [US3] Restore EMBEDDING_DIMENSION to 768 in iris_src/src/Graph/KG/PyOps.cls
- [X] T024 [US3] Grep codebase to confirm no hardcoded 768 values remain outside the Parameter

**Checkpoint**: User Story 3 complete - dimension is single-source configurable

---

## Phase 6: Polish & Verification

**Purpose**: Final validation and cleanup

- [X] T025 Run final comparison of all baseline behaviors vs refactored behaviors
- [X] T026 Verify SC-001: Vector conversion logic exists in exactly 1 method
- [X] T027 Verify SC-003: Dimension constant defined in exactly 1 location
- [X] T028 Verify SC-004: No behavioral changes to public API methods
- [X] T029 Update requirements checklist in specs/011-pyops-vector-extraction/checklists/requirements.md

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - establish baseline first
- **Foundational (Phase 2)**: Depends on Setup - BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational - core refactoring
- **User Story 2 (Phase 4)**: Depends on User Story 1 - validates consistency achieved
- **User Story 3 (Phase 5)**: Depends on Foundational - can run parallel to US1/US2
- **Polish (Phase 6)**: Depends on all user stories complete

### User Story Dependencies

- **User Story 1 (P1)**: Must complete first - core extraction
- **User Story 2 (P2)**: Validates US1 achieved consistency - sequential after US1
- **User Story 3 (P3)**: Independent validation - can run after Phase 2

### Within Each Phase

- Single file means limited parallelism within phases
- Most tasks are sequential due to editing same file

### Parallel Opportunities

- T001-T004 (baseline capture) can run in parallel
- T021-T024 (US3 validation) can run after Phase 2, parallel to US1/US2
- Limited [P] opportunities due to single-file refactoring

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (capture baseline)
2. Complete Phase 2: Foundational (add helper method)
3. Complete Phase 3: User Story 1 (integrate helper)
4. **STOP and VALIDATE**: Compare to baseline
5. Merge if US1 sufficient

### Full Implementation

1. Phases 1-3 → MVP complete
2. Phase 4 → Validate consistent messages
3. Phase 5 → Validate single-source config
4. Phase 6 → Final verification

---

## Notes

- All changes confined to single file: `iris_src/src/Graph/KG/PyOps.cls`
- No automated tests - manual IRIS terminal validation
- Baseline capture (Phase 1) is critical for verification
- This is a refactoring - no new functionality, only structure improvement
