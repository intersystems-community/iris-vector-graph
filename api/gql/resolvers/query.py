"""
GraphQL query resolvers for root Query type.

Resolves both:
1. Generic node queries (node, nodes) for any domain
2. Domain-specific queries (protein, gene, pathway) as convenience wrappers

DESIGN NOTE: The biomedical queries (protein, gene, pathway) are EXAMPLE
implementations. Users can create custom domains by using the generic node()
query or implementing their own domain-specific queries.
"""

import strawberry
from typing import Optional, List
from strawberry.types import Info

from ..types import Protein, Gene, Pathway, Node
from ..loaders import ProteinLoader, GeneLoader, PathwayLoader


class GenericNode(Node):
    """
    Generic node implementation for any domain.

    Used by node() query to return nodes of unknown type.
    Clients can use __typename to determine actual type.
    """
    pass


@strawberry.type
class Query:
    """
    Root GraphQL Query type.

    Provides both generic graph queries (node, nodes) and domain-specific
    convenience queries (protein, gene, pathway).
    """

    @strawberry.field
    async def node(self, info: Info, id: strawberry.ID) -> Optional[Node]:
        """
        Generic node query by ID.

        Queries any node regardless of label/type. Returns Node interface
        which can be downcast to domain-specific types via GraphQL fragments.

        Example:
            query {
              node(id: "PROTEIN:TP53") {
                __typename
                id
                labels
                properties
                property(key: "name")

                ... on Protein {
                  name
                  function
                }
              }
            }

        Args:
            id: Node ID (e.g., "PROTEIN:TP53", "GENE:BRCA1", "CUSTOM:123")

        Returns:
            Node object if found, None otherwise
        """
        # Try each loader to find the node
        # TODO: Optimize with single SQL query instead of trying loaders sequentially
        db_connection = info.context.get("db_connection")
        if not db_connection:
            return None

        cursor = db_connection.cursor()

        # Query nodes table directly
        cursor.execute("SELECT COUNT(*) FROM nodes WHERE node_id = ?", (str(id),))
        if cursor.fetchone()[0] == 0:
            return None

        # Load labels
        cursor.execute("SELECT label FROM rdf_labels WHERE s = ?", (str(id),))
        labels = [row[0] for row in cursor.fetchall()]

        # Load properties
        cursor.execute("SELECT key, val FROM rdf_props WHERE s = ?", (str(id),))
        properties = {row[0]: row[1] for row in cursor.fetchall()}

        # Load created_at
        cursor.execute("SELECT created_at FROM nodes WHERE node_id = ?", (str(id),))
        created_at = cursor.fetchone()[0]

        # Determine type from labels and return appropriate concrete type
        if "Protein" in labels:
            loader: ProteinLoader = info.context["protein_loader"]
            protein_data = await loader.load(str(id))
            if protein_data:
                return Protein(
                    id=strawberry.ID(protein_data["id"]),
                    labels=protein_data.get("labels", []),
                    properties=protein_data.get("properties", {}),
                    created_at=protein_data.get("created_at"),
                    name=protein_data.get("name", ""),
                    function=protein_data.get("function"),
                    organism=protein_data.get("organism"),
                    confidence=protein_data.get("confidence"),
                )

        if "Gene" in labels:
            loader: GeneLoader = info.context["gene_loader"]
            gene_data = await loader.load(str(id))
            if gene_data:
                return Gene(
                    id=strawberry.ID(gene_data["id"]),
                    labels=gene_data.get("labels", []),
                    properties=gene_data.get("properties", {}),
                    created_at=gene_data.get("created_at"),
                    name=gene_data.get("name", ""),
                    chromosome=gene_data.get("chromosome"),
                    position=gene_data.get("position"),
                )

        if "Pathway" in labels:
            loader: PathwayLoader = info.context["pathway_loader"]
            pathway_data = await loader.load(str(id))
            if pathway_data:
                return Pathway(
                    id=strawberry.ID(pathway_data["id"]),
                    labels=pathway_data.get("labels", []),
                    properties=pathway_data.get("properties", {}),
                    created_at=pathway_data.get("created_at"),
                    name=pathway_data.get("name", ""),
                    description=pathway_data.get("description"),
                )

        # Unknown label - return generic node
        return GenericNode(
            id=strawberry.ID(str(id)),
            labels=labels,
            properties=properties,
            created_at=created_at,
        )

    @strawberry.field
    async def protein(self, info: Info, id: strawberry.ID) -> Optional[Protein]:
        """
        Query a protein by ID.

        Args:
            id: Protein node ID (e.g., "PROTEIN:TP53")

        Returns:
            Protein object if found, None otherwise
        """
        loader: ProteinLoader = info.context["protein_loader"]
        protein_data = await loader.load(str(id))

        if protein_data is None:
            return None

        # Convert raw data to Protein type
        return Protein(
            id=strawberry.ID(protein_data["id"]),
            labels=protein_data.get("labels", []),
            properties=protein_data.get("properties", {}),
            created_at=protein_data.get("created_at"),
            name=protein_data.get("name", ""),
            function=protein_data.get("function"),
            organism=protein_data.get("organism"),
            confidence=protein_data.get("confidence"),
        )

    @strawberry.field
    async def gene(self, info: Info, id: strawberry.ID) -> Optional[Gene]:
        """
        Query a gene by ID.

        Args:
            id: Gene node ID (e.g., "GENE:TP53")

        Returns:
            Gene object if found, None otherwise
        """
        loader: GeneLoader = info.context["gene_loader"]
        gene_data = await loader.load(str(id))

        if gene_data is None:
            return None

        return Gene(
            id=strawberry.ID(gene_data["id"]),
            labels=gene_data.get("labels", []),
            properties=gene_data.get("properties", {}),
            created_at=gene_data.get("created_at"),
            name=gene_data.get("name", ""),
            chromosome=gene_data.get("chromosome"),
            position=gene_data.get("position"),
        )

    @strawberry.field
    async def pathway(self, info: Info, id: strawberry.ID) -> Optional[Pathway]:
        """
        Query a pathway by ID.

        Args:
            id: Pathway node ID (e.g., "PATHWAY:P53_SIGNALING")

        Returns:
            Pathway object if found, None otherwise
        """
        loader: PathwayLoader = info.context["pathway_loader"]
        pathway_data = await loader.load(str(id))

        if pathway_data is None:
            return None

        return Pathway(
            id=strawberry.ID(pathway_data["id"]),
            labels=pathway_data.get("labels", []),
            properties=pathway_data.get("properties", {}),
            created_at=pathway_data.get("created_at"),
            name=pathway_data.get("name", ""),
            description=pathway_data.get("description"),
        )
