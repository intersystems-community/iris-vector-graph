"""
Cypher-to-SQL Translation Artifacts

Classes for managing SQL generation from Cypher AST.
Based on data-model.md lines 265-343.
"""

from dataclasses import dataclass, field
from typing import List, Any, Dict, Optional


@dataclass
class QueryMetadata:
    """
    Query execution metadata.

    Tracks optimization decisions and performance hints.
    """
    estimated_rows: Optional[int] = None
    index_usage: List[str] = field(default_factory=list)
    optimization_applied: List[str] = field(default_factory=list)
    complexity_score: Optional[float] = None


@dataclass
class SQLQuery:
    """
    Generated SQL query with parameters and metadata.

    Example:
        sql = "SELECT p.val FROM rdf_props p WHERE p.s = ? AND p.key = 'name'"
        parameters = ['PROTEIN:TP53']
        query_metadata = QueryMetadata(optimization_applied=['label_pushdown'])
    """
    sql: str
    parameters: List[Any] = field(default_factory=list)
    query_metadata: QueryMetadata = field(default_factory=QueryMetadata)


@dataclass
class TranslationContext:
    """
    Stateful context for SQL generation.

    Tracks variable mappings, table aliases, and accumulated SQL clauses
    during translation.
    """
    # Variable to table alias mapping (e.g., 'p' -> 'n0')
    variable_aliases: Dict[str, str] = field(default_factory=dict)

    # SQL clauses being accumulated
    select_items: List[str] = field(default_factory=list)
    from_clauses: List[str] = field(default_factory=list)
    join_clauses: List[str] = field(default_factory=list)
    where_conditions: List[str] = field(default_factory=list)
    order_by_items: List[str] = field(default_factory=list)

    # Query parameters
    parameters: List[Any] = field(default_factory=list)

    # Table alias counter
    _alias_counter: int = 0

    def next_alias(self, prefix: str = "t") -> str:
        """Generate next unique table alias"""
        alias = f"{prefix}{self._alias_counter}"
        self._alias_counter += 1
        return alias

    def register_variable(self, variable: str) -> str:
        """Register a Cypher variable and return its SQL alias"""
        if variable not in self.variable_aliases:
            self.variable_aliases[variable] = self.next_alias("n")
        return self.variable_aliases[variable]

    def add_parameter(self, value: Any) -> str:
        """Add parameter and return placeholder"""
        self.parameters.append(value)
        return "?"

    def build_sql(
        self,
        distinct: bool = False,
        limit: Optional[int] = None,
        skip: Optional[int] = None
    ) -> str:
        """Assemble final SQL query from accumulated clauses"""
        parts = []

        # SELECT
        distinct_kw = "DISTINCT " if distinct else ""
        select_clause = f"SELECT {distinct_kw}{', '.join(self.select_items)}"
        parts.append(select_clause)

        # FROM
        if self.from_clauses:
            parts.append(f"FROM {', '.join(self.from_clauses)}")

        # JOINs
        if self.join_clauses:
            parts.extend(self.join_clauses)

        # WHERE
        if self.where_conditions:
            where_clause = f"WHERE {' AND '.join(self.where_conditions)}"
            parts.append(where_clause)

        # ORDER BY
        if self.order_by_items:
            parts.append(f"ORDER BY {', '.join(self.order_by_items)}")

        # LIMIT/OFFSET (IRIS SQL syntax)
        if limit is not None:
            parts.append(f"LIMIT {limit}")
        if skip is not None:
            parts.append(f"OFFSET {skip}")

        return "\n".join(parts)


# ==============================================================================
# Translation Functions (T018-T020)
# ==============================================================================

def translate_to_sql(cypher_query) -> SQLQuery:
    """
    Translate CypherQuery AST to SQLQuery.

    Strategy:
    - NodePattern → JOIN to rdf_labels + rdf_props
    - RelationshipPattern → JOIN to rdf_edges
    - WhereClause → SQL WHERE conditions
    - ReturnClause → SQL SELECT items
    - ORDER BY, LIMIT, SKIP → Direct SQL equivalents

    Example:
        MATCH (p:Protein {id: 'PROTEIN:TP53'}) RETURN p.name
        →
        SELECT p0.val
        FROM nodes n0
        JOIN rdf_labels l0 ON l0.s = n0.node_id AND l0.label = 'Protein'
        JOIN rdf_props p0 ON p0.s = n0.node_id AND p0.key = 'name'
        WHERE n0.node_id = 'PROTEIN:TP53'
    """
    from . import ast

    context = TranslationContext()
    metadata = QueryMetadata()

    # Translate MATCH clauses
    for match_clause in cypher_query.match_clauses:
        translate_match_clause(match_clause, context, metadata)

    # Translate WHERE clause
    if cypher_query.where_clause:
        translate_where_clause(cypher_query.where_clause, context)

    # Translate RETURN clause
    translate_return_clause(cypher_query.return_clause, context)

    # Translate ORDER BY
    if cypher_query.order_by_clause:
        translate_order_by(cypher_query.order_by_clause, context)

    # Build final SQL
    sql = context.build_sql(
        distinct=cypher_query.return_clause.distinct,
        limit=cypher_query.limit,
        skip=cypher_query.skip
    )

    return SQLQuery(
        sql=sql,
        parameters=context.parameters,
        query_metadata=metadata
    )


def translate_match_clause(match_clause, context: TranslationContext, metadata: QueryMetadata):
    """Translate MATCH clause to SQL JOINs"""
    from . import ast

    pattern = match_clause.pattern

    # Translate each node in the pattern
    for i, node in enumerate(pattern.nodes):
        translate_node_pattern(node, context, metadata)

    # Translate relationships
    for i, rel in enumerate(pattern.relationships):
        source_node = pattern.nodes[i]
        target_node = pattern.nodes[i + 1]
        translate_relationship_pattern(rel, source_node, target_node, context, metadata)


def translate_node_pattern(node, context: TranslationContext, metadata: QueryMetadata):
    """
    Translate NodePattern to SQL JOINs.

    Example:
        (p:Protein {id: 'PROTEIN:TP53'})
        →
        FROM nodes n0
        JOIN rdf_labels l0 ON l0.s = n0.node_id AND l0.label = 'Protein'
        WHERE n0.node_id = 'PROTEIN:TP53'
    """
    from . import ast

    # Register variable and get alias
    if node.variable:
        node_alias = context.register_variable(node.variable)
    else:
        node_alias = context.next_alias("n")

    # Add nodes table to FROM clause (first node only)
    if not context.from_clauses:
        context.from_clauses.append(f"nodes {node_alias}")

    # Add label filters
    if node.labels:
        for label in node.labels:
            label_alias = context.next_alias("l")
            context.join_clauses.append(
                f"JOIN rdf_labels {label_alias} ON {label_alias}.s = {node_alias}.node_id "
                f"AND {label_alias}.label = {context.add_parameter(label)}"
            )
            metadata.optimization_applied.append("label_pushdown")

    # Add property filters
    for key, value in node.properties.items():
        if key == "id":
            # Special case: id property maps to node_id
            context.where_conditions.append(
                f"{node_alias}.node_id = {context.add_parameter(value)}"
            )
        else:
            # Regular property
            prop_alias = context.next_alias("p")
            context.join_clauses.append(
                f"JOIN rdf_props {prop_alias} ON {prop_alias}.s = {node_alias}.node_id "
                f"AND {prop_alias}.key = {context.add_parameter(key)}"
            )
            context.where_conditions.append(
                f"{prop_alias}.val = {context.add_parameter(value)}"
            )
            metadata.optimization_applied.append("property_pushdown")


def translate_relationship_pattern(rel, source_node, target_node, context: TranslationContext, metadata: QueryMetadata):
    """
    Translate RelationshipPattern to SQL JOINs.

    Example:
        (p)-[:INTERACTS_WITH]->(t)
        →
        JOIN nodes n1
        JOIN rdf_edges e0 ON e0.s = n0.node_id AND e0.o_id = n1.node_id AND e0.p = 'INTERACTS_WITH'
    """
    from . import ast

    # Get source and target aliases
    source_alias = context.variable_aliases[source_node.variable]
    target_alias = context.register_variable(target_node.variable)

    # Add target node to FROM via JOIN
    context.join_clauses.append(f"JOIN nodes {target_alias}")

    # Add edge JOIN
    edge_alias = context.next_alias("e")

    # Direction handling
    if rel.direction == ast.Direction.OUTGOING:
        edge_condition = f"{edge_alias}.s = {source_alias}.node_id AND {edge_alias}.o_id = {target_alias}.node_id"
    elif rel.direction == ast.Direction.INCOMING:
        edge_condition = f"{edge_alias}.o_id = {source_alias}.node_id AND {edge_alias}.s = {target_alias}.node_id"
    else:  # BOTH
        # Use UNION for bidirectional (simplified for MVP)
        edge_condition = f"({edge_alias}.s = {source_alias}.node_id AND {edge_alias}.o_id = {target_alias}.node_id)"

    # Add relationship type filter
    if rel.types:
        rel_type = rel.types[0]  # MVP: single type only
        edge_condition += f" AND {edge_alias}.p = {context.add_parameter(rel_type)}"

    context.join_clauses.append(f"JOIN rdf_edges {edge_alias} ON {edge_condition}")

    # Variable-length paths (simplified for MVP - expand to multiple JOINs)
    if rel.variable_length:
        # For MVP, treat as single hop
        # Full implementation would use recursive CTEs
        pass


def translate_where_clause(where, context: TranslationContext):
    """Translate WhereClause to SQL WHERE conditions"""
    from . import ast

    expr = where.expression
    condition = translate_boolean_expression(expr, context)
    context.where_conditions.append(condition)


def translate_boolean_expression(expr, context: TranslationContext) -> str:
    """Translate BooleanExpression to SQL condition"""
    from . import ast

    if expr.operator == ast.BooleanOperator.EQUALS:
        left = translate_expression(expr.operands[0], context)
        right = translate_expression(expr.operands[1], context)
        return f"{left} = {right}"
    elif expr.operator == ast.BooleanOperator.AND:
        conditions = [translate_boolean_expression(op, context) for op in expr.operands]
        return f"({' AND '.join(conditions)})"
    elif expr.operator == ast.BooleanOperator.OR:
        conditions = [translate_boolean_expression(op, context) for op in expr.operands]
        return f"({' OR '.join(conditions)})"
    else:
        raise ValueError(f"Unsupported boolean operator: {expr.operator}")


def translate_expression(expr, context: TranslationContext) -> str:
    """Translate expression to SQL"""
    from . import ast

    if isinstance(expr, ast.PropertyReference):
        # Look up property value
        var_alias = context.variable_aliases[expr.variable]
        prop_alias = context.next_alias("p")
        context.join_clauses.append(
            f"JOIN rdf_props {prop_alias} ON {prop_alias}.s = {var_alias}.node_id "
            f"AND {prop_alias}.key = {context.add_parameter(expr.property_name)}"
        )
        return f"{prop_alias}.val"
    elif isinstance(expr, ast.Literal):
        return context.add_parameter(expr.value)
    elif isinstance(expr, ast.Variable):
        return context.variable_aliases[expr.name] + ".node_id"
    else:
        raise ValueError(f"Unsupported expression type: {type(expr)}")


def translate_return_clause(ret, context: TranslationContext):
    """Translate ReturnClause to SQL SELECT items"""
    from . import ast

    for item in ret.items:
        if isinstance(item.expression, ast.PropertyReference):
            # Property reference: p.name
            var_alias = context.variable_aliases[item.expression.variable]
            prop_alias = context.next_alias("p")

            # Add JOIN to fetch property value
            context.join_clauses.append(
                f"JOIN rdf_props {prop_alias} ON {prop_alias}.s = {var_alias}.node_id "
                f"AND {prop_alias}.key = {context.add_parameter(item.expression.property_name)}"
            )

            # Add to SELECT
            if item.alias:
                context.select_items.append(f"{prop_alias}.val AS {item.alias}")
            else:
                context.select_items.append(f"{prop_alias}.val")

        elif isinstance(item.expression, ast.Variable):
            # Variable reference: p
            var_alias = context.variable_aliases[item.expression.name]
            if item.alias:
                context.select_items.append(f"{var_alias}.node_id AS {item.alias}")
            else:
                context.select_items.append(f"{var_alias}.node_id")


def translate_order_by(order_by, context: TranslationContext):
    """Translate OrderByClause to SQL ORDER BY"""
    from . import ast

    for order_item in order_by.items:
        if isinstance(order_item.expression, ast.PropertyReference):
            # Need to reference same property as in SELECT
            # For MVP, assume it's already in SELECT
            # Full implementation would track property aliases
            direction = "ASC" if order_item.ascending else "DESC"
            # Simplified: use first select item
            context.order_by_items.append(f"1 {direction}")
        elif isinstance(order_item.expression, ast.Variable):
            var_alias = context.variable_aliases[order_item.expression.name]
            direction = "ASC" if order_item.ascending else "DESC"
            context.order_by_items.append(f"{var_alias}.node_id {direction}")
