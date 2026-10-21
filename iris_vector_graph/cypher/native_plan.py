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
        if len(query.query_parts) == 2:
            return _plan_stages(query, params)
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


def _plan_chain(query: ast.CypherQuery, params: Dict[str, Any], node_keys: bool = False) -> Dict[str, Any]:
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
    # the node index at each position of the chain: the same as the position, except that a chain closing on its
    # first node (a cycle: (a)-..-(c)-[]-(a)) names node 0 again at its last position
    at: List[int] = []
    for i, node in enumerate(nodes):
        if getattr(node, "labels_or", False) or (getattr(node, "has_property_map", False) and not node.properties):
            raise _Decline
        name = node.variable or f"__anon{i}"
        if name in names:
            closing = i == len(nodes) - 1 and i >= 3 and name == names[0] and not node.labels and not node.properties
            if not closing:
                raise _Decline  # a variable bound twice in the chain, other than to close it on its first node
            at.append(0)
            continue
        names.append(name)
        at.append(len(names) - 1)
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
    if varlength and len(names) < len(nodes):
        raise _Decline  # a cycle walked through a variable-length hop
    if varlength and len(hop_specs) > 1:
        # The walk yields each end of the variable-length hop once, without its path, so no other hop of the match
        # can be held to relationship uniqueness against it: allowed only where none could share a relationship.
        vl_types = next(h["types"] for h in hop_specs if "min" in h)
        if any(_may_share(vl_types, h["types"]) for h in hop_specs if "min" not in h):
            raise _Decline

    cross: List[List[Any]] = []
    if wheres:
        for cond in _conjuncts(wheres[0].expression):
            pair = _cross_comparison(cond, names)
            if pair is not None:
                cross.append(pair)
                continue
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
        agg = _grouping(query, names, varlength, node_keys)
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

    fixed = [h for h in hop_specs if "min" not in h]
    for h in hop_specs:
        # a hop keeps its relationships' identity only where another fixed hop could match the same relationship
        h["unique"] = "min" not in h and any(o is not h and _may_share(h["types"], o["types"]) for o in fixed)
    if agg is not None:
        multiplicity = any(a["kind"] != "count_distinct" for a in agg["aggs"])
        # nodes an aggregate reads beyond its count: the grouping keys' and count(DISTINCT)'s
        read = {k[0] for k in agg["keys"]} | {a["var"] for a in agg["aggs"] if a["kind"] == "count_distinct"}
    else:
        multiplicity = not query.return_clause.distinct

    def walk_from(start: int) -> List[Dict[str, Any]]:
        hops = _outward(hop_specs, start, at)
        if agg is not None and hops:
            last = hops[-1]
            if not last["unique"] and "min" not in last and last["to"] not in read:
                last["tally"] = True  # its far node is read by nothing but count: its matches are tallied, not walked
        return hops

    def accumulates(start: int) -> bool:
        # plain counts grouped by the anchor alone: an anchor's matches all fall in one group, summed before grouping
        return agg is not None and all(a["kind"] == "count" for a in agg["aggs"]) and read <= {start}

    anchor = next((i for i, spec in enumerate(node_specs) if spec["props"]), None)
    alts = []
    if anchor is None:
        # no property to look up: any labelled node may start the walk; the executor starts from the smallest label
        labelled = [i for i, spec in enumerate(node_specs) if spec["labels"]]
        if not labelled:
            raise _Decline  # an unanchored, unlabelled scan: family C
        anchor = labelled[0]
        alts = [{"anchor": i, "hops": walk_from(i), "acc": accumulates(i)} for i in labelled]

    return {
        "family": "A",
        "graph": query.graph_context,
        "nodes": node_specs,
        "anchor": anchor,
        "hops": walk_from(anchor),
        "alts": alts,
        "cross": cross,
        "acc": accumulates(anchor),
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


def _outward(hops, anchor, at) -> List[Dict[str, Any]]:
    """The chain's hops in the order a walk from the anchor meets them, each as from -> to.

    Hop k joins the nodes at positions k and k + 1 (`at` maps a position to its node; the anchor is a node, at the
    position of the same number). A hop whose far node the walk has bound already closes a cycle.
    """
    out = []
    for k in range(anchor, len(at) - 1):  # right of the anchor
        h = hops[k]
        out.append(dict(h, **{"from": at[k], "to": at[k + 1]}))
    for k in range(anchor - 1, -1, -1):  # left of the anchor, walked backwards
        h = hops[k]
        out.append(dict(h, **{"from": at[k + 1], "to": at[k], "dir": _FLIP[h["dir"]]}))
    return out


def _cross_comparison(expr, names: List[str]) -> Optional[List[Any]]:
    """[i, key, op, j, key2] for `vi.key op vj.key2` between two chain nodes (a "cross" filter), else None."""
    if not isinstance(expr, ast.BooleanExpression) or expr.operator not in _COMPARISONS:
        return None
    left, right = expr.operands
    if not (isinstance(left, ast.PropertyReference) and isinstance(right, ast.PropertyReference)):
        return None
    i, key = _node_property(left, names)
    j, key2 = _node_property(right, names)
    if i == j or key in _ID_KEYS or key2 in _ID_KEYS:
        raise _Decline
    return [i, key, _COMPARISONS[expr.operator], j, key2]


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


def _grouping(query: ast.CypherQuery, names: List[str], varlength: bool, node_keys: bool = False) -> Dict[str, Any]:
    """Grouping keys (node properties; with node_keys, a first stage's, whole nodes too) and aggregates, in RETURN
    order, and ORDER BY over the returned columns.

    {"keys": [[var, key] | [var, key, ["substring", start, length or -1]], ...], "aggs": [agg, ...],
    "cols": [["k", key index] | ["a", agg index], ...],
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
        elif node_keys and isinstance(item.expression, ast.Variable) and item.expression.name in names:
            keys.append([names.index(item.expression.name), "__node"])  # the node itself: its identity
            cols.append(["k", len(keys) - 1])
        elif isinstance(item.expression, ast.FunctionCall):
            keys.append(_substring_key(item.expression, names))
            cols.append(["k", len(keys) - 1])
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


def _plan_stages(query: ast.CypherQuery, params: Dict[str, Any]) -> Dict[str, Any]:
    """An aggregate over an aggregate: `MATCH chain WITH keys, aggregates [WHERE column op number] RETURN ...`.

    The first stage is the chain grouped by the WITH's items (plan_chain, keys may be whole nodes); "stage2" groups its
    rows again: {"where": [[column, op, number], ...], "keys": [column, ...], "aggs": [{"kind": "count"} |
    {"kind": "count_distinct", "col": column}, ...], "cols": [["k", i] | ["a", j], ...], "order": [[column, desc]]},
    the columns being the first stage's (WITH order) and, in "cols" and "order", the RETURN's.
    """
    first, second = query.query_parts
    w = first.with_clause
    if w is None or second.clauses or second.with_clause is not None or second.procedure_call is not None:
        raise _Decline
    if w.distinct or w.star or w.order_by_clause is not None or w.skip is not None or w.limit is not None:
        raise _Decline
    if query.skip is not None or query.return_clause is None or query.return_clause.distinct:
        raise _Decline
    if not any(isinstance(i.expression, ast.AggregationFunction) for i in w.items):
        raise _Decline
    stage1 = ast.CypherQuery(
        query_parts=[ast.QueryPart(clauses=list(first.clauses))],
        return_clause=ast.ReturnClause(items=list(w.items)),
        graph_context=query.graph_context,
    )
    plan = _plan_chain(stage1, params, node_keys=True)
    cols1 = plan["agg"]["cols"]
    # the first stage's columns by the names the WITH gives them
    names1 = []
    for item in w.items:
        if item.alias is not None:
            names1.append(item.alias)
        elif isinstance(item.expression, ast.Variable):
            names1.append(item.expression.name)
        else:
            raise _Decline
    node_cols = {i for i, (kind, k) in enumerate(cols1) if kind == "k" and plan["agg"]["keys"][k][1] == "__node"}

    def column(expr) -> int:
        if not isinstance(expr, ast.Variable) or expr.is_parameter or expr.name not in names1:
            raise _Decline
        return names1.index(expr.name)

    where = []
    if w.where_clause is not None:
        for cond in _conjuncts(w.where_clause.expression):
            if not isinstance(cond, ast.BooleanExpression) or cond.operator not in _COMPARISONS:
                raise _Decline
            op, (left, right) = _COMPARISONS[cond.operator], cond.operands
            if isinstance(right, ast.Variable) and not isinstance(left, ast.Variable):
                left, right, op = right, left, _MIRROR[op]
            col = column(left)
            number = right.value if isinstance(right, ast.Literal) else None
            if cols1[col][0] != "a" or isinstance(number, bool) or not isinstance(number, (int, float)):
                raise _Decline  # a count against a number: the only filter its numbers are known to compare as
            where.append([col, op, number])

    keys, aggs, cols = [], [], []
    items = query.return_clause.items
    for item in items:
        expr = item.expression
        if isinstance(expr, ast.AggregationFunction):
            if expr.function_name.lower() != "count":
                raise _Decline
            arg = expr.argument
            if isinstance(arg, ast.Literal) and arg.value == "*" and not expr.distinct:
                aggs.append({"kind": "count"})
            elif expr.distinct:
                aggs.append({"kind": "count_distinct", "col": column(arg)})
            else:
                col = column(arg)
                if col not in node_cols and cols1[col][0] != "a":
                    raise _Decline
                aggs.append({"kind": "count"})  # a node or a count is never null
            cols.append(["a", len(aggs) - 1])
        else:
            col = column(expr)
            if col in node_cols:
                raise _Decline  # returning the node itself: not rendered natively
            keys.append(col)
            cols.append(["k", len(keys) - 1])
    order = []
    for o in query.order_by_clause.items if query.order_by_clause is not None else ():
        col = next((i for i, item in enumerate(items) if _names_column(o.expression, item)), None)
        if col is None:
            raise _Decline
        order.append([col, not o.ascending])
    limit = query.limit
    if limit is not None and not isinstance(limit, int):
        raise _Decline
    key_cols = {i for i, (kind, _) in enumerate(cols) if kind == "k"}
    return dict(
        plan,
        stage2={"where": where, "keys": keys, "aggs": aggs, "cols": cols, "order": order},
        width=len(items),
        limit=limit,
        ordered=bool(order) and key_cols <= {c for c, _ in order},
        limited=limit is not None,
    )


def _substring_key(expr, names: List[str]) -> List[Any]:
    """[var, key, ["substring", start, length or -1]] for `substring(var.key, start[, length])`, literal start and
    length (the SQL path's SUBSTRING(val, start + 1, length))."""
    args = expr.arguments
    if expr.function_name.lower() != "substring" or len(args) not in (2, 3):
        raise _Decline
    var, key = _node_property(args[0], names)
    if key in _ID_KEYS:
        raise _Decline
    bounds = []
    for arg in args[1:]:
        literal_int = isinstance(arg, ast.Literal) and type(arg.value) is int
        if not literal_int or arg.value < 0:
            raise _Decline
        bounds.append(arg.value)
    return [var, key, ["substring", bounds[0], bounds[1] if len(bounds) > 1 else -1]]


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
