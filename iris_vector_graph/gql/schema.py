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

def create_dynamic_node_type(label: str, properties: List[str]) -> Type:
    """
    Creates a dynamic Strawberry type for a specific node label.
    """
    if label in DYNAMIC_TYPES:
        return DYNAMIC_TYPES[label]

    annotations = {
        "id": strawberry.ID,
        "labels": List[str],
    }
    
    for prop in properties:
        if prop.lower() in EXCLUDED_PROPERTIES:
            continue
            
        field_name = prop
        if prop.lower() in RESERVED_KEYWORDS:
            field_name = f"p_{prop}"
        
        annotations[field_name] = Optional[str]

    dynamic_type = type(
        label,
        (Node,),
        {"__annotations__": annotations}
    )
    
    st_type = strawberry.type(dynamic_type)
    DYNAMIC_TYPES[label] = st_type
    return st_type

def build_schema(engine: GQLGraphEngine) -> strawberry.Schema:
    """
    Introspects the graph and builds the complete Strawberry schema.
    """
    metadata = engine.get_schema_metadata()
    
    for label, props in metadata.items():
        create_dynamic_node_type(label, list(props))

    @strawberry.type
    class Query:
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
