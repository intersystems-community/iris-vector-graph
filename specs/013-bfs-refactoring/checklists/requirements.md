# Requirements Checklist: BFS Traversal Refactoring

## Functional Requirements

- [ ] **FR-001**: BFS_JSON MUST extract predicate-specific traversal logic into a dedicated helper method
- [ ] **FR-002**: BFS_JSON MUST extract all-predicates traversal logic into a dedicated helper method
- [ ] **FR-003**: System MUST maintain backward compatibility - identical inputs produce identical outputs
- [ ] **FR-004**: Helper methods MUST be marked as Internal to indicate they are not part of the public API
- [ ] **FR-005**: Object creation MUST use direct ._New() and ._Set() pattern instead of ._FromJSON(json.dumps())
- [ ] **FR-006**: The main BFS_JSON method SHOULD have a maximum nesting depth of 3 levels
- [ ] **FR-007**: Error handling SHOULD be added for invalid inputs (srcId, maxHops)

## Success Criteria

- [ ] **SC-001**: Maximum nesting depth in BFS_JSON is reduced from 5+ to 3 or fewer
- [ ] **SC-002**: Cyclomatic complexity of BFS_JSON is reduced by at least 40%
- [ ] **SC-003**: All existing traversal tests pass without modification
- [ ] **SC-004**: Zero uses of ._FromJSON(json.dumps()) pattern in Traversal.cls
- [ ] **SC-005**: Code review confirms the refactored code is easier to understand

## User Stories Verification

- [ ] **US-1**: Developer can understand BFS logic without deep nesting
- [ ] **US-2**: New traversal modes can be added via new helper methods
- [ ] **US-3**: Object creation uses consistent ._New()/_Set() pattern
- [ ] **US-4**: Error handling provides clear messages for invalid inputs
