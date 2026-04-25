# Feature Specification: Var-Length Relationship Property Filter

**Feature Branch**: `074-cypher-varlength-rel-prop`
**Status**: Implemented

## Summary
`[r*1..3 {weight: 5}]` was rejected by the parser (expected `]`, got `{`).
Fixed by parsing the optional `{prop: val}` map after the variable-length spec.
Property filter is passed to the engine via `var_length_paths[].properties`.

## Requirements
- **FR-001**: Parser accepts `{prop: val}` after `*min..max` in relationship pattern
- **FR-002**: Properties captured as `{key: resolved_value}` in `RelationshipPattern.properties`
- **FR-003**: `var_length_paths` dict includes `properties` key (empty dict if none)
- **FR-004**: Engine post-filters BFS results by property constraints (MVP: passes to future ObjScript filtering)

## Success Criteria
- `MATCH (a)-[r*1..3 {weight: 5}]->(b)` parses without error; 556+ tests pass
