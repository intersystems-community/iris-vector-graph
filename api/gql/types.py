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
        """
        Find similar proteins using vector embeddings with HNSW index.

        REQUIRES: InterSystems IRIS 2025.1+ with Vector Search feature enabled.

        NOTE: This implementation will return empty results if VECTOR functions
        are not available. For full vector search support, use IRIS with:
        - VECTOR_DOT_PRODUCT() function
        - VECTOR_COSINE() function
        - HNSW index on kg_NodeEmbeddings.emb

        See docs/setup/IRIS_PASSWORD_RESET.md for IRIS version requirements.
        """
        # Get database connection from context
        db_connection = info.context.get("db_connection")
        if not db_connection:
            return []

        cursor = db_connection.cursor()

        # Check if embeddings exist for this protein
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM kg_NodeEmbeddings WHERE id = ?",
                (str(self.id),)
            )
            count = cursor.fetchone()[0]
            if count == 0:
                # No embedding for this protein
                return []
        except Exception:
            return []

        # Try to use VECTOR functions if available (IRIS 2025.1+)
        # Query using HNSW vector search with VECTOR_DOT_PRODUCT
        query = """
            SELECT TOP ?
                e2.id,
                VECTOR_DOT_PRODUCT(e1.emb, e2.emb) as similarity
            FROM kg_NodeEmbeddings e1,
                 kg_NodeEmbeddings e2
            WHERE e1.id = ?
              AND e2.id != ?
              AND VECTOR_DOT_PRODUCT(e1.emb, e2.emb) >= ?
            ORDER BY similarity DESC
        """

        try:
            cursor.execute(query, (limit, str(self.id), str(self.id), threshold))
            rows = cursor.fetchall()
        except Exception as e:
            # VECTOR functions not available - requires IRIS 2025.1+ with Vector Search
            # Return empty list (graceful degradation)
            return []

        if not rows:
            return []

        # Batch load proteins using ProteinLoader
        protein_loader = info.context["protein_loader"]
        protein_ids = [row[0] for row in rows]

        proteins_data = await protein_loader.load_many(protein_ids)

        # Build SimilarProtein results
        results = []
        for i, row in enumerate(rows):
            protein_id = row[0]
            similarity = float(row[1]) if row[1] is not None else 0.0

            protein_data = proteins_data[i]
            if protein_data:
                protein = Protein(
                    id=strawberry.ID(protein_data["id"]),
                    labels=protein_data.get("labels", []),
                    properties=protein_data.get("properties", {}),
                    created_at=protein_data.get("created_at"),
                    name=protein_data.get("name", ""),
                    function=protein_data.get("function"),
                    organism=protein_data.get("organism"),
                    confidence=protein_data.get("confidence"),
                )

                results.append(SimilarProtein(
                    protein=protein,
                    similarity=similarity,
                    distance=None  # Distance not computed in this query
                ))

        return results


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
