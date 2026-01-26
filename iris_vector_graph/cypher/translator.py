"""
Cypher-to-SQL Translation Artifacts

Classes for managing SQL generation from Cypher AST.
Supports multi-stage queries via Common Table Expressions (CTEs).
"""

from dataclasses import dataclass, field
from typing import List, Any, Dict, Optional, Union
import logging
import json
from . import ast

logger = logging.getLogger(__name__)

@dataclass
class QueryMetadata:
    """Query execution metadata tracking."""
    estimated_rows: Optional[int] = None
    index_usage: List[str] = field(default_factory=list)
    optimization_applied: List[str] = field(default_factory=list)
    complexity_score: Optional[float] = None


@dataclass
class SQLQuery:
    """Generated SQL query with parameters and metadata."""
    sql: Union[str, List[str]]
    parameters: List[List[Any]] = field(default_factory=list)
    query_metadata: QueryMetadata = field(default_factory=QueryMetadata)
    is_transactional: bool = False


class TranslationContext:
    """Stateful context for SQL generation across multiple query stages."""
    
    def __init__(self, parent: Optional['TranslationContext'] = None):
        self.variable_aliases: Dict[str, str] = {}
        if parent is not None:
            self.variable_aliases = parent.variable_aliases.copy()
        
        self.select_items: List[str] = []
        self.from_clauses: List[str] = []
        self.join_clauses: List[str] = []
        self.where_conditions: List[str] = []
        self.group_by_items: List[str] = []
        
        self.select_params: List[Any] = []
        self.join_params: List[Any] = []
        self.where_params: List[Any] = []
        
        self.dml_statements: List[tuple[str, List[Any]]] = []
        
        self.all_stage_params: List[Any] = [] if parent is None else parent.all_stage_params
        self._alias_counter: int = 0 if parent is None else parent._alias_counter
        self.stages: List[str] = [] if parent is None else parent.stages
        self.input_params: Dict[str, Any] = {} if parent is None else parent.input_params

    def next_alias(self, prefix: str = "t") -> str:
        alias = f"{prefix}{self._alias_counter}"
        self._alias_counter += 1
        return alias

    def register_variable(self, variable: str, prefix: str = "n") -> str:
        if variable not in self.variable_aliases:
            self.variable_aliases[variable] = self.next_alias(prefix)
        return self.variable_aliases[variable]

    def add_select_param(self, value: Any) -> str:
        self.select_params.append(value); return "?"

    def add_join_param(self, value: Any) -> str:
        self.join_params.append(value); return "?"

    def add_where_param(self, value: Any) -> str:
        self.where_params.append(value); return "?"

    def build_stage_sql(self, distinct: bool = False, select_override: Optional[str] = None) -> tuple[str, List[Any]]:
        select = select_override if select_override else f"SELECT {'DISTINCT ' if distinct else ''}{', '.join(self.select_items)}"
        parts = [select]
        if self.from_clauses: parts.append(f"FROM {', '.join(self.from_clauses)}")
        if self.join_clauses: parts.extend(self.join_clauses)
        if self.where_conditions: parts.append(f"WHERE {' AND '.join(self.where_conditions)}")
        if self.group_by_items: parts.append(f"GROUP BY {', '.join(self.group_by_items)}")
        
        sql = "\n".join(parts)
        params = (self.select_params if not select_override else []) + self.join_params + self.where_params
        return sql, params

    def add_dml(self, sql: str, params: List[Any]):
        self.dml_statements.append((sql, params))


def translate_to_sql(cypher_query: ast.CypherQuery, params: Optional[Dict[str, Any]] = None) -> SQLQuery:
    context = TranslationContext()
    context.input_params = params or {}
    metadata = QueryMetadata()
    is_transactional = False

    for i, part in enumerate(cypher_query.query_parts):
        context.select_items, context.from_clauses, context.join_clauses = [], [], []
        context.where_conditions, context.group_by_items = [], []
        context.select_params, context.join_params, context.where_params = [], [], []
        if i > 0: context.from_clauses.append(f"Stage{i}")
        for clause in part.clauses:
            if isinstance(clause, ast.MatchClause): translate_match_clause(clause, context, metadata)
            elif isinstance(clause, ast.UnwindClause): translate_unwind_clause(clause, context)
            elif isinstance(clause, ast.UpdatingClause):
                is_transactional = True
                translate_updating_clause(clause, context, metadata)
            elif isinstance(clause, ast.WhereClause): translate_where_clause(clause, context)
        if part.with_clause:
            translate_with_clause(part.with_clause, context)
            sql, stage_params = context.build_stage_sql(part.with_clause.distinct)
            context.all_stage_params.extend(stage_params)
            context.stages.append(f"Stage{i+1} AS (\n{sql}\n)")
            new_aliases = {}
            for item in part.with_clause.items:
                alias = item.alias or (item.expression.name if isinstance(item.expression, ast.Variable) else None)
                if alias: new_aliases[alias] = f"Stage{i+1}"
            context.variable_aliases = new_aliases

    context.select_items, context.select_params = [], []
    if context.stages:
        context.from_clauses, context.join_clauses, context.join_params = [f"Stage{len(context.stages)}"], [], []
        context.where_conditions, context.where_params = [], []
    if cypher_query.return_clause: translate_return_clause(cypher_query.return_clause, context)
    
    if is_transactional:
        stmts, all_params = [], []
        for s, p in context.dml_statements:
            stmts.append(s); all_params.append(p)
        if cypher_query.return_clause:
            sql, p = context.build_stage_sql(cypher_query.return_clause.distinct)
            sql = apply_pagination(sql, cypher_query, context)
            if context.stages:
                sql = "WITH " + ",\n".join(context.stages) + "\n" + sql
                all_params.append(context.all_stage_params + p)
            else: all_params.append(p)
            stmts.append(sql)
        return SQLQuery(sql=stmts, parameters=all_params, query_metadata=metadata, is_transactional=True)
    else:
        sql, p = context.build_stage_sql(cypher_query.return_clause.distinct if cypher_query.return_clause else False)
        sql = apply_pagination(sql, cypher_query, context)
        if context.stages:
            sql = "WITH " + ",\n".join(context.stages) + "\n" + sql
            return SQLQuery(sql=sql, parameters=[context.all_stage_params + p], query_metadata=metadata)
        return SQLQuery(sql=sql, parameters=[p], query_metadata=metadata)


def apply_pagination(sql: str, query: ast.CypherQuery, context: TranslationContext) -> str:
    if query.order_by_clause:
        items = []
        for item in query.order_by_clause.items:
            expr = translate_expression(item.expression, context, segment="where")
            items.append(f"{expr} {'ASC' if item.ascending else 'DESC'}")
        sql += f"\nORDER BY {', '.join(items)}"
    if query.limit is not None: sql += f"\nLIMIT {query.limit}"
    if query.skip is not None: sql += f"\nOFFSET {query.skip}"
    return sql


def translate_updating_clause(upd, context, metadata):
    if isinstance(upd, ast.CreateClause): translate_create_clause(upd, context, metadata)
    elif isinstance(upd, ast.DeleteClause): translate_delete_clause(upd, context, metadata)
    elif isinstance(upd, ast.MergeClause): translate_merge_clause(upd, context, metadata)
    elif isinstance(upd, ast.SetClause): translate_set_clause(upd, context, metadata)
    elif isinstance(upd, ast.RemoveClause): translate_remove_clause(upd, context, metadata)


def translate_unwind_clause(unwind, context):
    expr = translate_expression(unwind.expression, context, segment="join")
    if isinstance(unwind.expression, ast.Variable) and unwind.expression.name in context.input_params:
        val = context.input_params[unwind.expression.name]
        if isinstance(val, list): context.join_params[-1] = json.dumps(val)
    alias = context.register_variable(unwind.alias, prefix="u")
    context.from_clauses.append(f"JSON_TABLE({expr}, '$[*]' COLUMNS ({unwind.alias} VARCHAR(1000) PATH '$')) {alias}")


def translate_create_clause(create, context, metadata):
    for node in create.pattern.nodes:
        if node.variable and node.variable in context.variable_aliases: continue
        node_id_expr = node.properties.get("id") or node.properties.get("node_id")
        if node_id_expr is None: raise ValueError("CREATE node requires an 'id' property")
        
        if isinstance(node_id_expr, ast.Variable):
            var_alias = context.variable_aliases.get(node_id_expr.name)
            if not var_alias: raise ValueError(f"Undefined: {node_id_expr.name}")
            sql, p = context.build_stage_sql(select_override=f"SELECT {var_alias}.{node_id_expr.name} AS node_id")
            context.add_dml(f"INSERT INTO nodes (node_id) SELECT t.node_id FROM ({sql}) AS t WHERE NOT EXISTS (SELECT 1 FROM nodes WHERE node_id = t.node_id)", p)
            for label in node.labels:
                context.add_dml(f"INSERT INTO rdf_labels (s, label) SELECT t.node_id, ? FROM ({sql}) AS t WHERE NOT EXISTS (SELECT 1 FROM rdf_labels WHERE s = t.node_id AND label = ?)", [label] + p + [label])
        else:
            node_id = node_id_expr.value if isinstance(node_id_expr, ast.Literal) else node_id_expr
            context.add_dml("INSERT INTO nodes (node_id) SELECT ? WHERE NOT EXISTS (SELECT 1 FROM nodes WHERE node_id = ?)", [node_id, node_id])
            for label in node.labels:
                context.add_dml("INSERT INTO rdf_labels (s, label) SELECT ?, ? WHERE NOT EXISTS (SELECT 1 FROM rdf_labels WHERE s = ? AND label = ?)", [node_id, label, node_id, label])
            for k, v in node.properties.items():
                if k not in ("id", "node_id"):
                    val = v.value if isinstance(v, ast.Literal) else v
                    context.add_dml("INSERT INTO rdf_props (s, \"key\", val) SELECT ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM rdf_props WHERE s = ? AND \"key\" = ?)", [node_id, k, val, node_id, k])
        if node.variable: context.register_variable(node.variable)
    
    for i, rel in enumerate(create.pattern.relationships):
        source_node, target_node = create.pattern.nodes[i], create.pattern.nodes[i+1]
        s_id_expr = source_node.properties.get("id") or source_node.properties.get("node_id")
        t_id_expr = target_node.properties.get("id") or target_node.properties.get("node_id")
        s_id = s_id_expr.value if isinstance(s_id_expr, ast.Literal) else s_id_expr if not isinstance(s_id_expr, ast.Variable) else None
        t_id = t_id_expr.value if isinstance(t_id_expr, ast.Literal) else t_id_expr if not isinstance(t_id_expr, ast.Variable) else None
        if s_id and t_id:
            for rt in rel.types: context.add_dml("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)", [s_id, rt, t_id])
        else:
            s_alias = context.variable_aliases.get(source_node.variable) if source_node.variable else None
            t_alias = context.variable_aliases.get(target_node.variable) if target_node.variable else None
            s_expr, s_p = ("?", [s_id]) if s_id else (f"{s_alias}.{source_node.variable}" if s_alias and s_alias.startswith('Stage') else f"{s_alias}.node_id", [])
            t_expr, t_p = ("?", [t_id]) if t_id else (f"{t_alias}.{target_node.variable}" if t_alias and t_alias.startswith('Stage') else f"{t_alias}.node_id", [])
            for rt in rel.types:
                sql, p = context.build_stage_sql(select_override=f"SELECT {s_expr}, ?, {t_expr}")
                context.add_dml(f"INSERT INTO rdf_edges (s, p, o_id) {sql}", s_p + [rt] + t_p + p)


def translate_delete_clause(delete, context, metadata):
    for var in delete.expressions:
        alias = context.variable_aliases.get(var.name)
        if not alias: raise ValueError(f"Undefined: {var.name}")
        subquery, subparams = context.build_stage_sql(select_override=f"SELECT {alias}.node_id")
        if delete.detach: context.add_dml(f"DELETE FROM rdf_edges WHERE s IN ({subquery}) OR o_id IN ({subquery})", subparams + subparams)
        if not alias.startswith('e'):
            context.add_dml(f"DELETE FROM rdf_labels WHERE s IN ({subquery})", subparams)
            context.add_dml(f"DELETE FROM rdf_props WHERE s IN ({subquery})", subparams)
            context.add_dml(f"DELETE FROM nodes WHERE node_id IN ({subquery})", subparams)
        else: context.add_dml(f"DELETE FROM rdf_edges WHERE s = (SELECT s FROM rdf_edges {alias}) AND p = (SELECT p FROM rdf_edges {alias}) AND o_id = (SELECT o_id FROM rdf_edges {alias})", [])


def translate_merge_clause(merge, context, metadata):
    translate_create_clause(ast.CreateClause(merge.pattern), context, metadata)
    for action, is_create in [(merge.on_create, True), (merge.on_match, False)]:
        if action:
            for item in action.items:
                if isinstance(item, ast.SetItem) and isinstance(item.expression, ast.PropertyReference):
                    node_id = context.variable_aliases.get(item.expression.variable)
                    k, v = item.expression.property_name, item.value
                    val = v.value if isinstance(v, ast.Literal) else v
                    if is_create: context.add_dml(f"INSERT INTO rdf_props (s, \"key\", val) SELECT node_id, ?, ? FROM nodes WHERE node_id = ? AND NOT EXISTS (SELECT 1 FROM rdf_props WHERE s = ? AND \"key\" = ?)", [k, val, node_id, node_id, k])
                    else: context.add_dml(f"UPDATE rdf_props SET val = ? WHERE s = ? AND \"key\" = ?", [val, node_id, k])


def translate_set_clause(set_cl, context, metadata):
    for item in set_cl.items:
        if isinstance(item.expression, ast.PropertyReference):
            alias, k, v = context.variable_aliases.get(item.expression.variable), item.expression.property_name, item.value
            val = v.value if isinstance(v, ast.Literal) else v
            subquery, subparams = context.build_stage_sql(select_override=f"SELECT {alias}.node_id")
            context.add_dml(f"UPDATE rdf_props SET val = ? WHERE s IN ({subquery}) AND \"key\" = ?", [val] + subparams + [k])
            context.add_dml(f"INSERT INTO rdf_props (s, \"key\", val) SELECT node_id, ?, ? FROM nodes WHERE node_id IN ({subquery}) AND NOT EXISTS (SELECT 1 FROM rdf_props WHERE s = nodes.node_id AND \"key\" = ?)", [k, val] + subparams + [k])
        elif isinstance(item.expression, ast.Variable):
            alias, label = context.variable_aliases.get(item.expression.name), str(item.value.value if isinstance(item.value, ast.Literal) else item.value)
            subquery, subparams = context.build_stage_sql(select_override=f"SELECT {alias}.node_id")
            context.add_dml(f"INSERT INTO rdf_labels (s, label) SELECT node_id, ? FROM nodes WHERE node_id IN ({subquery}) AND NOT EXISTS (SELECT 1 FROM rdf_labels WHERE s = nodes.node_id AND label = ?)", [label] + subparams + [label])


def translate_remove_clause(remove, context, metadata):
    for item in remove.items:
        if isinstance(item.expression, ast.PropertyReference):
            alias, k = context.variable_aliases.get(item.expression.variable), item.expression.property_name
            subquery, subparams = context.build_stage_sql(select_override=f"SELECT {alias}.node_id")
            context.add_dml(f"DELETE FROM rdf_props WHERE s IN ({subquery}) AND \"key\" = ?", subparams + [k])


def translate_match_clause(match_clause, context, metadata):
    for pattern in match_clause.patterns:
        if not pattern.nodes: continue
        translate_node_pattern(pattern.nodes[0], context, metadata, optional=match_clause.optional)
        for i, rel in enumerate(pattern.relationships):
            translate_relationship_pattern(rel, pattern.nodes[i], pattern.nodes[i+1], context, metadata, optional=match_clause.optional)
            translate_node_pattern(pattern.nodes[i+1], context, metadata, optional=match_clause.optional)


def translate_node_pattern(node, context, metadata, optional=False):
    if node.variable and node.variable in context.variable_aliases:
        if context.variable_aliases[node.variable].startswith('Stage'): return
    alias = context.register_variable(node.variable) if node.variable else context.next_alias("n")
    jt = "LEFT OUTER JOIN" if optional else "JOIN"
    if not context.from_clauses: context.from_clauses.append(f"nodes {alias}")
    elif f"nodes {alias}" not in context.from_clauses and not any(alias in j for j in context.join_clauses): context.join_clauses.append(f"CROSS JOIN nodes {alias}")
    for label in node.labels:
        l_alias = context.next_alias("l")
        context.join_clauses.append(f"{jt} rdf_labels {l_alias} ON {l_alias}.s = {alias}.node_id AND {l_alias}.label = {context.add_join_param(label)}")
        if not optional: context.where_conditions.append(f"{l_alias}.s IS NOT NULL")
    for k, v in node.properties.items():
        val = v.value if isinstance(v, ast.Literal) else v
        if k in ("node_id", "id"): context.where_conditions.append(f"{alias}.node_id = {context.add_where_param(val)}")
        else:
            p_alias = context.next_alias("p")
            context.join_clauses.append(f"{jt} rdf_props {p_alias} ON {p_alias}.s = {alias}.node_id AND {p_alias}.key = {context.add_join_param(k)}")
            if optional: context.where_conditions.append(f"({p_alias}.s IS NULL OR {p_alias}.val = {context.add_where_param(val)})")
            else: context.where_conditions.append(f"{p_alias}.val = {context.add_where_param(val)}")


def translate_relationship_pattern(rel, source_node, target_node, context, metadata, optional=False):
    source_alias, target_alias = context.variable_aliases[source_node.variable], context.register_variable(target_node.variable)
    edge_alias = context.register_variable(rel.variable, prefix="e") if rel.variable else context.next_alias("e")
    s_col = source_node.variable if source_alias.startswith('Stage') else "node_id"
    t_col = target_node.variable if target_alias.startswith('Stage') else "node_id"
    jt = "LEFT OUTER JOIN" if optional else "JOIN"
    if rel.direction == ast.Direction.OUTGOING:
        edge_cond, target_on = f"{edge_alias}.s = {source_alias}.{s_col}", f"{target_alias}.{t_col} = {edge_alias}.o_id"
    elif rel.direction == ast.Direction.INCOMING:
        edge_cond, target_on = f"{edge_alias}.o_id = {source_alias}.{s_col}", f"{target_alias}.{t_col} = {edge_alias}.s"
    else:
        edge_cond = f"({edge_alias}.s = {source_alias}.{s_col} OR {edge_alias}.o_id = {source_alias}.{s_col})"
        target_on = f"({target_alias}.{t_col} = {edge_alias}.o_id OR {target_alias}.{t_col} = {edge_alias}.s)"
    if rel.types:
        if len(rel.types) == 1: edge_cond += f" AND {edge_alias}.p = {context.add_join_param(rel.types[0])}"
        else: edge_cond += f" AND {edge_alias}.p IN ({', '.join([context.add_join_param(t) for t in rel.types])})"
    context.join_clauses.append(f"{jt} rdf_edges {edge_alias} ON {edge_cond}")
    if not target_alias.startswith('Stage'): context.join_clauses.append(f"{jt} nodes {target_alias} ON {target_on}")
    else: context.where_conditions.append(target_on)


def translate_where_clause(where, context):
    context.where_conditions.append(translate_boolean_expression(where.expression, context))


def translate_boolean_expression(expr, context) -> str:
    if not isinstance(expr, ast.BooleanExpression): return translate_expression(expr, context, segment="where")
    op = expr.operator
    if op == ast.BooleanOperator.AND: return "(" + " AND ".join(translate_boolean_expression(o, context) for o in expr.operands) + ")"
    if op == ast.BooleanOperator.OR: return "(" + " OR ".join(translate_boolean_expression(o, context) for o in expr.operands) + ")"
    if op == ast.BooleanOperator.NOT: return f"NOT ({translate_boolean_expression(expr.operands[0], context)})"
    left = translate_expression(expr.operands[0], context, segment="where")
    if op == ast.BooleanOperator.IS_NULL: return f"{left} IS NULL"
    if op == ast.BooleanOperator.IS_NOT_NULL: return f"{left} IS NOT NULL"
    right = translate_expression(expr.operands[1], context, segment="where")
    if op == ast.BooleanOperator.EQUALS: return f"{left} = {right}"
    if op == ast.BooleanOperator.NOT_EQUALS: return f"{left} <> {right}"
    if op == ast.BooleanOperator.LESS_THAN: return f"{left} < {right}"
    if op == ast.BooleanOperator.LESS_THAN_OR_EQUAL: return f"{left} <= {right}"
    if op == ast.BooleanOperator.GREATER_THAN: return f"{left} > {right}"
    if op == ast.BooleanOperator.GREATER_THAN_OR_EQUAL: return f"{left} >= {right}"
    if op == ast.BooleanOperator.STARTS_WITH: return f"{left} LIKE ({right} || '%')"
    if op == ast.BooleanOperator.ENDS_WITH: return f"{left} LIKE ('%' || {right})"
    if op == ast.BooleanOperator.CONTAINS: return f"{left} LIKE ('%' || {right} || '%')"
    if op == ast.BooleanOperator.IN: return f"{left} IN {right}"
    raise ValueError(f"Unsupported operator: {op}")


def translate_expression(expr, context, segment="select") -> str:
    if isinstance(expr, ast.PropertyReference):
        alias = context.variable_aliases.get(expr.variable)
        if not alias: raise ValueError(f"Undefined: {expr.variable}")
        if alias.startswith('Stage'):
            if expr.property_name in ("node_id", "id"): return f"{alias}.{expr.variable}"
            return f"{alias}.{expr.variable}_{expr.property_name}"
        if expr.property_name in ("node_id", "id"): return f"{alias}.node_id"
        p_alias = context.next_alias("p")
        context.join_clauses.append(f"JOIN rdf_props {p_alias} ON {p_alias}.s = {alias}.node_id AND {p_alias}.key = {context.add_join_param(expr.property_name)}")
        return f"{p_alias}.val"
    if isinstance(expr, ast.Variable):
        alias = context.variable_aliases.get(expr.name)
        if not alias:
            if expr.name in context.input_params:
                v = context.input_params[expr.name]
                if segment == "select": return context.add_select_param(v)
                if segment == "join": return context.add_join_param(v)
                return context.add_where_param(v)
            raise ValueError(f"Undefined: {expr.name}")
        if alias.startswith('Stage'): return f"{alias}.{expr.name}"
        return f"{alias}.p" if alias.startswith('e') else f"{alias}.node_id"
    if isinstance(expr, ast.Literal):
        v = expr.value
        if segment == "select": return context.add_select_param(v)
        if segment == "join": return context.add_join_param(v)
        return context.add_where_param(v)
    if isinstance(expr, ast.AggregationFunction):
        arg = translate_expression(expr.argument, context, segment=segment) if expr.argument else "*"
        fn = "JSON_ARRAYAGG" if expr.function_name.upper() == "COLLECT" else expr.function_name.upper()
        return f"{fn}({'DISTINCT ' if expr.distinct else ''}{arg})"
    if isinstance(expr, ast.FunctionCall):
        fn = expr.function_name.lower()
        if fn in ("shortestpath", "allshortestpaths"):
            from .algorithms.paths import generate_shortest_path_sql
            args = [translate_expression(arg, context, segment=segment) for arg in expr.arguments]
            s_id, t_id = (args[0] if len(args) > 0 else "NULL"), (args[1] if len(args) > 1 else "NULL")
            inner = generate_shortest_path_sql("?", "?", 10, fn == "allshortestpaths")
            return f"(SELECT TOP 1 path FROM ({inner}) WHERE 1=1)"
        fn, args = expr.function_name.lower(), [translate_expression(arg, context, segment=segment) for arg in expr.arguments]
        if fn in ("id", "type"): return args[0] if args else "NULL"
        if fn == "labels": return f"(SELECT JSON_ARRAYAGG(label) FROM rdf_labels WHERE s = {args[0] if args else 'NULL'})"
        return f"{fn.upper()}({', '.join(args)})"
    return "NULL"


def translate_return_clause(ret, context):
    has_agg = any(isinstance(i.expression, ast.AggregationFunction) for i in ret.items)
    for item in ret.items:
        sql = translate_expression(item.expression, context, segment="select")
        alias = item.alias
        if alias is None:
            if isinstance(item.expression, ast.PropertyReference): alias = f"{item.expression.variable}_{item.expression.property_name}"
            elif isinstance(item.expression, ast.Variable): alias = item.expression.name
            elif isinstance(item.expression, (ast.AggregationFunction, ast.FunctionCall)): alias = f"{item.expression.function_name}_res"
        if alias: context.select_items.append(f"{sql} AS {alias.replace('.', '_')}")
        else: context.select_items.append(sql)
        if has_agg and not isinstance(item.expression, ast.AggregationFunction): context.group_by_items.append(sql)


def translate_with_clause(with_clause, context):
    has_agg = any(isinstance(i.expression, ast.AggregationFunction) for i in with_clause.items)
    for item in with_clause.items:
        sql = translate_expression(item.expression, context, segment="select")
        alias = item.alias
        if alias is None:
            if isinstance(item.expression, ast.PropertyReference): alias = f"{item.expression.variable}_{item.expression.property_name}"
            elif isinstance(item.expression, ast.Variable): alias = item.expression.name
            elif isinstance(item.expression, ast.AggregationFunction): alias = f"{item.expression.function_name}"
        if alias is None: alias = context.next_alias("v")
        context.select_items.append(f"{sql} AS {alias.replace('.', '_')}")
        if has_agg and not isinstance(item.expression, ast.AggregationFunction): context.group_by_items.append(sql)
    if with_clause.where_clause: context.where_conditions.append(translate_boolean_expression(with_clause.where_clause.expression, context))
