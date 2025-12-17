"""
Cypher AST (Abstract Syntax Tree) Classes

Internal representation of parsed openCypher queries.
These classes are parser-agnostic and used for SQL translation.

Based on data-model.md from specs/002-add-opencypher-endpoint/
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


# ==============================================================================
# Graph Pattern Elements (data-model.md lines 84-155)
# ==============================================================================

@dataclass
class NodePattern:
    """
    Node pattern in MATCH clause.

    Example: (p:Protein {id: 'PROTEIN:TP53'})
    - variable: 'p'
    - labels: ['Protein']
    - properties: {'id': 'PROTEIN:TP53'}
    """
    variable: Optional[str] = None  # Variable name (e.g., 'p')
    labels: List[str] = field(default_factory=list)  # Node labels (e.g., ['Protein'])
    properties: Dict[str, Any] = field(default_factory=dict)  # Property filters


@dataclass
class VariableLength:
    """
    Variable-length path specification.

    Example: *1..3
    - min_hops: 1
    - max_hops: 3

    Validation: 1 ≤ min_hops ≤ max_hops ≤ 10
    """
    min_hops: int = 1
    max_hops: int = 1

    def __post_init__(self):
        if self.min_hops < 1:
            raise ValueError("min_hops must be >= 1")
        if self.max_hops < self.min_hops:
            raise ValueError("max_hops must be >= min_hops")
        if self.max_hops > 10:
            raise ValueError("max_hops must be <= 10 (complexity limit)")


@dataclass
class RelationshipPattern:
    """
    Relationship pattern in MATCH clause.

    Example: -[:INTERACTS_WITH*1..2]->
    - types: ['INTERACTS_WITH']
    - direction: Direction.OUTGOING
    - variable_length: VariableLength(1, 2)
    """
    types: List[str] = field(default_factory=list)  # Relationship types
    direction: Direction = Direction.BOTH  # Traversal direction
    variable: Optional[str] = None  # Variable name for relationship
    properties: Dict[str, Any] = field(default_factory=dict)  # Property filters
    variable_length: Optional[VariableLength] = None  # Variable-length path


@dataclass
class GraphPattern:
    """
    Complete graph pattern (nodes + relationships).

    Example: (p:Protein)-[:INTERACTS_WITH]->(t:Protein)
    - nodes: [NodePattern('p', ['Protein']), NodePattern('t', ['Protein'])]
    - relationships: [RelationshipPattern(['INTERACTS_WITH'], Direction.OUTGOING)]

    Validation: len(relationships) == len(nodes) - 1
    """
    nodes: List[NodePattern] = field(default_factory=list)
    relationships: List[RelationshipPattern] = field(default_factory=list)

    def __post_init__(self):
        if len(self.relationships) != len(self.nodes) - 1:
            raise ValueError(
                f"Invalid graph pattern: {len(self.nodes)} nodes requires "
                f"{len(self.nodes) - 1} relationships, got {len(self.relationships)}"
            )


# ==============================================================================
# Clause Elements
# ==============================================================================

@dataclass
class MatchClause:
    """
    MATCH clause with optional OPTIONAL modifier.

    Example: OPTIONAL MATCH (p:Protein)-[:INTERACTS_WITH]->(t)
    - optional: True
    - pattern: GraphPattern(...)
    """
    pattern: GraphPattern
    optional: bool = False


@dataclass
class PropertyReference:
    """Reference to a node/relationship property (e.g., p.name)"""
    variable: str  # Variable name (e.g., 'p')
    property_name: str  # Property name (e.g., 'name')


@dataclass
class Literal:
    """Literal value (string, number, boolean, null)"""
    value: Any


@dataclass
class Variable:
    """Variable reference (e.g., p, r, m)"""
    name: str


@dataclass
class BooleanExpression:
    """
    Boolean expression in WHERE clause (recursive).

    Examples:
    - p.name = 'TP53': BooleanExpression(EQUALS, [PropertyReference('p', 'name'), Literal('TP53')])
    - p.score > 0.8 AND p.type = 'kinase': BooleanExpression(AND, [expr1, expr2])
    """
    operator: BooleanOperator
    operands: List[Union['BooleanExpression', PropertyReference, Literal, Variable]]


@dataclass
class WhereClause:
    """WHERE clause with filter expression"""
    expression: BooleanExpression


@dataclass
class AggregationFunction:
    """Aggregation function (count, sum, avg, collect, etc.)"""
    function_name: str  # 'count', 'sum', 'collect', etc.
    argument: Union[PropertyReference, Variable, Literal]
    distinct: bool = False


@dataclass
class ReturnItem:
    """
    Single item in RETURN clause.

    Examples:
    - p.name: ReturnItem(PropertyReference('p', 'name'), None)
    - p.name AS protein_name: ReturnItem(PropertyReference('p', 'name'), 'protein_name')
    - count(p): ReturnItem(AggregationFunction('count', Variable('p')), None)
    """
    expression: Union[PropertyReference, Variable, AggregationFunction, Literal]
    alias: Optional[str] = None


@dataclass
class ReturnClause:
    """
    RETURN clause with projection items.

    Example: RETURN DISTINCT p.name, p.function AS func
    - distinct: True
    - items: [ReturnItem(...), ReturnItem(...)]
    """
    items: List[ReturnItem]
    distinct: bool = False


@dataclass
class OrderByItem:
    """Single item in ORDER BY clause"""
    expression: Union[PropertyReference, Variable]
    ascending: bool = True


@dataclass
class OrderByClause:
    """ORDER BY clause"""
    items: List[OrderByItem]


# ==============================================================================
# Custom Procedures (data-model.md lines 238-263)
# ==============================================================================

@dataclass
class CypherProcedureCall:
    """
    Custom procedure call (e.g., CALL db.index.vector.queryNodes).

    Example: CALL db.index.vector.queryNodes('protein_embeddings', 10, $queryVector)
    - procedure_name: 'db.index.vector.queryNodes'
    - arguments: [Literal('protein_embeddings'), Literal(10), Variable('queryVector')]
    - yield_items: ['node', 'score']
    """
    procedure_name: str
    arguments: List[Union[Literal, Variable, PropertyReference]]
    yield_items: List[str] = field(default_factory=list)


# ==============================================================================
# Root Query Node (data-model.md lines 11-40)
# ==============================================================================

@dataclass
class CypherQuery:
    """
    Root AST node for openCypher query.

    Complete query structure with all clauses.

    Validation:
    - At least one MATCH clause required
    - Exactly one RETURN clause required
    """
    match_clauses: List[MatchClause] = field(default_factory=list)
    where_clause: Optional[WhereClause] = None
    return_clause: Optional[ReturnClause] = None
    order_by_clause: Optional[OrderByClause] = None
    skip: Optional[int] = None
    limit: Optional[int] = None
    procedure_call: Optional[CypherProcedureCall] = None  # CALL clause

    def __post_init__(self):
        # Either MATCH or CALL required
        if not self.match_clauses and not self.procedure_call:
            raise ValueError("Query must have at least one MATCH clause or CALL clause")

        # RETURN required
        if not self.return_clause:
            raise ValueError("Query must have exactly one RETURN clause")


# ==============================================================================
# Helper Functions
# ==============================================================================

def create_simple_match(
    variable: str,
    labels: List[str],
    properties: Optional[Dict[str, Any]] = None
) -> MatchClause:
    """
    Helper to create simple single-node MATCH clause.

    Example: create_simple_match('p', ['Protein'], {'id': 'PROTEIN:TP53'})
    → MATCH (p:Protein {id: 'PROTEIN:TP53'})
    """
    node = NodePattern(variable=variable, labels=labels, properties=properties or {})
    pattern = GraphPattern(nodes=[node], relationships=[])
    return MatchClause(pattern=pattern)


def create_relationship_match(
    source_var: str,
    source_labels: List[str],
    rel_types: List[str],
    direction: Direction,
    target_var: str,
    target_labels: List[str],
    variable_length: Optional[VariableLength] = None
) -> MatchClause:
    """
    Helper to create two-node relationship MATCH clause.

    Example: create_relationship_match('p', ['Protein'], ['INTERACTS_WITH'],
                                       Direction.OUTGOING, 't', ['Protein'])
    → MATCH (p:Protein)-[:INTERACTS_WITH]->(t:Protein)
    """
    source = NodePattern(variable=source_var, labels=source_labels)
    target = NodePattern(variable=target_var, labels=target_labels)
    rel = RelationshipPattern(
        types=rel_types,
        direction=direction,
        variable_length=variable_length
    )
    pattern = GraphPattern(nodes=[source, target], relationships=[rel])
    return MatchClause(pattern=pattern)
