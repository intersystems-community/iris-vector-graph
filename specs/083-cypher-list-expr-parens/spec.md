# Feature Specification: Parenthesized Expressions in List Literals

**Feature Branch**: `083-cypher-list-expr-parens`
**Created**: 2026-04-30
**Source**: GQS differential testing — ~50% remaining failures after spec 082

## Root Cause (Confirmed)

```cypher
UNWIND [(n.k1), (n.k2)] AS a RETURN a
```

fails with `Expected ), got .` because the parser encounters `(n.k1)` inside a list literal, tries to parse `(n` as a node pattern, then sees `.k1` and fails.

The parser's `parse_primary_expression` handles `(` in two ways:
1. **Node pattern**: `(n :Label {prop: val})` — in MATCH context
2. **Parenthesized expression**: `(expr)` — in expression context

Inside list literals `[...]` and UNWIND sources, all `(...)` should be parsed as parenthesized expressions, never as node patterns. The parser currently tries the node-pattern path first and fails on property access.

## Failing Patterns

```cypher
UNWIND [(n.k1), (n.k2)] AS a               -- list with paren exprs
UNWIND [(r.k + n.k), 5] AS a              -- mixed list
RETURN [(n.k1), (n.k2)]                   -- list literal in RETURN
WITH [(n.id)] AS ids                       -- list literal in WITH
WHERE (n.k1 + n.k2) > (n.k3 - n.k4)     -- paren arith in WHERE (may already work)
```

## Requirements

- **FR-001**: Inside list literals `[expr, expr, ...]`, any `(expr)` MUST be parsed as a parenthesized expression, never a node pattern
- **FR-002**: `UNWIND [(n.k1), (n.k2)] AS a RETURN a` MUST parse and execute correctly
- **FR-003**: List literals containing mixed paren and non-paren elements MUST parse: `[(n.k1), 5, 'str']`
- **FR-004**: This fix MUST NOT break existing node-pattern parsing in MATCH clauses
- **FR-005**: All existing 567 unit tests continue to pass

## Success Criteria

- **SC-001**: `UNWIND [(n.k1), (n.k2)] AS a RETURN a` parses without error
- **SC-002**: GQS `Expected ), got .` and `Expected ), got (` crashes drop to 0
- **SC-003**: GQS pass rate improves from 50% to 80%+
- **SC-004**: No regression in MATCH node-pattern parsing

## Implementation Note (Root Cause in Parser)

`parse_list_literal()` in `parser.py` calls `parse_expression()` for each element. `parse_expression()` calls down to `parse_primary_expression()`. When `parse_primary_expression()` sees `(`, it currently calls the LPAREN handler which calls `self.parse_expression()` and expects `)` — this should work for `(n.k1)`. The issue is likely that the list literal parser or the expression parser for UNWIND sources calls something that parses `(n` as a node reference.

The specific fix: ensure `parse_primary_expression()` LPAREN branch always parses as `(expr)` — not as a graph pattern — when not in a MATCH pattern context. The context flag (or simply not calling `parse_graph_pattern` inside `parse_primary_expression`) should resolve this.
