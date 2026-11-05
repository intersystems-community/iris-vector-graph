"""Run a MERGE once per incoming row when the static translation cannot.

openCypher runs MERGE once per row, and each row sees what earlier rows merged.
The translator emits one static statement set per MERGE, which is right when the
pattern's values are fixed (literals, parameters, an unrolled UNWIND literal) but
not in two shapes:

* the MERGE follows a WITH and its properties are that WITH's values
  (`MATCH (n) WITH n.x AS x MERGE (:N {x: x})`, Merge1 [9], Merge9 [4]) — the
  static MERGE has one node id for all rows and no value to probe with;
* the MERGE follows a DELETE in the same part (`MATCH (a) DELETE a MERGE (b:A)`,
  Merge1 [14]) — the result is read before the DELETE, so it finds the nodes
  the DELETE removes.

For these, the query splits at the first MERGE. The prefix runs as its own query
returning the WITH's values (or `count(*)` when the MERGE reads none of them); the
suffix is then run once per row with the row's values bound in as literals.
Anything else keeps the static translation (`plan_row_merge` returns None).
"""

from __future__ import annotations

import copy
import dataclasses
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from iris_vector_graph.cypher import ast

_SCALAR = (str, int, float, bool, type(None))


def _inlinable(v, lists_ok: bool) -> bool:
    if isinstance(v, _SCALAR):
        return True
    return lists_ok and isinstance(v, list) and all(isinstance(x, _SCALAR) for x in v)


@dataclass
class RowMergePlan:
    prefix: ast.CypherQuery
    row_vars: List[str]  # prefix columns bound into the suffix; [] = count(*) rows
    suffix_parts: List[ast.QueryPart]
    return_clause: Optional[ast.ReturnClause]
    graph_context: Optional[str] = None
    # prefix column -> (variable, property) it reads; those references are inlined
    prop_refs: Dict[str, Tuple[str, str]] = dataclasses.field(default_factory=dict)
    # prefix columns bound as the prefix read them: a node property RETURN reads typed under any driver (R218), so a
    # stored string '7' is no number (4.6.0 R286); the others are re-typed from text (_prefix_value)
    typed_cols: frozenset = frozenset()
    # a list of scalars may be inlined (a CREATE stores it as a list literal; a MERGE matches on scalars only)
    lists_ok: bool = False

    def bind(self, row: Dict[str, Any]) -> Optional[ast.CypherQuery]:
        """The suffix query for one row, or None when a value cannot be inlined."""
        if any(not _inlinable(v, self.lists_ok) for v in row.values()):
            return None
        row = {self.prop_refs.get(k, k): v for k, v in row.items()}
        parts = [_substitute(copy.deepcopy(p), row) for p in self.suffix_parts]
        ret = None
        if self.return_clause is not None:
            items = []
            for it in self.return_clause.items:
                alias = it.alias
                if alias is None and isinstance(it.expression, ast.Variable) and (
                    it.expression.name in row
                ):
                    alias = it.expression.name
                if alias is None and isinstance(it.expression, ast.PropertyReference) and (
                    (it.expression.variable, it.expression.property_name) in row
                ):
                    alias = f"{it.expression.variable}.{it.expression.property_name}"
                items.append(
                    ast.ReturnItem(
                        expression=_substitute(copy.deepcopy(it.expression), row), alias=alias
                    )
                )
            ret = ast.ReturnClause(items=items, distinct=self.return_clause.distinct)
        for p in parts:
            for c in p.clauses:
                if isinstance(c, ast.MergeClause) and not _pattern_is_bound(c.pattern):
                    return None
                if isinstance(c, ast.CreateClause) and not all(_pattern_is_bound(g) for g in c.patterns):
                    return None
        q = ast.CypherQuery(query_parts=parts, return_clause=ret)
        q.graph_context = self.graph_context
        return q


def plan_row_merge(q: ast.CypherQuery, params: Optional[Dict[str, Any]]) -> Optional[RowMergePlan]:
    params = params or {}
    if q.union_queries or q.subsequent_queries or q.procedure_call is not None:
        return None
    if q.order_by_clause is not None or q.skip is not None or q.limit is not None:
        return None
    loc = _first_merge(q)
    if loc is None:
        return None
    k, j = loc
    part = q.query_parts[k]
    # The suffix is MERGEs only, and the query ends with them.
    if k != len(q.query_parts) - 1 or part.with_clause is not None:
        return None
    suffix_clauses = part.clauses[j:]
    if not all(isinstance(c, ast.MergeClause) for c in suffix_clauses):
        return None
    ret = q.return_clause
    if ret is not None and (ret.distinct or any(_has_agg(it.expression) for it in ret.items)):
        return None

    bound_by_suffix = set()
    for c in suffix_clauses:
        bound_by_suffix |= _pattern_vars(c)
    used = set()
    for c in suffix_clauses:
        used |= _vars_in(c)
    if ret is not None:
        for it in ret.items:
            used |= _vars_in(it.expression)
    outer = used - bound_by_suffix - set(params)

    head_clauses = part.clauses[:j]
    if j == 0:
        # MERGE right after a WITH that projects the values it merges on.
        if k == 0:
            return None
        w = q.query_parts[k - 1].with_clause
        if w is None or w.star:
            return None
        aliases = [_item_name(it) for it in w.items]
        if None in aliases or not outer or not outer <= set(aliases):
            return None
        if any(_has_agg(it.expression) for it in w.items):
            return None
        if not _merges_on(suffix_clauses, outer):
            return None
        # Only values can be inlined: a node, relationship or path stays static.
        if outer & _graph_vars(q.query_parts[:k]):
            return None
        if _has_updates(q.query_parts[:k]):
            return None
        row_vars = [a for a in aliases if a in outer]
        prefix_parts = q.query_parts[:k]
        prefix_ret = ast.ReturnClause(
            items=[ast.ReturnItem(expression=ast.Variable(name=a), alias=a) for a in row_vars]
        )
    elif not any(isinstance(c, ast.DeleteClause) for c in head_clauses):
        # MERGE after a MATCH in the same part whose pattern reads properties of the
        # matched rows (`MATCH (p:Person) MERGE (:City {name: p.bornIn})`, Merge1
        # [11]). The MATCH runs as a read-only prefix returning those properties.
        if not outer or _has_updates(q.query_parts[:k]):
            return None
        if not all(isinstance(c, _READING) for c in head_clauses):
            return None
        refs = _outer_prop_refs(suffix_clauses, ret, outer)
        if not refs or not _merges_on(suffix_clauses, {v for v, _p in refs}):
            return None
        prop_refs = {f"__rm{i}": r for i, r in enumerate(sorted(refs))}
        prefix = ast.CypherQuery(
            query_parts=list(q.query_parts[:k])
            + [ast.QueryPart(clauses=list(head_clauses), with_clause=None)],
            return_clause=ast.ReturnClause(
                items=[
                    ast.ReturnItem(
                        expression=ast.PropertyReference(variable=v, property_name=pn),
                        alias=a,
                    )
                    for a, (v, pn) in prop_refs.items()
                ]
            ),
        )
        prefix.graph_context = q.graph_context
        return RowMergePlan(
            prefix=prefix,
            row_vars=list(prop_refs),
            suffix_parts=[ast.QueryPart(clauses=list(suffix_clauses), with_clause=None)],
            return_clause=ret,
            graph_context=q.graph_context,
            prop_refs=prop_refs,
        )
    else:
        # MERGE after a DELETE in the same part, reading none of the part's rows.
        if outer:
            return None
        head_bound = _graph_vars(
            list(q.query_parts[:k])
            + [ast.QueryPart(clauses=list(head_clauses), with_clause=None)]
        )
        if bound_by_suffix & head_bound:
            # The suffix's MERGE reuses a variable the head already bound (and
            # perhaps deleted), e.g. `MATCH (a)-[t:T]->(b) DELETE t MERGE
            # (a)-[t2:T]->(b)` (Merge5 [20], [21]). That entity's identity has
            # to flow row-by-row from the head's MATCH; a bare row count can't
            # carry it, so the static translator handles the whole statement.
            return None
        if _has_updates(q.query_parts[:k]) or any(
            isinstance(c, ast.UpdatingClause) and not isinstance(c, ast.DeleteClause)
            for c in head_clauses
        ):
            return None
        row_vars = []
        prefix_parts = q.query_parts[:k] + [
            ast.QueryPart(clauses=list(head_clauses), with_clause=None)
        ]
        prefix_ret = ast.ReturnClause(
            items=[
                ast.ReturnItem(
                    expression=ast.AggregationFunction(
                        function_name="count", argument=ast.Literal(value="*")
                    ),
                    alias="__rows",
                )
            ]
        )
    prefix = ast.CypherQuery(query_parts=list(prefix_parts), return_clause=prefix_ret)
    prefix.graph_context = q.graph_context
    return RowMergePlan(
        prefix=prefix,
        row_vars=row_vars,
        suffix_parts=[ast.QueryPart(clauses=list(suffix_clauses), with_clause=None)],
        return_clause=ret,
        graph_context=q.graph_context,
    )


def plan_row_create(q: ast.CypherQuery, params: Optional[Dict[str, Any]]) -> Optional[RowMergePlan]:
    """A CREATE right after a WITH that projects the values it stores, run once per row (4.6.0 R120).

    `UNWIND range(1,500) AS i WITH i LIMIT 3 CREATE (:X {i: i})`: the translator names a CREATE's row by the `%ID` of
    the tables a MATCH joined (`_create_row_key`), and a stage's rows have none, so the static statements made one node
    for all of them and its second property insert failed with SQLCODE -119. As for a MERGE after a WITH, the prefix
    runs as a read returning the WITH's values and the CREATEs then run once per row with the row's values inlined.
    Only values: a WITH carrying a node, relationship or path keeps the static translation (None), as does anything
    whose WITH aggregates (one row, which the static statements already create once).
    """
    params = params or {}
    if q.union_queries or q.subsequent_queries or q.procedure_call is not None:
        return None
    if q.order_by_clause is not None or q.skip is not None or q.limit is not None:
        return None
    k = len(q.query_parts) - 1
    part = q.query_parts[k]
    if k == 0 or part.with_clause is not None or part.procedure_call is not None or not part.clauses:
        return None
    if not all(isinstance(c, ast.CreateClause) for c in part.clauses):
        return None
    ret = q.return_clause
    if ret is not None and (ret.distinct or any(_has_agg(it.expression) for it in ret.items)):
        return None
    bound_by_suffix = set()
    for c in part.clauses:
        for g in c.patterns:
            bound_by_suffix |= {el.variable for el in list(g.nodes) + list(g.relationships) if el.variable}
    used = _vars_in(part.clauses)
    if ret is not None:
        for it in ret.items:
            used |= _vars_in(it.expression)
    outer = used - bound_by_suffix - set(params)
    w = q.query_parts[k - 1].with_clause
    if w is None or w.star:
        return None
    aliases = [_item_name(it) for it in w.items]
    if None in aliases or not outer or not outer <= set(aliases):
        return None
    if any(_has_agg(it.expression) for it in w.items):
        return None
    if _has_updates(q.query_parts[:k]):
        return None
    graph_outer = outer & _graph_vars(q.query_parts[:k])
    # 4.6.0 R286: a node the WITH carries, read only through its properties (`WITH n CREATE (:H {v: n.k})`), is
    # carried as those values, as R120 carries `WITH n.k AS k`: the static statements bound `n.k`'s AST
    refs = _create_prop_refs(part.clauses, ret, graph_outer) if graph_outer else set()
    if refs is None:
        return None
    row_vars = [a for a in aliases if a in outer - graph_outer]
    prop_refs = {f"__rc{i}": r for i, r in enumerate(sorted(refs))}
    prefix = ast.CypherQuery(
        query_parts=list(q.query_parts[:k]),
        return_clause=ast.ReturnClause(
            items=[ast.ReturnItem(expression=ast.Variable(name=a), alias=a) for a in row_vars]
            + [
                ast.ReturnItem(expression=ast.PropertyReference(variable=v, property_name=pn), alias=a)
                for a, (v, pn) in prop_refs.items()
            ]
        ),
    )
    prefix.graph_context = q.graph_context
    return RowMergePlan(
        prefix=prefix,
        row_vars=row_vars + list(prop_refs),
        suffix_parts=[ast.QueryPart(clauses=list(part.clauses), with_clause=None)],
        return_clause=ret,
        graph_context=q.graph_context,
        prop_refs=prop_refs,
        typed_cols=frozenset(prop_refs),
        lists_ok=True,
    )


def _create_prop_refs(creates, ret, graph_vars):
    """`{(var, prop)}` the CREATE patterns read of `graph_vars` through their property maps, or None when one is read
    another way: as a pattern's node (an endpoint), bare in a property value, or in the RETURN (a node there must be
    the one the row carried, which inlined values are not)."""
    refs: set = set()
    for c in creates:
        for g in c.patterns:
            for el in list(g.nodes) + list(g.relationships):
                if el.variable in graph_vars:
                    return None
                for v in (el.properties or {}).values():
                    if not _only_prop_refs(v, graph_vars, refs):
                        return None
    if ret is not None and _vars_in([it.expression for it in ret.items]) & graph_vars:
        return None
    return refs


# ── helpers ──────────────────────────────────────────────────────────────────

_READING = (ast.MatchClause, ast.UnwindClause, ast.WhereClause)


def _outer_prop_refs(merges, ret, outer):
    """`{(var, prop)}` the suffix reads of `outer` variables, or None when it uses one
    other than through a property in a MERGE pattern or the RETURN (a bare node, or
    anything in ON CREATE / ON MATCH, cannot be inlined)."""
    refs = set()
    for m in merges:
        if _vars_in([m.on_create, m.on_match]) & outer:
            return None
        for el in list(m.pattern.nodes) + list(m.pattern.relationships):
            if el.variable in outer:
                return None
            for v in (el.properties or {}).values():
                if not _only_prop_refs(v, outer, refs):
                    return None
    if ret is not None:
        for it in ret.items:
            if not _only_prop_refs(it.expression, outer, refs):
                return None
    return refs


def _only_prop_refs(expr, outer, refs) -> bool:
    for n in _walk(expr):
        if isinstance(n, ast.Variable) and n.name in outer:
            return False
        if isinstance(n, ast.PropertyReference) and n.variable in outer:
            refs.add((n.variable, n.property_name))
    return True


def _first_merge(q):
    for k, part in enumerate(q.query_parts):
        for j, c in enumerate(part.clauses):
            if isinstance(c, ast.MergeClause):
                return k, j
    return None


def _has_updates(parts) -> bool:
    return any(
        isinstance(c, (ast.UpdatingClause, getattr(ast, "ForeachClause", ())))
        for p in parts
        for c in p.clauses
    )


def _graph_vars(parts) -> set:
    """Variables holding a node, relationship or path, followed through WITH renames."""
    out = set()
    for p in parts:
        for n in _walk(p.clauses):
            if isinstance(n, (ast.NodePattern, ast.RelationshipPattern, ast.NamedPath)):
                if n.variable:
                    out.add(n.variable)
        if p.with_clause is not None:
            for it in p.with_clause.items:
                if isinstance(it.expression, ast.Variable) and it.expression.name in out:
                    if it.alias:
                        out.add(it.alias)
    return out


def _item_name(it):
    if it.alias:
        return it.alias
    if isinstance(it.expression, ast.Variable):
        return it.expression.name
    return None


def _has_agg(expr) -> bool:
    return any(isinstance(n, ast.AggregationFunction) for n in _walk(expr))


def _walk(obj):
    if isinstance(obj, (list, tuple)):
        for x in obj:
            yield from _walk(x)
    elif isinstance(obj, dict):
        for x in obj.values():
            yield from _walk(x)
    elif dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        yield obj
        for f in dataclasses.fields(obj):
            yield from _walk(getattr(obj, f.name))


def _vars_in(obj) -> set:
    out = set()
    for n in _walk(obj):
        if isinstance(n, ast.Variable):
            out.add(n.name)
        elif isinstance(n, ast.PropertyReference):
            out.add(n.variable)
    return out


def _pattern_vars(merge) -> set:
    out = set()
    for n in merge.pattern.nodes:
        if n.variable:
            out.add(n.variable)
    for r in merge.pattern.relationships:
        if r.variable:
            out.add(r.variable)
    if merge.path_variable:
        out.add(merge.path_variable)
    return out


def _merges_on(merges, names) -> bool:
    """True when some MERGE property value reads one of `names`."""
    for m in merges:
        for el in list(m.pattern.nodes) + list(m.pattern.relationships):
            for v in (el.properties or {}).values():
                if _vars_in(v) & names:
                    return True
    return False


def _pattern_is_bound(pattern) -> bool:
    """Every property value in the pattern is a literal after substitution."""
    for el in list(pattern.nodes) + list(pattern.relationships):
        for v in (el.properties or {}).values():
            if not isinstance(v, ast.Literal):
                return False
    return True


def _substitute(obj, row):
    """Replace row variables with literals in place (returning the new node)."""
    if isinstance(obj, ast.PropertyReference):
        key = (obj.variable, obj.property_name)
        return ast.Literal(value=row[key]) if key in row else obj
    if isinstance(obj, ast.Variable):
        return ast.Literal(value=row[obj.name]) if obj.name in row else obj
    if isinstance(obj, list):
        return [_substitute(x, row) for x in obj]
    if isinstance(obj, dict):
        return {k: _substitute(v, row) for k, v in obj.items()}
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        for f in dataclasses.fields(obj):
            setattr(obj, f.name, _substitute(getattr(obj, f.name), row))
        if isinstance(obj, ast.FunctionCall):
            return _fold(obj)
        return obj
    return obj


def _fold(fc):
    """Fold `__arith_<op>` over two literals; anything else is returned unchanged."""
    name = fc.function_name
    if not name.startswith("__arith_") or len(fc.arguments) != 2:
        return fc
    a, b = fc.arguments
    if not (isinstance(a, ast.Literal) and isinstance(b, ast.Literal)):
        return fc
    x, y, op = a.value, b.value, name[len("__arith_") :]
    if x is None or y is None:
        return ast.Literal(value=None)
    num = (int, float)
    if isinstance(x, bool) or isinstance(y, bool):
        return fc
    try:
        if op == "+":
            if isinstance(x, str) or isinstance(y, str):
                if isinstance(x, (str, *num)) and isinstance(y, (str, *num)):
                    return ast.Literal(value=f"{x}{y}")
                return fc
            if isinstance(x, num) and isinstance(y, num):
                return ast.Literal(value=x + y)
            return fc
        if not (isinstance(x, num) and isinstance(y, num)):
            return fc
        if op == "-":
            return ast.Literal(value=x - y)
        if op == "*":
            return ast.Literal(value=x * y)
        if op == "/" and isinstance(x, int) and isinstance(y, int):
            if y == 0:
                return fc
            q = abs(x) // abs(y)
            return ast.Literal(value=q if (x >= 0) == (y >= 0) else -q)
        if op == "%" and isinstance(x, int) and isinstance(y, int):
            if y == 0:
                return fc
            r = abs(x) % abs(y)
            return ast.Literal(value=r if x >= 0 else -r)
    except (TypeError, ValueError, OverflowError):
        return fc
    return fc
