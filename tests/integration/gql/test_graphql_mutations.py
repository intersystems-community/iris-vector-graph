"""
Integration tests for GraphQL mutation resolvers.

Tests create, update, delete operations with FK constraint validation.
All tests use live IRIS database per constitution requirements.
"""

import pytest
import numpy as np
from typing import Optional

# TDD Gate: Tests will initially fail until mutation resolvers are implemented
try:
    from api.gql.schema import schema
    from api.gql.loaders import ProteinLoader, GeneLoader, PathwayLoader, EdgeLoader
    SCHEMA_EXISTS = True
except ImportError as e:
    SCHEMA_EXISTS = False
    schema = None


@pytest.mark.requires_database
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not SCHEMA_EXISTS, reason="Schema not implemented yet - TDD gate")
class TestCreateProteinMutation:
    """Integration tests for createProtein mutation with FK validation"""

    async def test_create_protein_basic(self, iris_connection):
        """Test createProtein mutation creates node with properties"""
        query = """
            mutation CreateProtein($input: CreateProteinInput!) {
                createProtein(input: $input) {
                    id
                    name
                    function
                    organism
                    labels
                }
            }
        """

        result = await schema.execute(
            query,
            variable_values={
                "input": {
                    "id": "PROTEIN:TEST_CREATE",
                    "name": "Test Protein",
                    "function": "Testing function",
                    "organism": "Homo sapiens"
                }
            },
            context_value={
                "protein_loader": ProteinLoader(iris_connection),
                "gene_loader": GeneLoader(iris_connection),
                "pathway_loader": PathwayLoader(iris_connection),
                "edge_loader": EdgeLoader(iris_connection),
                "db_connection": iris_connection,
            }
        )

        # Validate result
        assert result.errors is None, f"GraphQL errors: {result.errors}"
        protein = result.data["createProtein"]
        assert protein["id"] == "PROTEIN:TEST_CREATE"
        assert protein["name"] == "Test Protein"
        assert protein["function"] == "Testing function"
        assert protein["organism"] == "Homo sapiens"
        assert "Protein" in protein["labels"]

        # Verify data in database
        cursor = iris_connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM nodes WHERE node_id = ?", ("PROTEIN:TEST_CREATE",))
        assert cursor.fetchone()[0] == 1

        cursor.execute("SELECT val FROM rdf_props WHERE s = ? AND key = ?",
                      ("PROTEIN:TEST_CREATE", "name"))
        assert cursor.fetchone()[0] == "Test Protein"

    async def test_create_protein_with_embedding(self, iris_connection):
        """Test createProtein with 768-dimensional embedding vector"""
        # Cleanup any existing test data first
        cursor = iris_connection.cursor()
        try:
            cursor.execute("DELETE FROM kg_NodeEmbeddings WHERE id = ?", ("PROTEIN:TEST_WITH_EMB",))
            cursor.execute("DELETE FROM rdf_props WHERE s = ?", ("PROTEIN:TEST_WITH_EMB",))
            cursor.execute("DELETE FROM rdf_labels WHERE s = ?", ("PROTEIN:TEST_WITH_EMB",))
            cursor.execute("DELETE FROM nodes WHERE node_id = ?", ("PROTEIN:TEST_WITH_EMB",))
            iris_connection.commit()
        except:
            iris_connection.rollback()

        # Generate normalized 768-dimensional embedding
        emb = np.random.randn(768)
        emb = emb / np.linalg.norm(emb)

        query = """
            mutation CreateProtein($input: CreateProteinInput!) {
                createProtein(input: $input) {
                    id
                    name
                    similar(limit: 5, threshold: 0.0) {
                        protein {
                            id
                        }
                        similarity
                    }
                }
            }
        """

        result = await schema.execute(
            query,
            variable_values={
                "input": {
                    "id": "PROTEIN:TEST_WITH_EMB",
                    "name": "Embedded Protein",
                    "embedding": emb.tolist()
                }
            },
            context_value={
                "protein_loader": ProteinLoader(iris_connection),
                "gene_loader": GeneLoader(iris_connection),
                "pathway_loader": PathwayLoader(iris_connection),
                "edge_loader": EdgeLoader(iris_connection),
                "db_connection": iris_connection,
            }
        )

        assert result.errors is None, f"GraphQL errors: {result.errors}"
        protein = result.data["createProtein"]
        assert protein["id"] == "PROTEIN:TEST_WITH_EMB"

        # Verify embedding in database
        cursor = iris_connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM kg_NodeEmbeddings WHERE id = ?",
                      ("PROTEIN:TEST_WITH_EMB",))
        assert cursor.fetchone()[0] == 1

    async def test_create_protein_duplicate_id_error(self, iris_connection):
        """Test createProtein returns error for duplicate ID"""
        # First create
        cursor = iris_connection.cursor()
        try:
            cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ("PROTEIN:DUPLICATE",))
            iris_connection.commit()
        except:
            pass

        query = """
            mutation CreateProtein($input: CreateProteinInput!) {
                createProtein(input: $input) {
                    id
                }
            }
        """

        result = await schema.execute(
            query,
            variable_values={
                "input": {
                    "id": "PROTEIN:DUPLICATE",
                    "name": "Duplicate Protein"
                }
            },
            context_value={
                "protein_loader": ProteinLoader(iris_connection),
                "gene_loader": GeneLoader(iris_connection),
                "pathway_loader": PathwayLoader(iris_connection),
                "edge_loader": EdgeLoader(iris_connection),
                "db_connection": iris_connection,
            }
        )

        # Should return GraphQL error
        assert result.errors is not None
        assert "already exists" in str(result.errors[0]).lower() or "duplicate" in str(result.errors[0]).lower()

    async def test_create_protein_minimal_required_fields(self, iris_connection):
        """Test createProtein with only required fields (id, name)"""
        query = """
            mutation CreateProtein($input: CreateProteinInput!) {
                createProtein(input: $input) {
                    id
                    name
                    function
                    organism
                }
            }
        """

        result = await schema.execute(
            query,
            variable_values={
                "input": {
                    "id": "PROTEIN:MINIMAL",
                    "name": "Minimal Protein"
                }
            },
            context_value={
                "protein_loader": ProteinLoader(iris_connection),
                "gene_loader": GeneLoader(iris_connection),
                "pathway_loader": PathwayLoader(iris_connection),
                "edge_loader": EdgeLoader(iris_connection),
                "db_connection": iris_connection,
            }
        )

        assert result.errors is None
        protein = result.data["createProtein"]
        assert protein["id"] == "PROTEIN:MINIMAL"
        assert protein["name"] == "Minimal Protein"
        assert protein["function"] is None
        assert protein["organism"] is None


@pytest.mark.requires_database
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not SCHEMA_EXISTS, reason="Schema not implemented yet - TDD gate")
class TestUpdateProteinMutation:
    """Integration tests for updateProtein mutation"""

    async def test_update_protein_fields(self, iris_connection):
        """Test updateProtein modifies existing protein fields"""
        # Setup: Create protein
        cursor = iris_connection.cursor()
        try:
            cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ("PROTEIN:UPDATE_TEST",))
            cursor.execute("INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
                          ("PROTEIN:UPDATE_TEST", "Protein"))
            cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                          ("PROTEIN:UPDATE_TEST", "name", "Original Name"))
            cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                          ("PROTEIN:UPDATE_TEST", "function", "Original Function"))
            iris_connection.commit()
        except:
            pass

        query = """
            mutation UpdateProtein($id: ID!, $input: UpdateProteinInput!) {
                updateProtein(id: $id, input: $input) {
                    id
                    name
                    function
                    confidence
                }
            }
        """

        result = await schema.execute(
            query,
            variable_values={
                "id": "PROTEIN:UPDATE_TEST",
                "input": {
                    "name": "Updated Name",
                    "function": "Updated Function",
                    "confidence": 0.95
                }
            },
            context_value={
                "protein_loader": ProteinLoader(iris_connection),
                "gene_loader": GeneLoader(iris_connection),
                "pathway_loader": PathwayLoader(iris_connection),
                "edge_loader": EdgeLoader(iris_connection),
                "db_connection": iris_connection,
            }
        )

        assert result.errors is None, f"GraphQL errors: {result.errors}"
        protein = result.data["updateProtein"]
        assert protein["name"] == "Updated Name"
        assert protein["function"] == "Updated Function"
        assert protein["confidence"] == 0.95

        # Verify database changes
        cursor.execute("SELECT val FROM rdf_props WHERE s = ? AND key = ?",
                      ("PROTEIN:UPDATE_TEST", "name"))
        assert cursor.fetchone()[0] == "Updated Name"

    async def test_update_protein_partial_fields(self, iris_connection):
        """Test updateProtein with partial field update"""
        # Setup
        cursor = iris_connection.cursor()
        try:
            cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ("PROTEIN:PARTIAL_UPDATE",))
            cursor.execute("INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
                          ("PROTEIN:PARTIAL_UPDATE", "Protein"))
            cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                          ("PROTEIN:PARTIAL_UPDATE", "name", "Original Name"))
            cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                          ("PROTEIN:PARTIAL_UPDATE", "function", "Original Function"))
            iris_connection.commit()
        except:
            pass

        query = """
            mutation UpdateProtein($id: ID!, $input: UpdateProteinInput!) {
                updateProtein(id: $id, input: $input) {
                    id
                    name
                    function
                }
            }
        """

        # Only update function field
        result = await schema.execute(
            query,
            variable_values={
                "id": "PROTEIN:PARTIAL_UPDATE",
                "input": {
                    "function": "New Function Only"
                }
            },
            context_value={
                "protein_loader": ProteinLoader(iris_connection),
                "gene_loader": GeneLoader(iris_connection),
                "pathway_loader": PathwayLoader(iris_connection),
                "edge_loader": EdgeLoader(iris_connection),
                "db_connection": iris_connection,
            }
        )

        assert result.errors is None
        protein = result.data["updateProtein"]
        assert protein["name"] == "Original Name"  # Unchanged
        assert protein["function"] == "New Function Only"  # Changed

    async def test_update_protein_not_found(self, iris_connection):
        """Test updateProtein returns error for non-existent protein"""
        query = """
            mutation UpdateProtein($id: ID!, $input: UpdateProteinInput!) {
                updateProtein(id: $id, input: $input) {
                    id
                }
            }
        """

        result = await schema.execute(
            query,
            variable_values={
                "id": "PROTEIN:NONEXISTENT",
                "input": {
                    "name": "Updated Name"
                }
            },
            context_value={
                "protein_loader": ProteinLoader(iris_connection),
                "gene_loader": GeneLoader(iris_connection),
                "pathway_loader": PathwayLoader(iris_connection),
                "edge_loader": EdgeLoader(iris_connection),
                "db_connection": iris_connection,
            }
        )

        # Should return GraphQL error
        assert result.errors is not None
        assert "not found" in str(result.errors[0]).lower()


@pytest.mark.requires_database
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not SCHEMA_EXISTS, reason="Schema not implemented yet - TDD gate")
class TestDeleteProteinMutation:
    """Integration tests for deleteProtein mutation with FK cascade"""

    async def test_delete_protein_basic(self, iris_connection):
        """Test deleteProtein removes node and properties"""
        # Setup
        cursor = iris_connection.cursor()
        try:
            cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ("PROTEIN:DELETE_TEST",))
            cursor.execute("INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
                          ("PROTEIN:DELETE_TEST", "Protein"))
            cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                          ("PROTEIN:DELETE_TEST", "name", "To Be Deleted"))
            iris_connection.commit()
        except:
            pass

        query = """
            mutation DeleteProtein($id: ID!) {
                deleteProtein(id: $id)
            }
        """

        result = await schema.execute(
            query,
            variable_values={"id": "PROTEIN:DELETE_TEST"},
            context_value={
                "protein_loader": ProteinLoader(iris_connection),
                "gene_loader": GeneLoader(iris_connection),
                "pathway_loader": PathwayLoader(iris_connection),
                "edge_loader": EdgeLoader(iris_connection),
                "db_connection": iris_connection,
            }
        )

        assert result.errors is None, f"GraphQL errors: {result.errors}"
        assert result.data["deleteProtein"] is True

        # Verify deletion
        cursor.execute("SELECT COUNT(*) FROM nodes WHERE node_id = ?", ("PROTEIN:DELETE_TEST",))
        assert cursor.fetchone()[0] == 0

        cursor.execute("SELECT COUNT(*) FROM rdf_props WHERE s = ?", ("PROTEIN:DELETE_TEST",))
        assert cursor.fetchone()[0] == 0

    async def test_delete_protein_with_embedding(self, iris_connection):
        """Test deleteProtein removes embedding (FK cascade)"""
        # Setup with embedding
        cursor = iris_connection.cursor()
        try:
            cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ("PROTEIN:DELETE_WITH_EMB",))
            cursor.execute("INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
                          ("PROTEIN:DELETE_WITH_EMB", "Protein"))
            cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                          ("PROTEIN:DELETE_WITH_EMB", "name", "Delete Me"))
            iris_connection.commit()

            # Add embedding
            emb = np.random.randn(768)
            emb = emb / np.linalg.norm(emb)
            emb_str = "[" + ",".join([str(x) for x in emb.tolist()]) + "]"
            cursor.execute("INSERT INTO kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?))",
                          ("PROTEIN:DELETE_WITH_EMB", emb_str))
            iris_connection.commit()
        except Exception as e:
            print(f"Setup error: {e}")
            pass

        query = """
            mutation DeleteProtein($id: ID!) {
                deleteProtein(id: $id)
            }
        """

        result = await schema.execute(
            query,
            variable_values={"id": "PROTEIN:DELETE_WITH_EMB"},
            context_value={
                "protein_loader": ProteinLoader(iris_connection),
                "gene_loader": GeneLoader(iris_connection),
                "pathway_loader": PathwayLoader(iris_connection),
                "edge_loader": EdgeLoader(iris_connection),
                "db_connection": iris_connection,
            }
        )

        assert result.errors is None
        assert result.data["deleteProtein"] is True

        # Verify embedding deleted (FK cascade)
        cursor.execute("SELECT COUNT(*) FROM kg_NodeEmbeddings WHERE id = ?",
                      ("PROTEIN:DELETE_WITH_EMB",))
        assert cursor.fetchone()[0] == 0

    async def test_delete_protein_not_found(self, iris_connection):
        """Test deleteProtein returns error for non-existent protein"""
        query = """
            mutation DeleteProtein($id: ID!) {
                deleteProtein(id: $id)
            }
        """

        result = await schema.execute(
            query,
            variable_values={"id": "PROTEIN:NONEXISTENT"},
            context_value={
                "protein_loader": ProteinLoader(iris_connection),
                "gene_loader": GeneLoader(iris_connection),
                "pathway_loader": PathwayLoader(iris_connection),
                "edge_loader": EdgeLoader(iris_connection),
                "db_connection": iris_connection,
            }
        )

        # Should return GraphQL error
        assert result.errors is not None
        assert "not found" in str(result.errors[0]).lower()


# NOTE: iris_connection fixture is provided by tests/conftest.py
# Do not define a local fixture here to avoid shadowing


@pytest.fixture
def mutation_test_cleanup(iris_connection):
    """Fixture to cleanup mutation test data"""
    yield

    # Cleanup: Remove test data
    cursor = iris_connection.cursor()
    test_nodes = [
        "PROTEIN:TEST_CREATE",
        "PROTEIN:TEST_WITH_EMB",
        "PROTEIN:DUPLICATE",
        "PROTEIN:MINIMAL",
        "PROTEIN:UPDATE_TEST",
        "PROTEIN:PARTIAL_UPDATE",
        "PROTEIN:DELETE_TEST",
        "PROTEIN:DELETE_WITH_EMB",
    ]

    for node_id in test_nodes:
        try:
            cursor.execute("DELETE FROM kg_NodeEmbeddings WHERE id = ?", (node_id,))
            cursor.execute("DELETE FROM rdf_props WHERE s = ?", (node_id,))
            cursor.execute("DELETE FROM rdf_labels WHERE s = ?", (node_id,))
            cursor.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
        except:
            pass

    iris_connection.commit()
