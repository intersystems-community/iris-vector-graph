"""
GraphQL Types for IRIS Vector Graph API

Strawberry GraphQL type definitions implementing the schema contract.
All entity types implement the Node interface.
"""

import strawberry
from typing import List, Optional
from datetime import datetime
import json as json_module


# Custom scalar types
@strawberry.scalar(
    serialize=lambda v: json_module.dumps(v) if isinstance(v, dict) else v,
    parse_value=lambda v: json_module.loads(v) if isinstance(v, str) else v,
)
class JSON:
    """Arbitrary JSON data from rdf_props"""
    pass


DateTime = strawberry.scalar(
    datetime,
    serialize=lambda v: v.isoformat() if v else None,
    parse_value=lambda v: datetime.fromisoformat(v) if isinstance(v, str) else v,
)


# Node interface - base for all graph entities
@strawberry.interface
class Node:
    """Base interface for all graph entities"""
    id: strawberry.ID
    labels: List[str]
    properties: JSON
    created_at: DateTime = strawberry.field(name="createdAt")


# Forward declarations for circular references
@strawberry.type
class Protein(Node):
    """Protein entity with relationships and vector similarity"""
    # Node interface fields
    id: strawberry.ID
    labels: List[str]
    properties: JSON
    created_at: DateTime = strawberry.field(name="createdAt")

    # Protein-specific fields
    name: str
    function: Optional[str] = None
    organism: Optional[str] = None
    confidence: Optional[float] = None

    # Relationship fields (resolvers to be implemented)
    @strawberry.field
    async def interacts_with(
        self,
        info: strawberry.Info,
        first: int = 10,
        offset: int = 0
    ) -> List["Protein"]:
        """Proteins that interact with this protein"""
        raise NotImplementedError("Resolver not implemented - will be added in T022")

    @strawberry.field
    async def regulated_by(
        self,
        info: strawberry.Info,
        first: int = 10,
        offset: int = 0
    ) -> List["Gene"]:
        """Genes that regulate this protein"""
        raise NotImplementedError("Resolver not implemented - will be added in T022")

    @strawberry.field
    async def participates_in(
        self,
        info: strawberry.Info,
        first: int = 10,
        offset: int = 0
    ) -> List["Pathway"]:
        """Pathways this protein participates in"""
        raise NotImplementedError("Resolver not implemented - will be added in T022")

    # Vector similarity field
    @strawberry.field
    async def similar(
        self,
        info: strawberry.Info,
        limit: int = 10,
        threshold: float = 0.7
    ) -> List["SimilarProtein"]:
        """Find similar proteins using vector embeddings"""
        raise NotImplementedError("Resolver not implemented - will be added in T024")


@strawberry.type
class Gene(Node):
    """Gene entity with encoded proteins and variants"""
    # Node interface fields
    id: strawberry.ID
    labels: List[str]
    properties: JSON
    created_at: DateTime = strawberry.field(name="createdAt")

    # Gene-specific fields
    name: str
    chromosome: Optional[str] = None
    position: Optional[int] = None

    # Relationship fields (resolvers to be implemented)
    @strawberry.field
    async def encodes(
        self,
        info: strawberry.Info,
        first: int = 10,
        offset: int = 0
    ) -> List[Protein]:
        """Proteins encoded by this gene"""
        raise NotImplementedError("Resolver not implemented - will be added in T022")

    @strawberry.field
    async def variants(
        self,
        info: strawberry.Info,
        first: int = 10,
        offset: int = 0
    ) -> List["Variant"]:
        """Genetic variants of this gene"""
        raise NotImplementedError("Resolver not implemented - will be added in T022")


@strawberry.type
class Pathway(Node):
    """Pathway entity with associated proteins and genes"""
    # Node interface fields
    id: strawberry.ID
    labels: List[str]
    properties: JSON
    created_at: DateTime = strawberry.field(name="createdAt")

    # Pathway-specific fields
    name: str
    description: Optional[str] = None

    # Relationship fields (resolvers to be implemented)
    @strawberry.field
    async def proteins(
        self,
        info: strawberry.Info,
        first: int = 10,
        offset: int = 0
    ) -> List[Protein]:
        """Proteins participating in this pathway"""
        raise NotImplementedError("Resolver not implemented - will be added in T022")

    @strawberry.field
    async def genes(
        self,
        info: strawberry.Info,
        first: int = 10,
        offset: int = 0
    ) -> List[Gene]:
        """Genes associated with this pathway"""
        raise NotImplementedError("Resolver not implemented - will be added in T022")


@strawberry.type
class Variant(Node):
    """Genetic variant entity"""
    # Node interface fields
    id: strawberry.ID
    labels: List[str]
    properties: JSON
    created_at: DateTime = strawberry.field(name="createdAt")

    # Variant-specific fields
    name: str
    rs_id: Optional[str] = strawberry.field(name="rsId", default=None)
    chromosome: Optional[str] = None
    position: Optional[int] = None


@strawberry.type
class Interaction:
    """Edge/Interaction between nodes"""
    source: Node
    target: Node
    type: str
    confidence: Optional[float] = None
    qualifiers: Optional[JSON] = None


@strawberry.type
class SimilarProtein:
    """Vector similarity result for proteins"""
    protein: Protein
    similarity: float
    distance: Optional[float] = None


@strawberry.type
class ProteinNeighborhood:
    """Result of neighborhood query"""
    center: Protein
    neighbors: List[Protein]
    interactions: List[Interaction]
    depth: int


@strawberry.type
class Path:
    """Result of path query (e.g., shortest path)"""
    nodes: List[Node]
    edges: List[Interaction]
    length: int


@strawberry.type
class GraphStats:
    """Graph statistics aggregates"""
    total_nodes: int = strawberry.field(name="totalNodes")
    total_edges: int = strawberry.field(name="totalEdges")
    nodes_by_label: JSON = strawberry.field(name="nodesByLabel")
    edges_by_type: JSON = strawberry.field(name="edgesByType")


# Input types for mutations and filters
@strawberry.input
class CreateProteinInput:
    """Input for creating a new protein"""
    id: strawberry.ID
    name: str
    function: Optional[str] = None
    organism: Optional[str] = None
    embedding: Optional[List[float]] = None  # 768-dimensional vector


@strawberry.input
class UpdateProteinInput:
    """Input for updating an existing protein"""
    name: Optional[str] = None
    function: Optional[str] = None
    confidence: Optional[float] = None


@strawberry.input
class ProteinFilter:
    """Filter for protein queries"""
    name: Optional[str] = None
    organism: Optional[str] = None
    confidence_min: Optional[float] = strawberry.field(name="confidenceMin", default=None)
    confidence_max: Optional[float] = strawberry.field(name="confidenceMax", default=None)
