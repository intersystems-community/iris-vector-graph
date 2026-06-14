import strawberry
from typing import List, Optional, Any, Dict, Type, Union
from enum import Enum
from strawberry.scalars import JSON
from .engine import GQLGraphEngine
from .constants import RESERVED_KEYWORDS, EXCLUDED_PROPERTIES, DYNAMIC_TYPES

@strawberry.enum
class Direction(Enum):
    OUTGOING = "OUTGOING"
    INCOMING = "INCOMING"

@strawberry.type
class Property:
    key: str
    value: Optional[str]

@strawberry.type
class GraphStats:
    node_count: int = strawberry.field(name="nodeCount")
    edge_count: int = strawberry.field(name="edgeCount")
    label_count: int = strawberry.field(name="labelCount")


@strawberry.type
class CypherResult:
    columns: List[str]
    rows: List[JSON]

@strawberry.interface
class Node:
    id: strawberry.ID
    labels: List[str]
    
    @strawberry.field
    def properties(self) -> List[Property]:
        return []

    @strawberry.field
    async def outgoing(self, info: strawberry.Info, predicate: Optional[str] = None, limit: int = 10) -> List['Relationship']:
        from .resolvers import resolve_outgoing
        return await resolve_outgoing(info, self, predicate, limit)

    @strawberry.field
    async def incoming(self, info: strawberry.Info, predicate: Optional[str] = None, limit: int = 10) -> List['Relationship']:
        from .resolvers import resolve_incoming
        return await resolve_incoming(info, self, predicate, limit)

    @staticmethod
    def resolve_type(obj, info, return_type):
        if hasattr(obj, "_primary_label"):
            return obj._primary_label
        if hasattr(obj, "labels") and obj.labels:
            return obj.labels[0]
        return None

@strawberry.type
class Relationship:
    predicate: str
    target_id: str
    
    @strawberry.field
    async def node(self, info: strawberry.Info) -> Optional[Node]:
        from .resolvers import resolve_node
        return await resolve_node(info, strawberry.ID(self.target_id))

@strawberry.type
class SemanticSearchResult:
    score: float
    node: Node

_VALID_GQL_NAME = __import__("re").compile(r"[^_a-zA-Z0-9]")


def _sanitize_gql_name(name: str) -> str:
    """Convert an arbitrary string to a valid GraphQL type name."""
    sanitized = _VALID_GQL_NAME.sub("_", name)
    if sanitized and sanitized[0].isdigit():
        sanitized = f"T_{sanitized}"
    return sanitized or "Unknown"


def create_dynamic_node_type(label: str, properties: List[str]) -> Type:
    """
    Creates a dynamic Strawberry type for a specific node label.
    """
    if label in DYNAMIC_TYPES:
        return DYNAMIC_TYPES[label]

    type_name = _sanitize_gql_name(label)
    if not type_name:
        return None

    annotations = {
        "id": strawberry.ID,
        "labels": List[str],
    }
    
    defaults = {}
    for prop in properties:
        if prop.lower() in EXCLUDED_PROPERTIES:
            continue
            
        field_name = prop.replace(" ", "_").replace("-", "_").replace(".", "_")
        field_name = "".join(c if c.isalnum() or c == "_" else "_" for c in field_name)
        if field_name and field_name[0].isdigit():
            field_name = f"p_{field_name}"
        if not field_name:
            continue
        if field_name.lower() in RESERVED_KEYWORDS:
            field_name = f"p_{field_name}"
        
        annotations[field_name] = Optional[str]
        defaults[field_name] = None

    dynamic_type = type(
        type_name,
        (Node,),
        {"__annotations__": annotations, **defaults}
    )

    st_type = strawberry.type(dynamic_type)
    DYNAMIC_TYPES[label] = st_type
    return st_type

def build_schema(engine: GQLGraphEngine) -> strawberry.Schema:
    """
    Introspects the graph and builds the complete Strawberry schema.
    Clears the dynamic type registry first so each call reflects the current DB state.
    """
    DYNAMIC_TYPES.clear()

    metadata = engine.get_schema_metadata()
    
    for label, props in metadata.items():
        try:
            create_dynamic_node_type(label, list(props))
        except Exception:
            pass

    @strawberry.type
    class Query:
        @strawberry.field
        async def stats(self, info: strawberry.Info) -> GraphStats:
            from .resolvers import resolve_stats
            return await resolve_stats(info)

        @strawberry.field
        async def node(self, info: strawberry.Info, id: strawberry.ID) -> Optional[Node]:
            from .resolvers import resolve_node
            return await resolve_node(info, id)

        @strawberry.field
        async def nodes(self, info: strawberry.Info, label: str, limit: int = 10, offset: int = 0) -> List[Node]:
            from .resolvers import resolve_nodes
            return await resolve_nodes(info, label, limit, offset)

        @strawberry.field
        async def semantic_search(self, info: strawberry.Info, query: str, label: Optional[str] = None, limit: int = 5) -> List[SemanticSearchResult]:
            from .resolvers import resolve_semantic_search
            return await resolve_semantic_search(info, query, label, limit)

        @strawberry.field
        async def cypher(self, info: strawberry.Info, query: str, parameters: Optional[JSON] = None) -> CypherResult:
            from .resolvers import resolve_cypher
            return await resolve_cypher(info, query, parameters)

    return strawberry.Schema(query=Query, types=list(DYNAMIC_TYPES.values()))
