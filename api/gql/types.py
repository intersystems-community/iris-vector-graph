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
    """
    Base interface for all graph entities.

    DESIGN NOTE: This interface provides both:
    1. Generic accessors (property, neighbors) for any domain
    2. Domain-specific implementations (Protein, Gene) as convenience wrappers

    The biomedical types (Protein, Gene, Pathway) are EXAMPLE implementations.
    Users can create custom domains by implementing this interface.
    """
    id: strawberry.ID
    labels: List[str]
    properties: JSON
    created_at: DateTime = strawberry.field(name="createdAt")

    @strawberry.field
    def property(self, key: str) -> Optional[str]:
        """
        Generic property accessor.

        Get any property value by key from the properties JSON.
        This enables querying properties not defined as typed fields.

        Example:
            node { property(key: "custom_annotation") }
        """
        if isinstance(self.properties, dict):
            return self.properties.get(key)
        return None


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
        # Load edges for this protein using EdgeLoader
        edge_loader = info.context["edge_loader"]
        edges = await edge_loader.load(str(self.id))

        # Filter for INTERACTS_WITH relationships
        interaction_edges = [e for e in edges if e["type"] == "INTERACTS_WITH"]

        # Apply pagination
        paginated_edges = interaction_edges[offset:offset + first]

        # Load target proteins using ProteinLoader (batched!)
        protein_loader = info.context["protein_loader"]
        target_ids = [e["target_id"] for e in paginated_edges]

        if not target_ids:
            return []

        # Batch load all target proteins in single query
        proteins_data = await protein_loader.load_many(target_ids)

        # Convert to Protein objects
        proteins = []
        for data in proteins_data:
            if data:
                proteins.append(Protein(
                    id=strawberry.ID(data["id"]),
                    labels=data.get("labels", []),
                    properties=data.get("properties", {}),
                    created_at=data.get("created_at"),
                    name=data.get("name", ""),
                    function=data.get("function"),
                    organism=data.get("organism"),
                    confidence=data.get("confidence"),
                ))

        return proteins

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
        """Find similar proteins using vector embeddings with HNSW index"""
        engine = info.context.get("engine")
        if not engine:
            return []

        try:
            results = engine.search_nodes_by_vector(
                query=self.id,
                k=limit + 1,
                label_filter="Protein",
            )
        except Exception:
            return []

        if not results:
            return []

        similar_results = []
        for node_id, similarity in results:
            if node_id == str(self.id):
                continue
            if similarity < threshold:
                continue

            similar_results.append((node_id, similarity))
            if len(similar_results) >= limit:
                break

        if not similar_results:
            return []

        node_ids = [nid for nid, _ in similar_results]
        nodes_data = engine.get_nodes(node_ids)

        results_out = []
        for i, (node_id, similarity) in enumerate(similar_results):
            if i < len(nodes_data):
                node_data = nodes_data[i]
                if node_data:
                    protein = Protein(
                        id=strawberry.ID(node_data["id"]),
                        labels=node_data.get("labels", []),
                        properties=node_data.get("properties", {}),
                        created_at=node_data.get("created_at"),
                        name=node_data.get("name", ""),
                        function=node_data.get("function"),
                        organism=node_data.get("organism"),
                        confidence=node_data.get("confidence"),
                    )
                    results_out.append(SimilarProtein(
                        protein=protein,
                        similarity=similarity,
                        distance=None
                    ))

        return results_out


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
        # Load edges for this gene using EdgeLoader
        edge_loader = info.context["edge_loader"]
        edges = await edge_loader.load(str(self.id))

        # Filter for ENCODES relationships
        encodes_edges = [e for e in edges if e["type"] == "ENCODES"]

        # Apply pagination
        paginated_edges = encodes_edges[offset:offset + first]

        # Load target proteins using ProteinLoader (batched!)
        protein_loader = info.context["protein_loader"]
        target_ids = [e["target_id"] for e in paginated_edges]

        if not target_ids:
            return []

        # Batch load all target proteins in single query
        proteins_data = await protein_loader.load_many(target_ids)

        # Convert to Protein objects
        proteins = []
        for data in proteins_data:
            if data:
                proteins.append(Protein(
                    id=strawberry.ID(data["id"]),
                    labels=data.get("labels", []),
                    properties=data.get("properties", {}),
                    created_at=data.get("created_at"),
                    name=data.get("name", ""),
                    function=data.get("function"),
                    organism=data.get("organism"),
                    confidence=data.get("confidence"),
                ))

        return proteins

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
