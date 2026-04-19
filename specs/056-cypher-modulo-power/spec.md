# Spec 056: Cypher Modulo (%) and Power (^) Operators

**Created**: 2026-04-18 | **Branch**: 048-unified-edge-store

## Overview

The `%` (modulo) and `^` (exponentiation) operators are standard openCypher arithmetic but cause hard `SyntaxError: Unexpected character` in v1.53.1. Both are needed for graph algorithms (mod for hash partitioning, power for decay functions, PageRank damping).

## Requirements

- **FR-001**: `n % m` MUST return the remainder of integer division (SQL `MOD(n, m)`)
- **FR-002**: `n ^ m` MUST return n raised to the power m (SQL `POWER(n, m)`)
- **FR-003**: Both operators MUST have correct precedence: `^` binds tighter than `*`, `%` same level as `*`/`/`
- **FR-004**: Both operators MUST work in WHERE, RETURN, and WITH clauses

## User Scenarios

```cypher
RETURN [x IN range(0,9) WHERE x % 2 = 0 | x] AS evens
RETURN 2 ^ 10 AS kb
MATCH (n) WHERE n.score ^ 2 > 100 RETURN n.id
```

## Success Criteria
- `RETURN 7 % 3` → `1`
- `RETURN 2 ^ 8` → `256`
- `RETURN [x IN range(1,5) WHERE x % 2 = 0 | x]` → `[2, 4]`
- Zero regressions
