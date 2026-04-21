# Spec 057: FOREACH Clause

**Created**: 2026-04-18 | **Branch**: 048-unified-edge-store

## Overview

`FOREACH` is a standard openCypher write clause for iterating over a list and executing update operations (SET, CREATE, MERGE, DELETE) for each element. It is entirely missing from the parser. Used heavily in graph loading patterns and conditional updates.

## Requirements

- **FR-001**: `FOREACH (var IN list | update_clause)` MUST parse without error
- **FR-002**: Inner update clauses MUST support `SET`, `CREATE`, `MERGE`, `DELETE`
- **FR-003**: `FOREACH` variable MUST be scoped to the inner clause (not visible after)
- **FR-004**: List may be any expression (literal, variable, function result)

## User Scenarios

```cypher
FOREACH (tag IN ['kg', 'rdf', 'owl'] |
  MERGE (t:Tag {id: tag})
)

MATCH (n) WHERE n.tags IS NOT NULL
FOREACH (tag IN split(n.tags, ',') |
  SET n.processed = true
)
```

## Success Criteria
- `FOREACH (x IN [1,2,3] | SET n.count = x)` parses and executes
- Inner variable `x` not accessible after FOREACH
- Zero regressions
