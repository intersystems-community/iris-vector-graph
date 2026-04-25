# Feature Specification: Cypher WITH Aggregation HAVING Filter

**Feature Branch**: `070-cypher-with-having`
**Created**: 2026-04-24
**Status**: Implemented

## Summary

`WITH n, count(r) AS cnt WHERE cnt > 2` is standard Cypher for post-aggregation filtering (equivalent to SQL HAVING). IVG previously failed with `ValueError: Undefined: cnt` because the aggregation alias was not registered when the WHERE condition was translated. Fixed by routing agg-alias WHERE predicates to SQL HAVING clause.

## Requirements
- **FR-001**: `WITH ..., agg(x) AS alias WHERE alias > N` MUST emit HAVING clause in SQL
- **FR-002**: Non-aggregation aliases in the same WHERE MUST go to WHERE (not HAVING)
- **FR-003**: Mixed AND predicates split correctly between WHERE and HAVING

## Success Criteria
- **SC-001**: `MATCH (n)-[r]->(m) WITH n, count(r) AS cnt WHERE cnt > 2 RETURN n.id, cnt` produces SQL with GROUP BY + HAVING
- **SC-002**: 556+ tests pass
