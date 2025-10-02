"""
Biomedical Domain Resolver

Implements domain-specific node resolution and query/mutation fields
for biomedical knowledge graphs (Protein, Gene, Pathway, etc.).

This is an EXAMPLE domain implementation showing how to extend
the generic graph core with typed domain entities.
"""

import strawberry
from typing import Optional, Dict, Any, List
from strawberry.types import Info

from api.gql.core.domain_resolver import DomainResolver
from .types import Protein, Gene, Pathway, CreateProteinInput, UpdateProteinInput
from .loaders import ProteinLoader, GeneLoader, PathwayLoader


class BiomedicalDomainResolver(DomainResolver):
    """
    Domain resolver for biomedical entities.

    Resolves nodes with Protein, Gene, or Pathway labels to
    their respective typed classes.
    """

    async def resolve_node(
        self,
        info: Info,
        node_id: str,
        labels: List[str],
        properties: Dict[str, Any],
        created_at: Any,
    ) -> Optional[Any]:
        """
        Resolve node to biomedical domain type based on labels.

        Args:
            info: GraphQL Info context
            node_id: Node ID
            labels: Node labels
            properties: Node properties
            created_at: Node creation timestamp

        Returns:
            Protein, Gene, or Pathway instance, or None if not biomedical
        """
        # Check if this is a Protein
        if "Protein" in labels:
            loader: ProteinLoader = info.context["protein_loader"]
            protein_data = await loader.load(node_id)
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

        # Check if this is a Gene
        if "Gene" in labels:
            loader: GeneLoader = info.context["gene_loader"]
            gene_data = await loader.load(node_id)
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

        # Check if this is a Pathway
        if "Pathway" in labels:
            loader: PathwayLoader = info.context["pathway_loader"]
            pathway_data = await loader.load(node_id)
            if pathway_data:
                return Pathway(
                    id=strawberry.ID(pathway_data["id"]),
                    labels=pathway_data.get("labels", []),
                    properties=pathway_data.get("properties", {}),
                    created_at=pathway_data.get("created_at"),
                    name=pathway_data.get("name", ""),
                    description=pathway_data.get("description"),
                )

        # Not a biomedical entity
        return None

    def get_query_fields(self) -> Dict[str, Any]:
        """
        Register biomedical-specific query fields.

        Adds protein(), gene(), pathway() queries to the schema.
        """
        return {
            "protein": self._protein_query,
            "gene": self._gene_query,
            "pathway": self._pathway_query,
        }

    def get_mutation_fields(self) -> Dict[str, Any]:
        """
        Register biomedical-specific mutation fields.

        Adds createProtein(), updateProtein(), deleteProtein() mutations.
        """
        return {
            "createProtein": self._create_protein_mutation,
            "updateProtein": self._update_protein_mutation,
            "deleteProtein": self._delete_protein_mutation,
        }

    # Query resolvers
    async def _protein_query(
        self, info: Info, id: strawberry.ID
    ) -> Optional[Protein]:
        """
        Query a protein by ID.

        Convenience wrapper around Query.node() that returns
        Protein type directly.
        """
        loader: ProteinLoader = info.context["protein_loader"]
        protein_data = await loader.load(str(id))

        if protein_data is None:
            return None

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

    async def _gene_query(self, info: Info, id: strawberry.ID) -> Optional[Gene]:
        """Query a gene by ID."""
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

    async def _pathway_query(
        self, info: Info, id: strawberry.ID
    ) -> Optional[Pathway]:
        """Query a pathway by ID."""
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

    # Mutation resolvers
    async def _create_protein_mutation(
        self, info: Info, input: CreateProteinInput
    ) -> Protein:
        """
        Create a new protein with optional embedding vector.

        Creates:
        - nodes.node_id entry
        - rdf_labels entry with "Protein" label
        - rdf_props entries for name, function, organism
        - kg_NodeEmbeddings entry if embedding provided
        """
        db_connection = info.context.get("db_connection")
        cursor = db_connection.cursor()

        # Check if protein already exists
        cursor.execute(
            "SELECT COUNT(*) FROM nodes WHERE node_id = ?", (str(input.id),)
        )
        if cursor.fetchone()[0] > 0:
            raise Exception(f"Protein with ID {input.id} already exists")

        # Create node + labels + properties
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", (str(input.id),))
        cursor.execute(
            "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
            (str(input.id), "Protein"),
        )
        cursor.execute(
            "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
            (str(input.id), "name", input.name),
        )

        if input.function:
            cursor.execute(
                "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                (str(input.id), "function", input.function),
            )

        if input.organism:
            cursor.execute(
                "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                (str(input.id), "organism", input.organism),
            )

        # CRITICAL: Commit nodes before embeddings (FK validation)
        db_connection.commit()

        # Add embedding if provided
        if input.embedding and len(input.embedding) > 0:
            if len(input.embedding) != 768:
                raise Exception(
                    f"Embedding must be 768-dimensional, got {len(input.embedding)}"
                )
            emb_str = "[" + ",".join([str(x) for x in input.embedding]) + "]"
            cursor.execute(
                "INSERT INTO kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?))",
                (str(input.id), emb_str),
            )

        db_connection.commit()

        # Load created protein using ProteinLoader
        protein_loader = info.context["protein_loader"]
        protein_data = await protein_loader.load(str(input.id))

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

    async def _update_protein_mutation(
        self, info: Info, id: strawberry.ID, input: UpdateProteinInput
    ) -> Protein:
        """
        Update an existing protein's fields.

        Uses UPSERT pattern (UPDATE if exists, INSERT if not).
        """
        db_connection = info.context.get("db_connection")
        cursor = db_connection.cursor()

        # Check if protein exists
        cursor.execute("SELECT COUNT(*) FROM nodes WHERE node_id = ?", (str(id),))
        if cursor.fetchone()[0] == 0:
            raise Exception(f"Protein with ID {id} not found")

        # Update fields using UPSERT pattern
        if input.name is not None:
            cursor.execute(
                "DELETE FROM rdf_props WHERE s = ? AND key = ?", (str(id), "name")
            )
            cursor.execute(
                "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                (str(id), "name", input.name),
            )

        if input.function is not None:
            cursor.execute(
                "DELETE FROM rdf_props WHERE s = ? AND key = ?", (str(id), "function")
            )
            cursor.execute(
                "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                (str(id), "function", input.function),
            )

        if input.confidence is not None:
            cursor.execute(
                "DELETE FROM rdf_props WHERE s = ? AND key = ?",
                (str(id), "confidence"),
            )
            cursor.execute(
                "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                (str(id), "confidence", str(input.confidence)),
            )

        db_connection.commit()

        # Clear DataLoader cache
        protein_loader = info.context["protein_loader"]
        try:
            protein_loader.clear(str(id))
        except KeyError:
            pass  # Not in cache

        # Reload protein
        protein_data = await protein_loader.load(str(id))

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

    async def _delete_protein_mutation(
        self, info: Info, id: strawberry.ID
    ) -> bool:
        """
        Delete a protein.

        Deletes in FK constraint order: embeddings → edges → props → labels → nodes
        """
        db_connection = info.context.get("db_connection")
        cursor = db_connection.cursor()

        # Check if protein exists
        cursor.execute("SELECT COUNT(*) FROM nodes WHERE node_id = ?", (str(id),))
        if cursor.fetchone()[0] == 0:
            raise Exception(f"Protein with ID {id} not found")

        # Delete in reverse FK order
        cursor.execute("DELETE FROM kg_NodeEmbeddings WHERE id = ?", (str(id),))
        cursor.execute("DELETE FROM rdf_edges WHERE s = ? OR o_id = ?", (str(id), str(id)))
        cursor.execute("DELETE FROM rdf_props WHERE s = ?", (str(id),))
        cursor.execute("DELETE FROM rdf_labels WHERE s = ?", (str(id),))
        cursor.execute("DELETE FROM nodes WHERE node_id = ?", (str(id),))

        db_connection.commit()

        # Clear DataLoader cache
        protein_loader = info.context["protein_loader"]
        try:
            protein_loader.clear(str(id))
        except KeyError:
            pass

        return True
