# Research: Cypher CAST Functions

**Feature**: 032-cypher-cast-coerce | **Date**: 2026-03-31

## R1: Current Broken Behavior

**Decision**: Fix directly in `translate_expression()` before the generic `sql_fn` emit.

**Root cause**: `_CYPHER_FN_MAP` at line 959 maps the functions to SQL equivalents but the generic emit path at line 988-989 ignores the special semantics. The "# handled below" comments never materialize.

**Fix location**: `iris_vector_graph/cypher/translator.py`, after line 987 (`sql_fn = _CYPHER_FN_MAP.get(fn, fn.upper())`), before line 988 (`return f"{sql_fn}(..."`)

## R2: IRIS SQL Types

- `CAST(x AS INTEGER)` — valid in all IRIS 2023.1+ versions
- `CAST(x AS DOUBLE)` — valid
- `CAST(x AS VARCHAR(4096))` — valid; 4096 matches max reasonable property length
- `LOWER(x)` — valid scalar function

## R3: COUNT(DISTINCT) Status

**Decision**: Already implemented — verify with a test.

The `AggregationFunction` AST node has `distinct: bool`. In `translate_expression` line 928: `f"{fn}({'DISTINCT ' if expr.distinct else ''}{arg})"`. The `distinct` flag is set by the parser at `parse_primary_expression()` when `DISTINCT` follows the function name. **This likely already works** — just needs a test to confirm and document.
