"""
Cypher AST (Abstract Syntax Tree) Classes

Internal representation of parsed openCypher queries.
These classes are parser-agnostic and used for SQL translation.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Union
from enum import Enum


# ==============================================================================
# Enums
# ==============================================================================

class Direction(Enum):
    """Direction for relationship traversal"""
    OUTGOING = "OUTGOING"
    INCOMING = "INCOMING"
    BOTH = "BOTH"


class BooleanOperator(Enum):
    """Boolean operators for WHERE clause"""
    AND = "AND"
    OR = "OR"
    NOT = "NOT"
    EQUALS = "="
    NOT_EQUALS = "<>"
    LESS_THAN = "<"
    LESS_THAN_OR_EQUAL = "<="
    GREATER_THAN = ">"
    GREATER_THAN_OR_EQUAL = ">="
    IN = "IN"
    LIKE = "LIKE"
    IS_NULL = "IS NULL"
    IS_NOT_NULL = "IS NOT NULL"
    STARTS_WITH = "STARTS WITH"
    ENDS_WITH = "ENDS WITH"
    CONTAINS = "CONTAINS"


# ==============================================================================
# Graph Pattern Elements
# ==============================================================================

@dataclass(slots=True)
class NodePattern:
    """
    Node pattern in MATCH clause.
    Example: (p:Protein {id: 'PROTEIN:TP53'})
    """
    variable: Optional[str] = None
    labels: List[str] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class VariableLength:
    """Variable-length path specification: *min..max"""
    min_hops: int = 1
    max_hops: int = 1

    def __post_init__(self):
        if self.min_hops < 1:
            raise ValueError("min_hops must be >= 1")
        if self.max_hops < self.min_hops:
            raise ValueError("max_hops must be >= min_hops")
        if self.max_hops > 10:
            raise ValueError("max_hops must be <= 10 (complexity limit)")


@dataclass(slots=True)
class RelationshipPattern:
    """
    Relationship pattern in MATCH clause.
    Example: -[:INTERACTS_WITH*1..2]->
    """
    types: List[str] = field(default_factory=list)
    direction: Direction = Direction.BOTH
    variable: Optional[str] = None
    properties: Dict[str, Any] = field(default_factory=dict)
    variable_length: Optional[VariableLength] = None


@dataclass(slots=True)
class GraphPattern:
    """Complete graph pattern (nodes + relationships)"""
    nodes: List[NodePattern] = field(default_factory=list)
    relationships: List[RelationshipPattern] = field(default_factory=list)

    def __post_init__(self):
        if len(self.relationships) != len(self.nodes) - 1:
            raise ValueError(
                f"Invalid graph pattern: {len(self.nodes)} nodes requires "
                f"{len(self.nodes) - 1} relationships, got {len(self.relationships)}"
            )


# ==============================================================================
# Expressions and Clauses
# ==============================================================================

@dataclass(slots=True)
class PropertyReference:
    """Reference to a node/relationship property (e.g., p.name)"""
    variable: str
    property_name: str


@dataclass(slots=True)
class Literal:
    """Literal value (string, number, boolean, null)"""
    value: Any


@dataclass(slots=True)
class Variable:
    """Variable reference (e.g., p, r, m)"""
    name: str


@dataclass(slots=True)
class AggregationFunction:
    """Aggregation function (count, sum, avg, collect, etc.)"""
    function_name: str
    argument: Optional[Union['BooleanExpression', 'PropertyReference', 'Variable', 'Literal', 'FunctionCall']] = None
    distinct: bool = False


@dataclass(slots=True)
class FunctionCall:
    """Function call (id, type, labels, etc.)"""
    function_name: str
    arguments: List[Union['BooleanExpression', 'PropertyReference', 'Variable', 'Literal', 'FunctionCall']]


@dataclass(slots=True)
class BooleanExpression:
    """Boolean expression in WHERE clause (recursive)"""
    operator: BooleanOperator
    operands: List[Union['BooleanExpression', 'PropertyReference', 'Literal', 'Variable', 'AggregationFunction', 'FunctionCall']]



@dataclass(slots=True)
class WhereClause:
    """WHERE clause with filter expression"""
    expression: BooleanExpression


@dataclass(slots=True)
class MatchClause:
    """MATCH clause with optional pattern"""
    pattern: GraphPattern
    optional: bool = False


@dataclass(slots=True)
class ReturnItem:
    """Single item in RETURN or WITH clause"""
    expression: Union[PropertyReference, Variable, AggregationFunction, Literal, BooleanExpression]
    alias: Optional[str] = None


@dataclass(slots=True)
class WithClause:
    """WITH clause for chaining queries"""
    items: List[ReturnItem]
    distinct: bool = False
    where_clause: Optional[WhereClause] = None


@dataclass(slots=True)
class QueryPart:
    """A stage in a multi-stage query"""
    match_clauses: List[MatchClause] = field(default_factory=list)
    where_clause: Optional[WhereClause] = None
    with_clause: Optional[WithClause] = None


@dataclass(slots=True)
class ReturnClause:
    """Final projection of the query"""
    items: List[ReturnItem]
    distinct: bool = False


@dataclass(slots=True)
class OrderByItem:
    """Single item in ORDER BY clause"""
    expression: Union[PropertyReference, Variable]
    ascending: bool = True


@dataclass(slots=True)
class OrderByClause:
    """ORDER BY clause"""
    items: List[OrderByItem]


@dataclass(slots=True)
class CypherProcedureCall:
    """Custom procedure call (CALL clause)"""
    procedure_name: str
    arguments: List[Union[Literal, Variable, PropertyReference]]
    yield_items: List[str] = field(default_factory=list)


@dataclass(slots=True)
class CypherQuery:
    """Root AST node for openCypher query"""
    query_parts: List[QueryPart] = field(default_factory=list)
    return_clause: Optional[ReturnClause] = None
    order_by_clause: Optional[OrderByClause] = None
    skip: Optional[int] = None
    limit: Optional[int] = None
    procedure_call: Optional[CypherProcedureCall] = None

    def __post_init__(self):
        if not self.query_parts and not self.procedure_call:
            raise ValueError("Query must have at least one MATCH/WITH stage or CALL clause")
        if not self.return_clause and not self.procedure_call:
            # RETURN is not required if there's a standalone procedure call, 
            # but usually Cypher expects RETURN.
            pass
