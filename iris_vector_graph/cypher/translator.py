"""
Cypher-to-SQL Translation Artifacts

Classes for managing SQL generation from Cypher AST.
Supports multi-stage queries via Common Table Expressions (CTEs).
"""

from dataclasses import dataclass, field
from typing import List, Any, Dict, Optional, Union
import logging
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
    sql: str
    parameters: List[Any] = field(default_factory=list)
    query_metadata: QueryMetadata = field(default_factory=QueryMetadata)


class TranslationContext:
    """Stateful context for SQL generation across multiple query stages."""
    
    def __init__(self, parent: Optional['TranslationContext'] = None):
        # Variable to table alias mapping (e.g., 'p' -> 'n0')
        self.variable_aliases: Dict[str, str] = {}
        if parent is not None:
            self.variable_aliases = parent.variable_aliases.copy()
        
        # SQL clauses for CURRENT stage
        self.select_items: List[str] = []
        self.from_clauses: List[str] = []
        self.join_clauses: List[str] = []
        self.where_conditions: List[str] = []
        self.group_by_items: List[str] = []
        
        # Shared state across all stages
        self.parameters: List[Any] = [] if parent is None else parent.parameters
        self._alias_counter: int = 0 if parent is None else parent._alias_counter
        self.stages: List[str] = [] if parent is None else parent.stages

    def next_alias(self, prefix: str = "t") -> str:
        alias = f"{prefix}{self._alias_counter}"
        self._alias_counter += 1
        return alias

    def register_variable(self, variable: str, prefix: str = "n") -> str:
        if variable not in self.variable_aliases:
            self.variable_aliases[variable] = self.next_alias(prefix)
        return self.variable_aliases[variable]

    def add_parameter(self, value: Any) -> str:
        self.parameters.append(value)
        return "?"

    def build_stage_sql(self, distinct: bool = False) -> str:
        """Build SQL for a single stage (to be used in a CTE or final SELECT)"""
        parts = []
        distinct_kw = "DISTINCT " if distinct else ""
        select_clause = f"SELECT {distinct_kw}{', '.join(self.select_items)}"
        parts.append(select_clause)
        
        if self.from_clauses:
            parts.append(f"FROM {', '.join(self.from_clauses)}")
        
        if self.join_clauses:
            parts.extend(self.join_clauses)
            
        if self.where_conditions:
            parts.append(f"WHERE {' AND '.join(self.where_conditions)}")
            
        if self.group_by_items:
            parts.append(f"GROUP BY {', '.join(self.group_by_items)}")
            
        return "\n".join(parts)


def translate_to_sql(cypher_query: ast.CypherQuery) -> SQLQuery:
    """Translate enriched Cypher AST to SQLQuery using CTEs for multi-stage queries."""
    context = TranslationContext()
    metadata = QueryMetadata()

    # 1. Translate each QueryPart (stage)
    for i, part in enumerate(cypher_query.query_parts):
        # Reset stage-specific items but keep variable aliases and parameters
        context.select_items = []
        context.from_clauses = []
        context.join_clauses = []
        context.where_conditions = []
        context.group_by_items = []
        
        # If not the first stage, the FROM clause is the previous stage
        if i > 0:
            prev_stage = f"Stage{i}"
            context.from_clauses.append(prev_stage)
            # In subsequent stages, variables refer to columns in the CTE
            # We don't need nodes JOINs for variables carried over in WITH
            # but we do need them for NEW variables in MATCH.
        
        for match_clause in part.match_clauses:
            translate_match_clause(match_clause, context, metadata)
            
        if part.where_clause:
            translate_where_clause(part.where_clause, context)
            
        if part.with_clause:
            translate_with_clause(part.with_clause, context)
            stage_sql = context.build_stage_sql(part.with_clause.distinct)
            context.stages.append(f"Stage{i+1} AS (\n{stage_sql}\n)")
            
            # CRITICAL: Strict variable scoping.
            # Only variables explicitly named in WITH are carried over.
            new_aliases = {}
            for item in part.with_clause.items:
                alias = item.alias or (item.expression.name if isinstance(item.expression, ast.Variable) else None)
                if alias:
                    # The variable now refers to the CTE column
                    # We'll use the stage name as table alias for these variables
                    new_aliases[alias] = f"Stage{i+1}"
            context.variable_aliases = new_aliases

    # 2. Final stage (RETURN)
    # We continue with the state from the last QueryPart
    if cypher_query.return_clause:
        translate_return_clause(cypher_query.return_clause, context)
    
    # 3. Assemble final SQL
    distinct = cypher_query.return_clause.distinct if cypher_query.return_clause else False
    final_sql = context.build_stage_sql(distinct)
    
    # Add ORDER BY, LIMIT, SKIP to final stage
    if cypher_query.order_by_clause:
        order_items = []
        for item in cypher_query.order_by_clause.items:
            expr = translate_expression(item.expression, context)
            direction = "ASC" if item.ascending else "DESC"
            order_items.append(f"{expr} {direction}")
        final_sql += f"\nORDER BY {', '.join(order_items)}"
        
    if cypher_query.limit is not None:
        final_sql += f"\nLIMIT {cypher_query.limit}"
    if cypher_query.skip is not None:
        final_sql += f"\nOFFSET {cypher_query.skip}"

    if context.stages:
        full_sql = "WITH " + ",\n".join(context.stages) + "\n" + final_sql
    else:
        full_sql = final_sql

    return SQLQuery(sql=full_sql, parameters=context.parameters, query_metadata=metadata)


def translate_match_clause(match_clause, context: TranslationContext, metadata: QueryMetadata):
    pattern = match_clause.pattern
    if not pattern.nodes: return
    
    translate_node_pattern(pattern.nodes[0], context, metadata)
    
    for i, rel in enumerate(pattern.relationships):
        source_node = pattern.nodes[i]
        target_node = pattern.nodes[i + 1]
        translate_relationship_pattern(rel, source_node, target_node, context, metadata)
        translate_node_pattern(target_node, context, metadata)


def translate_node_pattern(node, context: TranslationContext, metadata: QueryMetadata):
    if node.variable and node.variable in context.variable_aliases:
        node_alias = context.variable_aliases[node.variable]
        if node_alias.startswith('Stage'):
            # Already joined via CTE stage
            # We don't need to add nodes table, but we might want to check labels/props
            # (Though in strict Cypher, labels/props should be checked in the stage where they were defined)
            return
    
    node_alias = context.register_variable(node.variable) if node.variable else context.next_alias("n")
    
    if not context.from_clauses:
        context.from_clauses.append(f"nodes {node_alias}")
    elif f"nodes {node_alias}" not in context.from_clauses and not any(node_alias in j for j in context.join_clauses):
        # Multi-match: use CROSS JOIN to avoid JOIN precedence issues in IRIS
        context.join_clauses.append(f"CROSS JOIN nodes {node_alias}")
        
    for label in node.labels:
        l_alias = context.next_alias("l")
        context.join_clauses.append(
            f"JOIN rdf_labels {l_alias} ON {l_alias}.s = {node_alias}.node_id "
            f"AND {l_alias}.label = {context.add_parameter(label)}"
        )
        
    for key, value in node.properties.items():
        if key == "node_id" or key == "id":
            context.where_conditions.append(f"{node_alias}.node_id = {context.add_parameter(value)}")
        else:
            p_alias = context.next_alias("p")
            context.join_clauses.append(
                f"JOIN rdf_props {p_alias} ON {p_alias}.s = {node_alias}.node_id "
                f"AND {p_alias}.key = {context.add_parameter(key)}"
            )
            context.where_conditions.append(f"{p_alias}.val = {context.add_parameter(value)}")


def translate_relationship_pattern(rel, source_node, target_node, context: TranslationContext, metadata: QueryMetadata):
    source_alias = context.variable_aliases[source_node.variable]
    target_alias = context.register_variable(target_node.variable)
    edge_alias = context.register_variable(rel.variable, prefix="e") if rel.variable else context.next_alias("e")
    
    # Column mapping for nodes (node_id or CTE column name)
    s_col = source_node.variable if source_alias.startswith('Stage') else "node_id"
    t_col = target_node.variable if target_alias.startswith('Stage') else "node_id"

    if rel.direction == ast.Direction.OUTGOING:
        edge_cond = f"{edge_alias}.s = {source_alias}.{s_col}"
        target_on = f"{target_alias}.{t_col} = {edge_alias}.o_id"
    elif rel.direction == ast.Direction.INCOMING:
        edge_cond = f"{edge_alias}.o_id = {source_alias}.{s_col}"
        target_on = f"{target_alias}.{t_col} = {edge_alias}.s"
    else:
        # BOTH
        edge_cond = f"({edge_alias}.s = {source_alias}.{s_col} OR {edge_alias}.o_id = {source_alias}.{s_col})"
        target_on = f"({target_alias}.{t_col} = {edge_alias}.o_id OR {target_alias}.{t_col} = {edge_alias}.s)"
        
    if rel.types:
        if len(rel.types) == 1:
            edge_cond += f" AND {edge_alias}.p = {context.add_parameter(rel.types[0])}"
        else:
            placeholders = ", ".join([context.add_parameter(t) for t in rel.types])
            edge_cond += f" AND {edge_alias}.p IN ({placeholders})"
            
    context.join_clauses.append(f"JOIN rdf_edges {edge_alias} ON {edge_cond}")
    
    # Only join nodes table if target is not a CTE stage reference
    if not target_alias.startswith('Stage'):
        context.join_clauses.append(f"JOIN nodes {target_alias} ON {target_on}")
    else:
        # For CTE targets, we apply the join condition as a WHERE since it's already in FROM
        context.where_conditions.append(target_on)


def translate_where_clause(where, context: TranslationContext):
    cond = translate_boolean_expression(where.expression, context)
    context.where_conditions.append(cond)


def translate_boolean_expression(expr, context: TranslationContext) -> str:
    if not isinstance(expr, ast.BooleanExpression):
        return translate_expression(expr, context)
        
    op = expr.operator
    if op == ast.BooleanOperator.AND:
        return "(" + " AND ".join(translate_boolean_expression(operand, context) for operand in expr.operands) + ")"
    if op == ast.BooleanOperator.OR:
        return "(" + " OR ".join(translate_boolean_expression(operand, context) for operand in expr.operands) + ")"
    if op == ast.BooleanOperator.NOT:
        return f"NOT ({translate_boolean_expression(expr.operands[0], context)})"
        
    # Binary comparisons
    left = translate_expression(expr.operands[0], context)
    
    if op == ast.BooleanOperator.IS_NULL: return f"{left} IS NULL"
    if op == ast.BooleanOperator.IS_NOT_NULL: return f"{left} IS NOT NULL"
    
    right = translate_expression(expr.operands[1], context)
    
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


def translate_expression(expr, context: TranslationContext) -> str:
    logger.debug(f"Translating expression: {expr}, aliases: {context.variable_aliases}")
    if isinstance(expr, ast.PropertyReference):
        var_alias = context.variable_aliases.get(expr.variable)
        if not var_alias:
            # Debug: show current state
            print(f"DEBUG: Undefined variable '{expr.variable}'. Current aliases: {context.variable_aliases}")
            raise ValueError(f"Undefined variable: {expr.variable}")
            
        if var_alias.startswith('Stage'):
            # Variable carried over from CTE, property must have been projected or it's unavailable
            if expr.property_name in ("node_id", "id"):
                return f"{var_alias}.{expr.variable}"
            return f"{var_alias}.{expr.variable}_{expr.property_name}"
            
        if expr.property_name in ("node_id", "id"):
            return f"{var_alias}.node_id"
        p_alias = context.next_alias("p")
        context.join_clauses.append(
            f"JOIN rdf_props {p_alias} ON {p_alias}.s = {var_alias}.node_id "
            f"AND {p_alias}.key = {context.add_parameter(expr.property_name)}"
        )
        return f"{p_alias}.val"
    if isinstance(expr, ast.Variable):
        var_alias = context.variable_aliases.get(expr.name)
        if not var_alias:
            raise ValueError(f"Undefined variable: {expr.name}")
            
        if var_alias.startswith('Stage'):
            return f"{var_alias}.{expr.name}"
        return f"{var_alias}.p" if var_alias.startswith('e') else f"{var_alias}.node_id"
    if isinstance(expr, ast.Literal):
        return context.add_parameter(expr.value)
    if isinstance(expr, ast.AggregationFunction):
        arg_sql = "*"
        if expr.argument:
            arg_sql = translate_expression(expr.argument, context)
        
        func_name = expr.function_name.upper()
        if func_name == "COLLECT":
            func_name = "JSON_ARRAYAGG"
        
        distinct = "DISTINCT " if expr.distinct else ""
        return f"{func_name}({distinct}{arg_sql})"
    
    if isinstance(expr, ast.FunctionCall):
        func_name = expr.function_name.lower()
        args_sql = [translate_expression(arg, context) for arg in expr.arguments]
        
        if func_name == "id":
            return args_sql[0] if args_sql else "NULL"
        if func_name == "type":
            return args_sql[0] if args_sql else "NULL"
        if func_name == "labels":
            s_val = args_sql[0] if args_sql else "NULL"
            return f"(SELECT JSON_ARRAYAGG(label) FROM rdf_labels WHERE s = {s_val})"
        
        return f"{func_name.upper()}({', '.join(args_sql)})"
        
    return "NULL"


def translate_return_clause(ret, context: TranslationContext):
    # Check for aggregations to determine if GROUP BY is needed
    has_agg = any(isinstance(item.expression, ast.AggregationFunction) for item in ret.items)
    
    for item in ret.items:
        expr_sql = translate_expression(item.expression, context)
        
        # Determine alias
        alias = item.alias
        if alias is None:
            if isinstance(item.expression, ast.PropertyReference):
                alias = f"{item.expression.variable}_{item.expression.property_name}"
            elif isinstance(item.expression, ast.Variable):
                alias = item.expression.name
            elif isinstance(item.expression, ast.AggregationFunction):
                alias = f"{item.expression.function_name}_res" # Avoid reserved word conflicts
            elif isinstance(item.expression, ast.FunctionCall):
                alias = f"{item.expression.function_name}_res"
        
        if alias:
            # IRIS SQL might not like dots in aliases unless quoted, use underscore
            safe_alias = alias.replace('.', '_')
            context.select_items.append(f"{expr_sql} AS {safe_alias}")
        else:
            context.select_items.append(expr_sql)
            
        # Add to GROUP BY if this is a mixed aggregation query
        if has_agg and not isinstance(item.expression, ast.AggregationFunction):
            context.group_by_items.append(expr_sql)


def translate_with_clause(with_clause, context: TranslationContext):
    # Similar to return but redefines scope
    has_agg = any(isinstance(item.expression, ast.AggregationFunction) for item in with_clause.items)
    
    for item in with_clause.items:
        expr_sql = translate_expression(item.expression, context)
        
        alias = item.alias
        if alias is None:
            if isinstance(item.expression, ast.PropertyReference):
                alias = f"{item.expression.variable}_{item.expression.property_name}"
            elif isinstance(item.expression, ast.Variable):
                alias = item.expression.name
            elif isinstance(item.expression, ast.AggregationFunction):
                alias = f"{item.expression.function_name}"
        
        if alias is None:
            alias = context.next_alias("v")
            
        safe_alias = alias.replace('.', '_')
        context.select_items.append(f"{expr_sql} AS {safe_alias}")
        
        if has_agg and not isinstance(item.expression, ast.AggregationFunction):
            context.group_by_items.append(expr_sql)
            
    if with_clause.where_clause:
        translate_where_clause(with_clause.where_clause, context)
