"""
Biomedical Domain Types for IRIS Vector Graph API

Domain-specific GraphQL types for biomedical knowledge graphs.
These types extend the generic Node interface with typed fields.

This is an EXAMPLE domain implementation demonstrating how to create
domain-specific types on top of the generic graph core.
"""

import strawberry
from typing import List, Optional
from api.gql.core.types import Node, DateTime, JSON


@strawberry.type
class Protein(Node):
    """
    Protein entity with relationships and vector similarity.

    Domain-specific type providing typed access to protein properties.
    Extends generic Node interface with biomedical-specific fields.
    """
    # Node interface fields
    id: strawberry.ID
    labels: List[str]
    properties: JSON
    created_at: DateTime = strawberry.field(name="createdAt")

    # Protein-specific typed fields (convenience accessors)
    name: str
    function: Optional[str] = None
    organism: Optional[str] = None
    confidence: Optional[float] = None

    # Relationship fields (resolvers implemented in resolvers.py)
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
        raise NotImplementedError("Resolver not implemented - will be added in future")

    @strawberry.field
    async def participates_in(
        self,
        info: strawberry.Info,
        first: int = 10,
        offset: int = 0
    ) -> List["Pathway"]:
        """Pathways this protein participates in"""
        raise NotImplementedError("Resolver not implemented - will be added in future")

    # Vector similarity field
    @strawberry.field
    async def similar(
        self,
        info: strawberry.Info,
        limit: int = 10,
        threshold: float = 0.7
    ) -> List["SimilarProtein"]:
        """Find similar proteins using vector embeddings.

        This used to hand-write SQL against `Graph_KG.kg_NodeEmbeddings` keyed on an
        `id` column, and answered `[]` in every deployment for three reasons:

        * post-4.0.0 that table is keyed `(graph_id, node_id)` and has no `id`
          column — but a DDL-created table still carries an implicit RowID spelled
          `ID`, so `WHERE id = 'PROTEIN:TP53'` parses, matches nothing, and the
          existence check returned early;
        * spec 227 routes vectors to a per-`(graph, model)` table, so one hardcoded
          table name is not where a graph's vectors necessarily live;
        * both queries were wrapped in `except Exception: return []`, which reported
          "no similar proteins" for every failure, including the one above.

        The engine seam handles routing, graph scope and the KNN itself. The search
        runs in the default graph, which is the only graph this example addresses.
        """
        engine = info.context.get("engine")
        if engine is None:
            db_connection = info.context.get("db_connection")
            if db_connection is None:
                return []
            from iris_vector_graph.engine import IRISGraphEngine

            engine = IRISGraphEngine(db_connection)

        own = engine.get_embedding(str(self.id))
        if not own or not own.get("embedding"):
            return []

        # Not wrapped in a blanket `except`: a failed KNN must reach the caller as a
        # GraphQL error rather than as "this protein has no similar proteins".
        rows = engine.search_nodes_by_vector(
            query=list(own["embedding"]),
            k=limit + 1,
            label_filter="Protein",
        )

        similar_results = []
        for node_id, similarity in rows:
            if node_id == str(self.id):
                continue
            if similarity < threshold:
                continue
            similar_results.append((node_id, float(similarity)))
            if len(similar_results) >= limit:
                break

        if not similar_results:
            return []

        # Keyed by ID, not by position: `get_nodes` drops an ID it cannot find, and
        # indexing by position then pairs a node with another node's similarity.
        nodes_data = {
            n["id"]: n for n in engine.get_nodes([nid for nid, _ in similar_results]) if n
        }

        results = []
        for node_id, similarity in similar_results:
            protein_data = nodes_data.get(node_id)
            if not protein_data:
                continue
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
                distance=None,  # Distance not computed by the KNN path
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
        raise NotImplementedError("Resolver not implemented - will be added in future")


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
        raise NotImplementedError("Resolver not implemented - will be added in future")

    @strawberry.field
    async def genes(
        self,
        info: strawberry.Info,
        first: int = 10,
        offset: int = 0
    ) -> List[Gene]:
        """Genes associated with this pathway"""
        raise NotImplementedError("Resolver not implemented - will be added in future")


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


# Result types for biomedical queries
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
    depth: int


# Input types for biomedical mutations
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
