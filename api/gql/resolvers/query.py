"""
GraphQL query resolvers for root Query type.

Resolves protein, gene, pathway queries using DataLoader pattern for efficient batching.
"""

import strawberry
from typing import Optional
from strawberry.types import Info

from ..types import Protein, Gene, Pathway
from ..loaders import ProteinLoader, GeneLoader, PathwayLoader


@strawberry.type
class Query:
    """Root GraphQL Query type"""

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
