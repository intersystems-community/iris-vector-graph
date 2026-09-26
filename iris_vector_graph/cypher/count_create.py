"""Run CREATE once per incoming row when only the number of rows matters.

openCypher runs CREATE once per row: `MATCH () CREATE ()` over two nodes creates
two nodes, and `MATCH () CREATE () WITH * MATCH () CREATE ()` then matches the
four nodes once for each of the two rows (Create3 [1][2][3]). The translator
emits one static statement set per CREATE, so it creates one node.

When no clause reads a variable another clause binds, a row carries no values:
each MATCH multiplies the row count by its own match count (taken at that point
in the pipeline, so it sees what earlier CREATEs wrote), and each CREATE runs
once per row. `plan_count_create` returns that pipeline as a list of steps:

* `("count", query)`: a read-only `MATCH ... RETURN count(*)`;
* `("create", query)`: a plain CREATE, run once per current row.

Anything else keeps the static translation (`plan_count_create` returns None).
"""

from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Optional, Set, Tuple

from iris_vector_graph.cypher import ast

Step = Tuple[str, ast.CypherQuery]


def plan_count_create(q: ast.CypherQuery, params: Optional[Dict[str, Any]]) -> Optional[List[Step]]:
    if q.union_queries or q.subsequent_queries or q.procedure_call is not None:
        return None
    if q.return_clause is not None or q.order_by_clause is not None:
        return None
    if q.skip is not None or q.limit is not None:
        return None
    clauses: list = []
    for i, part in enumerate(q.query_parts):
        if part.procedure_call is not None:
            return None
        w = part.with_clause
        if w is not None:
            if not w.star or w.items or w.distinct or w.where_clause is not None:
                return None
            if w.order_by_clause is not None or w.skip is not None or w.limit is not None:
                return None
        elif i != len(q.query_parts) - 1:
            return None
        clauses.extend(part.clauses)

    steps: List[Step] = []
    seen_vars: Set[str] = set()
    matched = False
    for c in clauses:
        if isinstance(c, ast.MatchClause):
            if c.optional or c.named_paths:
                return None
            names = _pattern_vars(c.patterns)
            if names is None or names & seen_vars:
                return None
            seen_vars |= names
            count_q = ast.CypherQuery(
                query_parts=[ast.QueryPart(clauses=[c], with_clause=None)],
                return_clause=ast.ReturnClause(
                    items=[
                        ast.ReturnItem(
                            expression=ast.AggregationFunction(
                                function_name="count", argument=ast.Literal(value="*")
                            ),
                            alias="__rows",
                        )
                    ]
                ),
            )
            count_q.graph_context = q.graph_context
            steps.append(("count", count_q))
            matched = True
        elif isinstance(c, ast.CreateClause):
            names = _pattern_vars(c.patterns)
            if names is None or names & seen_vars:
                return None
            seen_vars |= names
            create_q = ast.CypherQuery(
                query_parts=[ast.QueryPart(clauses=[c], with_clause=None)],
                return_clause=None,
            )
            create_q.graph_context = q.graph_context
            steps.append(("create", create_q))
        else:
            return None
    if not matched or not any(k == "create" for k, _q in steps):
        return None
    # Only a CREATE after a MATCH needs the pipeline.
    first_match = next(i for i, (k, _q) in enumerate(steps) if k == "count")
    if not any(k == "create" for k, _q in steps[first_match:]):
        return None
    return steps


def _pattern_vars(patterns) -> Optional[Set[str]]:
    """Variables the patterns bind, or None when a property reads a variable (a
    value from the row) or one variable appears twice (a join within the clause
    is fine for MATCH, but kept static to stay simple)."""
    names: Set[str] = set()
    for p in patterns:
        for el in list(p.nodes) + list(p.relationships):
            if el.variable is not None:
                if el.variable in names:
                    return None
                names.add(el.variable)
            for v in (el.properties or {}).values():
                if _reads_variable(v):
                    return None
    return names


def _reads_variable(obj) -> bool:
    if isinstance(obj, (ast.Variable, ast.PropertyReference)):
        return True
    if isinstance(obj, (list, tuple)):
        return any(_reads_variable(x) for x in obj)
    if isinstance(obj, dict):
        return any(_reads_variable(x) for x in obj.values())
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return any(_reads_variable(getattr(obj, f.name)) for f in dataclasses.fields(obj))
    return False
