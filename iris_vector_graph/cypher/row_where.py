"""The clauses after a YIELD that IVG applies to the rows an engine handler answers (4.7.0 row 33).

A procedure the translator answers reads `YIELD a, b WHERE cond` as a WITH of the yielded names
(`translator.yield_where_as_with`), in SQL. A procedure an engine handler answers (`db.*`, `dbms.*`, `apoc.*`,
`gds.*`) computes its rows in Python, and its WHERE is read here, with openCypher's three-valued logic: a row is kept
when the condition is true, and a null (a comparison with null, or of two values of types that do not compare) drops
it. What is read, and nothing more: the yielded names, literals and parameters, `=`, `<>`, `<`, `<=`, `>`, `>=`,
`IN` a literal list, `IS [NOT] NULL`, `AND`, `OR`, `XOR`, `NOT`. Anything else raises, naming it, rather than keep or
drop a row on a guess.

`handler_tail` says which of the clauses after the YIELD a handler's rows can take: a WHERE, one `WITH` of yielded
names with a WHERE (the same filter), a RETURN of yielded names, and ORDER BY, SKIP and LIMIT
(`_engine.query._apply_handler_tail`). Any other clause raises: the handler answered the CALL alone, and a WITH, MATCH
or UNWIND after it was dropped, so `CALL db.labels() YIELD label WITH label WHERE label = 'A' RETURN label` answered
every label.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from . import ast


def _unsupported(expr) -> ValueError:
    return ValueError(
        "WHERE after YIELD of a procedure IVG answers outside SQL reads the yielded names, literals and parameters, "
        f"comparisons, IN a literal list, IS NULL, AND, OR, XOR and NOT; not {expr!r}. "
        "Filter the returned rows in Python instead."
    )


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _eq(a, b) -> Optional[bool]:
    """openCypher `=`: null with null, and a value with a value of another type is false."""
    if a is None or b is None:
        return None
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _is_number(a) and _is_number(b):
        return a == b
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        result: Optional[bool] = True
        for x, y in zip(a, b):
            e = _eq(x, y)
            if e is False:
                return False
            if e is None:
                result = None
        return result
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return False
        keys = sorted(a)
        return _eq([a[k] for k in keys], [b[k] for k in keys])
    return False


def _order(a, b) -> Optional[int]:
    """-1, 0 or 1 for two values openCypher orders against each other (numbers, strings, booleans), else None."""
    if a is None or b is None:
        return None
    if _is_number(a) and _is_number(b):
        if (isinstance(a, float) and math.isnan(a)) or (isinstance(b, float) and math.isnan(b)):
            return None
    elif isinstance(a, list) and isinstance(b, list):
        raise _unsupported("an ordering comparison of two lists")
    elif not ((isinstance(a, str) and isinstance(b, str)) or (isinstance(a, bool) and isinstance(b, bool))):
        return None
    return (a > b) - (a < b)


_ORDERINGS = {
    ast.BooleanOperator.LESS_THAN: lambda c: c < 0,
    ast.BooleanOperator.LESS_THAN_OR_EQUAL: lambda c: c <= 0,
    ast.BooleanOperator.GREATER_THAN: lambda c: c > 0,
    ast.BooleanOperator.GREATER_THAN_OR_EQUAL: lambda c: c >= 0,
}


def _not(v: Optional[bool]) -> Optional[bool]:
    return None if v is None else not v


def _truth(v) -> Optional[bool]:
    if v is None or isinstance(v, bool):
        return v
    raise ValueError(f"WHERE after YIELD: {v!r} is not a boolean")


def _value(expr, row: Dict[str, Any], params: Dict[str, Any]):
    if isinstance(expr, ast.Literal):
        if isinstance(expr.value, (list, dict, ast.MapLiteral)):
            raise _unsupported(expr)
        return expr.value
    if isinstance(expr, ast.Variable):
        if expr.is_parameter:
            if expr.name not in params:
                raise ValueError(f"WHERE after YIELD: parameter ${expr.name} is not given")
            return params[expr.name]
        if expr.name not in row:
            raise ValueError(
                f"WHERE after YIELD reads {expr.name!r}, which is not a yielded name ({', '.join(row)})"
            )
        return row[expr.name]
    if isinstance(expr, ast.BooleanExpression):
        return _boolean(expr, row, params)
    raise _unsupported(expr)


def _boolean(expr: ast.BooleanExpression, row, params) -> Optional[bool]:
    op = expr.operator
    ops = expr.operands
    if op is ast.BooleanOperator.NOT:
        return _not(_truth(_value(ops[0], row, params)))
    if op in (ast.BooleanOperator.AND, ast.BooleanOperator.OR, ast.BooleanOperator.XOR):
        vals = [_truth(_value(o, row, params)) for o in ops]
        if op is ast.BooleanOperator.AND and False in vals:
            return False
        if op is ast.BooleanOperator.OR and True in vals:
            return True
        if None in vals:
            return None
        if op is ast.BooleanOperator.AND:
            return True
        if op is ast.BooleanOperator.OR:
            return False
        out = False
        for v in vals:
            out = out != v
        return out
    if op is ast.BooleanOperator.IS_NULL:
        return _value(ops[0], row, params) is None
    if op is ast.BooleanOperator.IS_NOT_NULL:
        return _value(ops[0], row, params) is not None
    if len(ops) != 2:
        raise _unsupported(expr)
    if op is ast.BooleanOperator.IN:
        listed = ops[1]
        if not (isinstance(listed, ast.Literal) and isinstance(listed.value, list)):
            raise _unsupported(expr)
        a = _value(ops[0], row, params)
        found: Optional[bool] = False
        for e in listed.value:
            m = _eq(a, _value(e, row, params))
            if m:
                return True
            if m is None:
                found = None
        return found
    if op not in _ORDERINGS and op not in (ast.BooleanOperator.EQUALS, ast.BooleanOperator.NOT_EQUALS):
        raise _unsupported(expr)
    a, b = (_value(o, row, params) for o in ops)
    if op is ast.BooleanOperator.EQUALS:
        return _eq(a, b)
    if op is ast.BooleanOperator.NOT_EQUALS:
        return _not(_eq(a, b))
    if op in _ORDERINGS:
        c = _order(a, b)
        return None if c is None else _ORDERINGS[op](c)
    raise _unsupported(expr)


def row_passes(expr, row: Dict[str, Any], params: Dict[str, Any]) -> bool:
    """Whether the WHERE condition `expr` is true for `row` (the yielded names to their values)."""
    return _truth(_value(expr, row, params)) is True


def yield_names(proc) -> Dict[str, str]:
    """Each name the YIELD binds -> the handler's column it reads (`node AS n` binds n to column node)."""
    pairs = [item if isinstance(item, tuple) else (item, item) for item in proc.yield_items or []]
    return {name: column for column, name in pairs}


def _clause_name(clause) -> str:
    """`MatchClause` -> `MATCH`, as the query spells the clause."""
    name = type(clause).__name__
    return name[: -len("Clause")].upper() if name.endswith("Clause") else name


def handler_tail(parsed) -> Tuple[List[Any], Optional[List[str]]]:
    """(the WHERE conditions, the RETURN's names or None) of a CALL a handler answers; ValueError naming any clause
    after the YIELD it cannot apply.

    A handler's rows take a WHERE after the YIELD, one `WITH` of yielded names with an optional WHERE and nothing
    else (`YIELD label WITH label WHERE label = 'A'`, the same filter), a RETURN of yielded names, and ORDER BY, SKIP
    and LIMIT (applied by the caller). Before 4.7.0 (row 33) every other clause was dropped without a word."""
    proc = parsed.procedure_call
    name = proc.procedure_name
    scope = yield_names(proc)
    conditions = [proc.yield_where.expression] if proc.yield_where is not None else []

    def refuse(what: str) -> ValueError:
        return ValueError(
            f"{name} is answered by IVG outside SQL, which applies a WHERE, a WITH of the yielded names and its "
            f"WHERE, a RETURN of the yielded names, ORDER BY, SKIP and LIMIT after the YIELD; not {what}. Run the "
            "rest in Python over the returned rows."
        )

    if parsed.union_queries:
        raise refuse("UNION")
    if getattr(parsed, "subsequent_queries", None):
        raise refuse("a later statement")
    parts = list(parsed.query_parts or [])
    if parts:
        first = parts[0]
        if first.clauses:
            raise refuse(_clause_name(first.clauses[0]))
        if first.procedure_call is not None:
            raise refuse("a second CALL")
        w = first.with_clause
        if w is not None:
            if w.star or w.distinct or w.order_by_clause is not None or w.skip is not None or w.limit is not None:
                raise refuse("a WITH that does more than pass yielded names on")
            kept = {}
            for item in w.items:
                e = item.expression
                if not isinstance(e, ast.Variable) or e.is_parameter or e.name not in scope:
                    raise refuse(f"WITH {item.source_text or e!r}")
                if item.alias is not None and item.alias != e.name:
                    raise refuse(f"WITH {e.name} AS {item.alias}")
                kept[e.name] = scope[e.name]
            scope = kept
            if w.where_clause is not None:
                conditions.append(w.where_clause.expression)
        for later in parts[1:]:
            if later.clauses:
                raise refuse(_clause_name(later.clauses[0]))
            if later.with_clause is not None:
                raise refuse("a second WITH")
            if later.procedure_call is not None:
                raise refuse("a second CALL")
    rc = parsed.return_clause
    if rc is None:
        return conditions, None
    if rc.distinct:
        raise refuse("RETURN DISTINCT")
    names = []
    for item in rc.items:
        e = item.expression
        if not isinstance(e, ast.Variable) or e.is_parameter or e.name not in scope:
            raise refuse(f"RETURN {item.source_text or e!r}")
        if item.alias is not None and item.alias != e.name:
            raise refuse(f"RETURN {e.name} AS {item.alias}")
        names.append(e.name)
    return conditions, names


def where_rows(result, proc: ast.CypherProcedureCall, params: Optional[Dict[str, Any]], conditions=None):
    """`result` (a handler's answer) with only the rows every condition keeps, in their order, columns unchanged.

    `conditions` defaults to the WHERE after the YIELD. A condition sees the yielded names: a column yielded as
    itself, or under its `AS` alias; a column the YIELD does not name is not in scope."""
    if conditions is None:
        conditions = [proc.yield_where.expression] if proc.yield_where is not None else []
    if not conditions or not getattr(result, "rows", None):
        return result
    columns = list(result.columns or [])
    names = yield_names(proc)
    missing = [c for c in names.values() if c not in columns]
    if missing:
        raise ValueError(f"{proc.procedure_name} answers {columns}, not {missing}")
    at = [(n, columns.index(c)) for n, c in names.items()]
    params = params or {}
    result.rows = [
        r for r in result.rows if all(row_passes(c, {n: r[i] for n, i in at}, params) for c in conditions)
    ]
    return result
