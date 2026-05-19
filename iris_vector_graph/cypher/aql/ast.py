import enum
from dataclasses import dataclass, field
from typing import Any, Optional, Union


class AQLDirection(enum.Enum):
    OUTBOUND = "OUTBOUND"
    INBOUND = "INBOUND"
    ANY = "ANY"


@dataclass(slots=True)
class AQLVariable:
    name: str


@dataclass(slots=True)
class AQLPropertyAccess:
    object: Any
    property: str


@dataclass(slots=True)
class AQLKeyAccess:
    object: Any
    key: str


@dataclass(slots=True)
class AQLBindVar:
    name: str


@dataclass(slots=True)
class AQLLiteral:
    value: Any


@dataclass(slots=True)
class AQLBinaryOp:
    operator: str
    left: Any
    right: Any


@dataclass(slots=True)
class AQLUnaryOp:
    operator: str
    operand: Any


@dataclass(slots=True)
class AQLFunctionCall:
    name: str
    args: list = field(default_factory=list)


@dataclass(slots=True)
class AQLObjectLiteral:
    fields: dict = field(default_factory=dict)


@dataclass(slots=True)
class AQLArrayLiteral:
    items: list = field(default_factory=list)


AQLExpression = Union[
    AQLVariable, AQLPropertyAccess, AQLKeyAccess, AQLBindVar, AQLLiteral,
    AQLBinaryOp, AQLUnaryOp, AQLFunctionCall, AQLObjectLiteral, AQLArrayLiteral
]


@dataclass(slots=True)
class ForClause:
    vertex_var: str
    edge_var: Optional[str]
    path_var: Optional[str]
    min_depth: int
    max_depth: int
    direction: AQLDirection
    start_expr: Any
    graph_or_collections: list
    is_graph: bool


@dataclass(slots=True)
class ShortestPathClause:
    vertex_var: str
    edge_var: Optional[str]
    direction: AQLDirection
    start_expr: Any
    end_expr: Any
    graph_or_collections: list
    is_graph: bool


@dataclass(slots=True)
class FilterClause:
    condition: Any


@dataclass(slots=True)
class LetClause:
    variable: str
    value: Any


@dataclass(slots=True)
class CollectClause:
    assignments: list
    with_count_into: Optional[str]
    aggregate: list
    into_var: Optional[str]


@dataclass(slots=True)
class SortItem:
    expression: Any
    ascending: bool


@dataclass(slots=True)
class SortClause:
    items: list


@dataclass(slots=True)
class LimitClause:
    offset: Optional[int]
    count: int


@dataclass(slots=True)
class ReturnClause:
    distinct: bool
    expression: Any


@dataclass(slots=True)
class AQLQuery:
    for_clause: Any
    filter_clauses: list = field(default_factory=list)
    let_clauses: list = field(default_factory=list)
    collect_clause: Optional[Any] = None
    sort_clause: Optional[Any] = None
    limit_clause: Optional[Any] = None
    return_clause: Optional[Any] = None
