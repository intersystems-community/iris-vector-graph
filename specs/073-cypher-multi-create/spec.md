# Feature Specification: Cypher Multi-Pattern CREATE

**Feature Branch**: `073-cypher-multi-create`
**Status**: Implemented

## Summary
`CREATE (a:Gene {id:"x"}), (b:Drug {id:"y"}), (a)-[:BINDS]->(b)` was rejected by
the parser (expected EOF at the first comma). Fixed by allowing multiple
comma-separated graph patterns in a single CREATE clause.

## Requirements
- **FR-001**: Parser loops on COMMA after each graph pattern in CREATE
- **FR-002**: `CreateClause.patterns: List[GraphPattern]` replaces single `.pattern`
- **FR-003**: Translator iterates all patterns; existing single-pattern behavior preserved
- **FR-004**: MERGE call site updated to wrap in list

## Success Criteria
- `CREATE (a), (b)` and `CREATE (a)-[:R]->(b), (c)` both work; 556+ tests pass
