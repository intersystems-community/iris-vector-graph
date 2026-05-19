import warnings
from typing import Any, Optional

from iris_vector_graph.cypher.aql import AQLTranslationError
from iris_vector_graph.cypher.aql.ast import (
    AQLDirection, AQLQuery, ForClause, ShortestPathClause, FilterClause,
    LetClause, CollectClause, SortClause, LimitClause, ReturnClause,
    AQLVariable, AQLPropertyAccess, AQLKeyAccess, AQLBindVar, AQLLiteral,
    AQLBinaryOp, AQLUnaryOp, AQLFunctionCall, AQLObjectLiteral, AQLArrayLiteral,
    SortItem,
)


_AQL_FN_MAP = {
    "LENGTH": "size", "CONTAINS": None, "STARTS_WITH": None, "ENDS_WITH": None,
    "TO_STRING": "toString", "TO_NUMBER": "toFloat", "TO_BOOL": "toBoolean",
    "TO_INT": "toInteger", "UPPER": "toUpper", "LOWER": "toLower",
    "TRIM": "trim", "LTRIM": "ltrim", "RTRIM": "rtrim",
    "REGEX_TEST": None, "ABS": "abs", "FLOOR": "floor", "CEIL": "ceil",
    "SQRT": "sqrt", "POW": "sqrt", "MIN": "min", "MAX": "max",
    "SUM": "sum", "AVERAGE": "avg", "COUNT": "size",
    "HAS": None, "NOT_NULL": "coalesce", "CONCAT": None,
    "SUBSTRING": None, "REVERSE": "reverse",
}

_OP_MAP = {
    "==": "=", "!=": "<>", "=~": "=~", "!~": "NOT =~",
    "<": "<", "<=": "<=", ">": ">", ">=": ">=",
    "IN": "IN", "NOT IN": "NOT IN",
    "AND": "AND", "OR": "OR",
    "+": "+", "-": "-", "*": "*", "/": "/", "%": "%",
}


class AQLTranslator:
    def translate_to_cypher(self, aql_query: AQLQuery, bind_vars: dict) -> tuple:
        self._bind_vars = dict(bind_vars)
        self._rev_warned = False

        fc = aql_query.for_clause
        is_sp = isinstance(fc, ShortestPathClause)

        if is_sp:
            cypher = self._translate_shortest_path(fc, aql_query, bind_vars)
        else:
            cypher = self._translate_traversal(fc, aql_query, bind_vars)

        return cypher, self._bind_vars

    def _resolve(self, expr) -> str:
        if isinstance(expr, AQLBindVar):
            return f"${expr.name}"
        if isinstance(expr, AQLLiteral):
            v = expr.value
            if v is None: return "null"
            if isinstance(v, bool): return "true" if v else "false"
            if isinstance(v, str): return f'"{v}"'
            return str(v)
        if isinstance(expr, AQLVariable):
            return expr.name
        if isinstance(expr, AQLKeyAccess):
            obj = self._resolve(expr.object)
            if expr.key == "_rev":
                if not self._rev_warned:
                    warnings.warn(f"v._rev has no IVG equivalent and is dropped from translation",
                                  UserWarning, stacklevel=5)
                    self._rev_warned = True
                return "null"
            return f"{obj}.node_id"
        if isinstance(expr, AQLPropertyAccess):
            obj = self._resolve(expr.object)
            # path sub-arrays
            if isinstance(expr.object, AQLVariable) and obj == getattr(self, "_path_var", None):
                if expr.property == "edges":
                    return f"relationships({obj})"
                if expr.property == "vertices":
                    return f"nodes({obj})"
            return f"{obj}.{expr.property}"
        if isinstance(expr, AQLBinaryOp):
            return self._resolve_binop(expr)
        if isinstance(expr, AQLUnaryOp):
            if expr.operator == "NOT":
                return f"NOT ({self._resolve(expr.operand)})"
            return f"{expr.operator}{self._resolve(expr.operand)}"
        if isinstance(expr, AQLFunctionCall):
            return self._resolve_function(expr)
        if isinstance(expr, AQLObjectLiteral):
            parts = ", ".join(f"{k}: {self._resolve(v)}" for k, v in expr.fields.items())
            return f"{{{parts}}}"
        if isinstance(expr, AQLArrayLiteral):
            parts = ", ".join(self._resolve(i) for i in expr.items)
            return f"[{parts}]"
        return str(expr)

    def _resolve_binop(self, expr: AQLBinaryOp) -> str:
        left = self._resolve(expr.left)
        op = _OP_MAP.get(expr.operator, expr.operator)
        if expr.operator == "CONTAINS":
            return f"{left} CONTAINS {self._resolve(expr.right)}"
        if expr.operator == "STARTS_WITH":
            return f"{left} STARTS WITH {self._resolve(expr.right)}"
        if expr.operator == "ENDS_WITH":
            return f"{left} ENDS WITH {self._resolve(expr.right)}"
        right = self._resolve(expr.right)
        if expr.operator == "==" and right == "null":
            return f"{left} IS NULL"
        if expr.operator == "!=" and right == "null":
            return f"{left} IS NOT NULL"
        return f"{left} {op} {right}"

    def _resolve_function(self, fn: AQLFunctionCall) -> str:
        name = fn.name.upper()
        if name in ("K_SHORTEST_PATHS",):
            raise AQLTranslationError(f"{name} not supported in Spec 157, await Spec 158", name)
        if name == "SEARCH":
            raise AQLTranslationError("SEARCH not supported in Spec 157, await Spec 159", "SEARCH")
        if name == "CONTAINS" and len(fn.args) == 2:
            return f"{self._resolve(fn.args[0])} CONTAINS {self._resolve(fn.args[1])}"
        if name == "STARTS_WITH" and len(fn.args) == 2:
            return f"{self._resolve(fn.args[0])} STARTS WITH {self._resolve(fn.args[1])}"
        if name == "ENDS_WITH" and len(fn.args) == 2:
            return f"{self._resolve(fn.args[0])} ENDS WITH {self._resolve(fn.args[1])}"
        if name == "REGEX_TEST" and len(fn.args) == 2:
            return f"{self._resolve(fn.args[0])} =~ {self._resolve(fn.args[1])}"
        if name == "HAS" and len(fn.args) == 2:
            obj = self._resolve(fn.args[0])
            key = fn.args[1].value if isinstance(fn.args[1], AQLLiteral) else self._resolve(fn.args[1])
            return f"{obj}.{key} IS NOT NULL"
        if name == "LENGTH" and len(fn.args) == 1:
            arg = fn.args[0]
            # LENGTH(p.edges) → length(p)
            if isinstance(arg, AQLPropertyAccess) and arg.property == "edges":
                return f"length({self._resolve(arg.object)})"
            return f"size({self._resolve(arg)})"
        cypher_name = _AQL_FN_MAP.get(name)
        if cypher_name is None and name not in _AQL_FN_MAP:
            raise AQLTranslationError(f"Unsupported AQL function: {name}", name)
        cypher_name = cypher_name or name.lower()
        args = ", ".join(self._resolve(a) for a in fn.args)
        return f"{cypher_name}({args})"

    def _build_where_conditions(self, fc: ForClause, filters: list, bind_vars: dict) -> list:
        conditions = []
        start = self._resolve(fc.start_expr)
        # For bind vars in start position, use $param syntax for Cypher
        if start.startswith("$"):
            pass  # already correct
        elif not (start.startswith('"') or start.startswith("'") or start[0].isdigit()):
            # bare identifier → wrap in quotes as literal or treat as param
            pass
        conditions.append(f"start.node_id = {start}")
        for f in filters:
            conditions.append(self._resolve(f.condition))
        return conditions

    def _translate_traversal(self, fc: ForClause, q: AQLQuery, bind_vars: dict) -> str:
        self._path_var = fc.path_var

        if fc.is_graph:
            warnings.warn(
                f"GRAPH '{fc.graph_or_collections[0] if fc.graph_or_collections else ''}' semantics "
                f"are not enforced by IVG — use collection list syntax to scope edge types",
                UserWarning, stacklevel=6
            )
            rel_type = ""
        else:
            colls = fc.graph_or_collections
            rel_type = (":" + "|".join(colls)) if colls else ""

        dir_map = {AQLDirection.OUTBOUND: ("-", "->"), AQLDirection.INBOUND: ("<-", "-"),
                   AQLDirection.ANY: ("-", "-")}
        ldir, rdir = dir_map[fc.direction]
        hops = f"*{fc.min_depth}..{fc.max_depth}" if fc.min_depth != fc.max_depth else f"*{fc.min_depth}..{fc.max_depth}"

        edge_part = f"[{fc.edge_var or ''}{rel_type}{hops}]" if fc.edge_var else f"[{rel_type}{hops}]"
        pattern = f"(start){ldir}{edge_part}{rdir}({fc.vertex_var})"
        if fc.path_var:
            match_line = f"MATCH {fc.path_var} = {pattern}"
        else:
            match_line = f"MATCH {pattern}"

        # WHERE
        conditions = self._build_where_conditions(fc, q.filter_clauses, bind_vars)
        where_line = "WHERE " + " AND ".join(conditions) if conditions else ""

        # WITH (LET clauses)
        with_parts = []
        for let in (q.let_clauses or []):
            with_parts.append(f"{self._resolve(let.value)} AS {let.variable}")

        # RETURN / COLLECT
        if q.collect_clause:
            return_line = self._build_collect_return(q.collect_clause)
        elif q.return_clause:
            return_line = self._build_return(q.return_clause)
        else:
            return_line = "RETURN *"

        # ORDER BY
        order_line = ""
        if q.sort_clause:
            items = []
            for item in q.sort_clause.items:
                expr = self._resolve(item.expression)
                items.append(f"{expr} {'ASC' if item.ascending else 'DESC'}")
            order_line = "ORDER BY " + ", ".join(items)

        # SKIP / LIMIT
        skip_line = ""
        limit_line = ""
        if q.limit_clause:
            if q.limit_clause.offset:
                skip_line = f"SKIP {q.limit_clause.offset}"
            limit_line = f"LIMIT {q.limit_clause.count}"

        parts = [match_line]
        if where_line: parts.append(where_line)
        if with_parts: parts.append("WITH *, " + ", ".join(with_parts))
        parts.append(return_line)
        if order_line: parts.append(order_line)
        if skip_line: parts.append(skip_line)
        if limit_line: parts.append(limit_line)

        return "\n".join(p for p in parts if p)

    def _build_return(self, rc: ReturnClause) -> str:
        prefix = "RETURN DISTINCT " if rc.distinct else "RETURN "
        expr = rc.expression
        if isinstance(expr, AQLObjectLiteral):
            items = []
            for k, v in expr.fields.items():
                items.append(f"{self._resolve(v)} AS {k}")
            return prefix + ", ".join(items)
        if isinstance(expr, AQLKeyAccess):
            safe = expr.key.lstrip('_')
            return f"{prefix}{self._resolve(expr.object)}.node_id AS {expr.object.name if isinstance(expr.object, AQLVariable) else 'id'}_{safe}"
        return prefix + self._resolve(expr)

    def _build_collect_return(self, cc: CollectClause) -> str:
        parts = []
        for var, expr in (cc.assignments or []):
            parts.append(f"{self._resolve(expr)} AS {var}")
        if cc.with_count_into:
            parts.append(f"count(*) AS {cc.with_count_into}")
        return "RETURN " + ", ".join(parts) if parts else "RETURN count(*)"

    def _translate_shortest_path(self, fc: ShortestPathClause, q: AQLQuery, bind_vars: dict) -> str:
        if fc.is_graph:
            warnings.warn(
                f"GRAPH '{fc.graph_or_collections[0] if fc.graph_or_collections else ''}' semantics not enforced",
                UserWarning, stacklevel=6
            )
        dir_map = {AQLDirection.OUTBOUND: ("-", "->"), AQLDirection.INBOUND: ("<-", "-"),
                   AQLDirection.ANY: ("-", "-")}
        ldir, rdir = dir_map[fc.direction]

        start_ref = self._resolve(fc.start_expr)
        end_ref = self._resolve(fc.end_expr)

        path_var = q.for_clause.edge_var  # edge_var reused as path in SP
        pv = getattr(fc, 'path_var', None) or q.for_clause.edge_var or "p"
        match_line = f"MATCH {pv} = shortestPath((from){ldir}[*]{rdir}(to))"
        where_line = f"WHERE from.node_id = {start_ref} AND to.node_id = {end_ref}"

        if q.return_clause:
            return_line = self._build_return(q.return_clause)
        else:
            return_line = f"RETURN {pv}"

        return "\n".join([match_line, where_line, return_line])
