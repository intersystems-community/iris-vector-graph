# Requirements Checklist: SQL Parameterization Security Fix

## Functional Requirements

- [ ] **FR-001**: The kgTXT method MUST use SQL parameter binding for the TOP clause value rather than f-string interpolation
- [ ] **FR-002**: System MUST maintain backward compatibility - valid integer k values produce identical results
- [ ] **FR-003**: System SHOULD validate that k is a positive integer before query execution
- [ ] **FR-004**: System SHOULD enforce a maximum value for k to prevent resource exhaustion
- [ ] **FR-005**: All SQL queries in GraphOperators.cls MUST use parameter binding for any dynamic values
- [ ] **FR-006**: Error messages for invalid k values MUST NOT reveal internal implementation details

## Success Criteria

- [ ] **SC-001**: Zero SQL queries in GraphOperators.cls use f-string or format() for dynamic values
- [ ] **SC-002**: Security scan/review confirms no SQL injection vulnerabilities
- [ ] **SC-003**: All existing tests pass without modification
- [ ] **SC-004**: Attempted SQL injection payloads in k parameter are safely rejected or neutralized

## User Stories Verification

- [ ] **US-1**: All SQL queries use parameterized values (no f-string interpolation)
- [ ] **US-2**: Input validation provides defense in depth for k parameter
- [ ] **US-3**: Consistent query patterns across codebase
