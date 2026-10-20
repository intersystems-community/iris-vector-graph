"""The planner for natively executed read-only Cypher (spec 253).

`plan_query` turns a parsed query into a plan the ObjectScript executor (Graph.KG.Cypher.Exec) runs, or answers None,
and the query takes today's SQL path unchanged (FR-001). It declines anything outside the supported set; the set grows
family by family (tasks.md), each only after its shadow gate.

Family A (T020/T021, first slice): one MATCH chain of fixed hops from a bound or labelled node, WHERE as a conjunction
of `var.prop op literal | $param` (op one of = <> < <= > >=, a string value), inline property maps, RETURN of node properties
(DISTINCT allowed), ORDER BY node properties, an integer LIMIT.

Family B (T030/T031, first slice): the same, where one relationship is variable-length (any others of types it
cannot share) and nothing downstream can tell two paths to the same end apart (RETURN DISTINCT, or
count(DISTINCT var)): the walk yields each end once (Graph.KG.VarLength).

Aggregates: RETURN of node-property grouping keys beside count(*), count(var), count(DISTINCT var) (over a
variable-length hop, count(DISTINCT var) only), ORDER BY returned columns, an integer LIMIT.

The plan names the anchor (a property lookup, else a label scan), the hops outward from it, the node checks and the
projection; the engine supplies the columns from the translated statement (FR-002).

`native_gate` is the engine-side check that a plan may run at all (FR-003, FR-005, FR-011, FR-014).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from . import ast

_DIRECTIONS = {ast.Direction.OUTGOING: "out", ast.Direction.INCOMING: "in", ast.Direction.BOTH: "both"}
_FLIP = {"out": "in", "in": "out", "both": "both"}
# the node identity, which is not a property row: declined until the plan can read it
_ID_KEYS = ("id", "node_id")


class _Decline(Exception):
    """The query is outside the supported set: the SQL path answers it."""


def plan_query(query: ast.CypherQuery, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The native plan for `query`, or None for the SQL path."""
    if query.is_mutation or query.procedure_call is not None or query.union_queries or query.subsequent_queries:
        return None
    try:
        return _plan_chain(query, params)
    except _Decline:
        return None


def _value(expr, params) -> Dict[str, Any]:
    if isinstance(expr, ast.Variable) and expr.is_parameter:
        if expr.name not in params:
            raise _Decline
        value = params[expr.name]
    elif isinstance(expr, ast.Literal):
        value = expr.value
    else:
        raise _Decline
    # strings only: the SQL path's typed equality rules for numbers, booleans and lists are not reproduced yet
    if not isinstance(value, str):
        raise _Decline
    return {"v": value}


def _plan_chain(query: ast.CypherQuery, params: Dict[str, Any]) -> Dict[str, Any]:
    """Families A and B: one MATCH chain from an anchor (the executor runs both as "family": "A")."""
    if len(query.query_parts) != 1 or query.skip is not None or query.return_clause is None:
        raise _Decline
    part = query.query_parts[0]
    if part.with_clause is not None or part.procedure_call is not None:
        raise _Decline
    matches = [c for c in part.clauses if isinstance(c, ast.MatchClause)]
    wheres = [c for c in part.clauses if isinstance(c, ast.WhereClause)]
    if len(matches) != 1 or len(wheres) > 1 or len(matches) + len(wheres) != len(part.clauses):
        raise _Decline
    match = matches[0]
    if match.optional or match.named_paths or len(match.patterns) != 1:
        raise _Decline
    pattern = match.patterns[0]
    nodes, rels = pattern.nodes, pattern.relationships
    if not nodes or len(rels) != len(nodes) - 1:
        raise _Decline

    names: List[str] = []
    node_specs: List[Dict[str, Any]] = []
    for i, node in enumerate(nodes):
        if getattr(node, "labels_or", False) or (getattr(node, "has_property_map", False) and not node.properties):
            raise _Decline
        name = node.variable or f"__anon{i}"
        if name in names:
            raise _Decline  # a variable bound twice in the chain
        names.append(name)
        props = [[k, _value(v, params)] for k, v in (node.properties or {}).items()]
        if any(k in _ID_KEYS for k, _ in props):
            raise _Decline
        node_specs.append({"labels": list(dict.fromkeys(node.labels or [])), "props": props, "cmp": []})

    hop_specs = []
    varlength = False
    for rel in rels:
        if rel.properties or rel.variable:
            raise _Decline
        spec = {"types": list(rel.types or []), "dir": _DIRECTIONS[rel.direction]}
        vl = rel.variable_length
        if vl is not None:
            if vl.shortest or vl.all_shortest or vl.max_hops is None or varlength:
                raise _Decline
            spec["min"], spec["max"] = int(vl.min_hops), int(vl.max_hops)
            varlength = True
        hop_specs.append(spec)
    if varlength and len(hop_specs) > 1:
        # The walk yields each end of the variable-length hop once, without its path, so no other hop of the match
        # can be held to relationship uniqueness against it: allowed only where none could share a relationship.
        vl_types = next(h["types"] for h in hop_specs if "min" in h)
        if any(_may_share(vl_types, h["types"]) for h in hop_specs if "min" not in h):
            raise _Decline

    if wheres:
        for cond in _conjuncts(wheres[0].expression):
            op, var, key, value = _comparison(cond, params)
            if var not in names or key in _ID_KEYS:
                raise _Decline
            spec = node_specs[names.index(var)]
            if op == "=":
                spec["props"].append([key, value])
            else:
                spec["cmp"].append([key, op, value])

    returns, agg, order = [], None, []
    items = query.return_clause.items
    if any(isinstance(item.expression, ast.AggregationFunction) for item in items):
        agg = _grouping(query, names, varlength)
        # total when every grouping key is ordered on: a group is one row
        key_cols = {i for i, (kind, _) in enumerate(agg["cols"]) if kind == "k"}
        ordered = bool(agg["order"]) and key_cols <= {col for col, _ in agg["order"]}
    else:
        for item in items:
            var, key = _node_property(item.expression, names)
            if key in _ID_KEYS:
                raise _Decline
            returns.append([var, key])
        if varlength and not query.return_clause.distinct:
            raise _Decline  # every path is a row: ends alone cannot answer it
        if query.order_by_clause is not None:
            for item in query.order_by_clause.items:
                var, key = _node_property(item.expression, names)
                if query.return_clause.distinct and [var, key] not in returns:
                    raise _Decline  # DISTINCT orders by returned columns only (the executor dedupes in sort order)
                order.append([var, key, not item.ascending])
        ordered = bool(order)

    limit = query.limit
    if limit is not None and not isinstance(limit, int):
        raise _Decline

    anchor = next((i for i, spec in enumerate(node_specs) if spec["props"]), None)
    alts = []
    if anchor is None:
        # no property to look up: any labelled node may start the walk; the executor starts from the smallest label
        labelled = [i for i, spec in enumerate(node_specs) if spec["labels"]]
        if not labelled:
            raise _Decline  # an unanchored, unlabelled scan: family C
        anchor = labelled[0]
        alts = [{"anchor": i, "hops": _outward(hop_specs, i, len(nodes))} for i in labelled]

    fixed = [h for h in hop_specs if "min" not in h]
    for h in hop_specs:
        # a hop keeps its relationships' identity only where another fixed hop could match the same relationship
        h["unique"] = "min" not in h and any(o is not h and _may_share(h["types"], o["types"]) for o in fixed)
    if agg is not None:
        multiplicity = any(a["kind"] != "count_distinct" for a in agg["aggs"])
    else:
        multiplicity = not query.return_clause.distinct

    return {
        "family": "A",
        "graph": query.graph_context,
        "nodes": node_specs,
        "anchor": anchor,
        "hops": _outward(hop_specs, anchor, len(nodes)),
        "alts": alts,
        "returns": returns,
        "agg": agg,
        "width": len(items),
        "distinct": bool(query.return_clause.distinct),
        # whether a match's count of parallel relationships matters (it does not under DISTINCT / count(DISTINCT))
        "multiplicity": multiplicity,
        "order": order,
        "limit": limit,
        "ordered": ordered,
        "limited": limit is not None,
    }


def _may_share(types: List[str], other: List[str]) -> bool:
    """Whether hops of these types could match the same relationship (no types: any type)."""
    return not types or not other or bool(set(types) & set(other))


def _outward(hops, anchor, n) -> List[Dict[str, Any]]:
    """The chain's hops in the order a walk from the anchor meets them, each as from -> to.

    Hop k joins node k to node k + 1.
    """
    out = []
    for k in range(anchor, n - 1):  # right of the anchor
        h = hops[k]
        out.append(dict(h, **{"from": k, "to": k + 1}))
    for k in range(anchor - 1, -1, -1):  # left of the anchor, walked backwards
        h = hops[k]
        out.append(dict(h, **{"from": k + 1, "to": k, "dir": _FLIP[h["dir"]]}))
    return out


def _node_property(expr, names: List[str]) -> Tuple[int, str]:
    """(node index, key) for `var.key` over a node of the chain."""
    if not isinstance(expr, ast.PropertyReference) or expr.variable not in names:
        raise _Decline
    return names.index(expr.variable), expr.property_name


def _conjuncts(expr) -> List[Any]:
    if isinstance(expr, ast.BooleanExpression) and expr.operator == ast.BooleanOperator.AND:
        return [c for operand in expr.operands for c in _conjuncts(operand)]
    return [expr]


_COMPARISONS = {
    ast.BooleanOperator.EQUALS: "=",
    ast.BooleanOperator.NOT_EQUALS: "<>",
    ast.BooleanOperator.LESS_THAN: "<",
    ast.BooleanOperator.LESS_THAN_OR_EQUAL: "<=",
    ast.BooleanOperator.GREATER_THAN: ">",
    ast.BooleanOperator.GREATER_THAN_OR_EQUAL: ">=",
}
_MIRROR = {"=": "=", "<>": "<>", "<": ">", "<=": ">=", ">": "<", ">=": "<="}


def _comparison(expr, params) -> Tuple[str, str, str, Dict[str, Any]]:
    """(op, var, key, value) for `var.key op value` (or `value op var.key`, mirrored), op one of _COMPARISONS."""
    if not isinstance(expr, ast.BooleanExpression) or expr.operator not in _COMPARISONS:
        raise _Decline
    op = _COMPARISONS[expr.operator]
    left, right = expr.operands
    if isinstance(right, ast.PropertyReference) and not isinstance(left, ast.PropertyReference):
        left, right, op = right, left, _MIRROR[op]
    if not isinstance(left, ast.PropertyReference):
        raise _Decline
    return op, left.variable, left.property_name, _value(right, params)


def _grouping(query: ast.CypherQuery, names: List[str], varlength: bool) -> Dict[str, Any]:
    """Grouping keys (node properties) and aggregates, in RETURN order, and ORDER BY over the returned columns.

    {"keys": [[var, key], ...], "aggs": [agg, ...], "cols": [["k", key index] | ["a", agg index], ...],
    "order": [[column, descending], ...]}
    """
    keys: List[List[Any]] = []
    aggs: List[Dict[str, Any]] = []
    cols: List[List[Any]] = []
    items = query.return_clause.items
    for item in items:
        if isinstance(item.expression, ast.AggregationFunction):
            aggs.append(_aggregate(item.expression, names))
            cols.append(["a", len(aggs) - 1])
        else:
            var, key = _node_property(item.expression, names)
            if key in _ID_KEYS:
                raise _Decline
            keys.append([var, key])
            cols.append(["k", len(keys) - 1])
    if varlength and any(a["kind"] != "count_distinct" for a in aggs):
        raise _Decline  # a count of paths: ends alone cannot answer it
    order = []
    order_items = query.order_by_clause.items if query.order_by_clause is not None else []
    for o in order_items:
        col = next((i for i, item in enumerate(items) if _names_column(o.expression, item)), None)
        if col is None:
            raise _Decline
        order.append([col, not o.ascending])
    return {"keys": keys, "aggs": aggs, "cols": cols, "order": order}


def _names_column(expr, item) -> bool:
    """Whether an ORDER BY expression names RETURN item `item`: by its alias, or as the same expression."""
    if isinstance(expr, ast.Variable) and not expr.is_parameter and item.alias is not None:
        return expr.name == item.alias
    return expr == item.expression


def _aggregate(expr, names: List[str]) -> Dict[str, Any]:
    """count(*) or count(var) (a chain node is never null, so the two agree), count(DISTINCT var)."""
    if expr.function_name.lower() != "count":
        raise _Decline
    arg = expr.argument
    star = isinstance(arg, ast.Literal) and arg.value == "*"
    node = isinstance(arg, ast.Variable) and not arg.is_parameter and arg.name in names
    if not expr.distinct and (star or node):
        return {"kind": "count"}
    if expr.distinct and node:
        return {"kind": "count_distinct", "var": names.index(arg.name)}
    raise _Decline


def native_gate(engine) -> Optional[str]:
    """None when a native plan may run on this engine, else the reason it may not."""
    if not getattr(engine, "native_cypher", False):
        return "native_cypher=False"
    if getattr(engine, "_adjacency_indexed", False) is not True:
        return "adjacency index not declared"
    if getattr(engine, "_in_bulk_load", False):
        return "inside bulk_load_session"
    if not engine._native_privileges():
        return "missing SELECT on a Graph_KG table"
    return None
