"""
Integration tests for GraphQL query resolvers.

Tests protein, gene, pathway query resolvers against live IRIS database.
All tests marked @pytest.mark.requires_database for live database validation.
"""

import pytest
from typing import Optional

# TDD Gate: Tests will initially fail until schema and resolvers are implemented
try:
    from api.gql.schema import schema
    from api.gql.loaders import ProteinLoader, GeneLoader, PathwayLoader
    SCHEMA_EXISTS = True
except ImportError as e:
    SCHEMA_EXISTS = False
    schema = None


@pytest.mark.requires_database
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not SCHEMA_EXISTS, reason="Schema not implemented yet - TDD gate")
class TestProteinQueryResolver:
    """Integration tests for protein query resolver"""

    async def test_protein_query_simple_lookup(self, iris_connection):
        """Test protein(id) query returns correct fields"""
        # Setup: Create test protein in database
        cursor = iris_connection.cursor()

        # Ensure node exists (IRIS SQL compatible)
        try:
            cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ("PROTEIN:TP53_TEST",))
        except:
            pass  # Already exists

        # Add label
        try:
            cursor.execute(
                "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
                ("PROTEIN:TP53_TEST", "Protein")
            )
        except:
            pass

        # Add properties
        try:
            cursor.execute(
                "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                ("PROTEIN:TP53_TEST", "name", "Tumor protein p53")
            )
        except:
            pass
        try:
            cursor.execute(
                "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                ("PROTEIN:TP53_TEST", "function", "Tumor suppressor protein")
            )
        except:
            pass
        iris_connection.commit()

        # Execute GraphQL query
        query = """
            query GetProtein($id: ID!) {
                protein(id: $id) {
                    id
                    name
                    function
                    labels
                }
            }
        """

        result = await schema.execute(
            query,
            variable_values={"id": "PROTEIN:TP53_TEST"},
            context_value={
                "protein_loader": ProteinLoader(iris_connection),
                "gene_loader": GeneLoader(iris_connection),
                "pathway_loader": PathwayLoader(iris_connection),
            }
        )

        # Validate result
        assert result.errors is None, f"GraphQL errors: {result.errors}"
        assert result.data is not None

        protein = result.data["protein"]
        assert protein is not None
        assert protein["id"] == "PROTEIN:TP53_TEST"
        assert protein["name"] == "Tumor protein p53"
        assert protein["function"] == "Tumor suppressor protein"
        assert "Protein" in protein["labels"]

    async def test_protein_query_not_found(self, iris_connection):
        """Test protein(id) query returns None for non-existent protein"""
        query = """
            query GetProtein($id: ID!) {
                protein(id: $id) {
                    id
                    name
                }
            }
        """

        result = await schema.execute(
            query,
            variable_values={"id": "PROTEIN:NONEXISTENT"},
            context_value={
                "protein_loader": ProteinLoader(iris_connection),
                "gene_loader": GeneLoader(iris_connection),
                "pathway_loader": PathwayLoader(iris_connection),
            }
        )

        # Should return null (not an error)
        assert result.errors is None
        assert result.data["protein"] is None

    async def test_protein_query_optional_fields(self, iris_connection):
        """Test protein query handles missing optional fields gracefully"""
        cursor = iris_connection.cursor()

        # Create minimal protein (only required fields)
        try:
            cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ("PROTEIN:MINIMAL_TEST",))
        except:
            pass
        try:
            cursor.execute(
                "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
                ("PROTEIN:MINIMAL_TEST", "Protein")
            )
        except:
            pass
        try:
            cursor.execute(
                "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                ("PROTEIN:MINIMAL_TEST", "name", "Minimal Protein")
            )
        except:
            pass
        iris_connection.commit()

        query = """
            query GetProtein($id: ID!) {
                protein(id: $id) {
                    id
                    name
                    function
                    organism
                    confidence
                }
            }
        """

        result = await schema.execute(
            query,
            variable_values={"id": "PROTEIN:MINIMAL_TEST"},
            context_value={
                "protein_loader": ProteinLoader(iris_connection),
                "gene_loader": GeneLoader(iris_connection),
                "pathway_loader": PathwayLoader(iris_connection),
            }
        )

        assert result.errors is None
        protein = result.data["protein"]
        assert protein["name"] == "Minimal Protein"
        assert protein["function"] is None  # Optional field not set
        assert protein["organism"] is None
        assert protein["confidence"] is None


@pytest.mark.requires_database
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not SCHEMA_EXISTS, reason="Schema not implemented yet - TDD gate")
class TestGeneQueryResolver:
    """Integration tests for gene query resolver"""

    async def test_gene_query_simple_lookup(self, iris_connection):
        """Test gene(id) query returns correct fields"""
        cursor = iris_connection.cursor()

        # Setup test gene
        try:
            cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ("GENE:TP53",))
        except:
            pass
        try:
            cursor.execute(
                "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
                ("GENE:TP53", "Gene")
            )
        except:
            pass
        try:
            cursor.execute(
                "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                ("GENE:TP53", "name", "TP53")
            )
        except:
            pass
        try:
            cursor.execute(
                "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                ("GENE:TP53", "chromosome", "17")
            )
        except:
            pass
        iris_connection.commit()

        query = """
            query GetGene($id: ID!) {
                gene(id: $id) {
                    id
                    name
                    chromosome
                    labels
                }
            }
        """

        result = await schema.execute(
            query,
            variable_values={"id": "GENE:TP53"},
            context_value={
                "protein_loader": ProteinLoader(iris_connection),
                "gene_loader": GeneLoader(iris_connection),
                "pathway_loader": PathwayLoader(iris_connection),
            }
        )

        assert result.errors is None
        gene = result.data["gene"]
        assert gene["id"] == "GENE:TP53"
        assert gene["name"] == "TP53"
        assert gene["chromosome"] == "17"
        assert "Gene" in gene["labels"]


@pytest.mark.requires_database
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not SCHEMA_EXISTS, reason="Schema not implemented yet - TDD gate")
class TestPathwayQueryResolver:
    """Integration tests for pathway query resolver"""

    async def test_pathway_query_simple_lookup(self, iris_connection):
        """Test pathway(id) query returns correct fields"""
        cursor = iris_connection.cursor()

        # Setup test pathway
        try:
            cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ("PATHWAY:P53_SIGNALING",))
        except:
            pass
        try:
            cursor.execute(
                "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
                ("PATHWAY:P53_SIGNALING", "Pathway")
            )
        except:
            pass
        try:
            cursor.execute(
                "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                ("PATHWAY:P53_SIGNALING", "name", "p53 signaling pathway")
            )
        except:
            pass
        try:
            cursor.execute(
                "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                ("PATHWAY:P53_SIGNALING", "description", "Tumor suppressor pathway")
            )
        except:
            pass
        iris_connection.commit()

        query = """
            query GetPathway($id: ID!) {
                pathway(id: $id) {
                    id
                    name
                    description
                    labels
                }
            }
        """

        result = await schema.execute(
            query,
            variable_values={"id": "PATHWAY:P53_SIGNALING"},
            context_value={
                "protein_loader": ProteinLoader(iris_connection),
                "gene_loader": GeneLoader(iris_connection),
                "pathway_loader": PathwayLoader(iris_connection),
            }
        )

        assert result.errors is None
        pathway = result.data["pathway"]
        assert pathway["id"] == "PATHWAY:P53_SIGNALING"
        assert pathway["name"] == "p53 signaling pathway"
        assert pathway["description"] == "Tumor suppressor pathway"
        assert "Pathway" in pathway["labels"]


# NOTE: iris_connection fixture is provided by tests/conftest.py
# Do not define a local fixture here to avoid shadowing


@pytest.fixture
def query_test_cleanup(iris_connection):
    """Fixture to cleanup query test data"""
    yield

    # Cleanup: Remove test data
    cursor = iris_connection.cursor()
    test_nodes = [
        "PROTEIN:TP53_TEST",
        "PROTEIN:MINIMAL_TEST",
        "GENE:TP53",
        "PATHWAY:P53_SIGNALING"
    ]

    for node_id in test_nodes:
        try:
            cursor.execute("DELETE FROM rdf_props WHERE s = ?", (node_id,))
            cursor.execute("DELETE FROM rdf_labels WHERE s = ?", (node_id,))
            cursor.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
        except:
            pass

    iris_connection.commit()
