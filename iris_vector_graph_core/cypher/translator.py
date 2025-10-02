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
# Stub Functions (to be implemented in T018-T020)
# ==============================================================================

def translate_to_sql(cypher_query) -> SQLQuery:
    """
    Translate CypherQuery AST to SQLQuery.

    To be implemented in T018-T020.
    """
    raise NotImplementedError("translate_to_sql not implemented yet - see T018-T020")


def translate_node_pattern(node, context: TranslationContext) -> str:
    """Translate NodePattern to SQL JOINs. To be implemented in T018."""
    raise NotImplementedError("translate_node_pattern not implemented yet")


def translate_relationship_pattern(rel, context: TranslationContext) -> str:
    """Translate RelationshipPattern to SQL JOINs. To be implemented in T019."""
    raise NotImplementedError("translate_relationship_pattern not implemented yet")


def translate_where_clause(where, context: TranslationContext) -> str:
    """Translate WhereClause to SQL WHERE conditions. To be implemented in T020."""
    raise NotImplementedError("translate_where_clause not implemented yet")


def translate_return_clause(ret, context: TranslationContext) -> None:
    """Translate ReturnClause to SQL SELECT items. To be implemented in T020."""
    raise NotImplementedError("translate_return_clause not implemented yet")
