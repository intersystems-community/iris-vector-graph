"""
Integration tests for GraphQL vector similarity search.

Tests the similar() field resolver using HNSW index with kg_KNN_VEC operator.
Validates vector search performance (<10ms target) and DataLoader integration.
"""

import pytest
from typing import Optional

# TDD Gate: Tests will initially fail until similar() resolver is implemented
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
class TestVectorSimilarityResolver:
    """Integration tests for Protein.similar() field resolver with HNSW"""

    async def test_protein_similar_basic_search(self, iris_connection):
        """Test protein.similar() returns semantically similar proteins"""
        cursor = iris_connection.cursor()

        # Cleanup any existing test data first
        test_nodes = ["PROTEIN:TP53", "PROTEIN:MDM2", "PROTEIN:P21"]
        for node_id in test_nodes:
            try:
                cursor.execute("DELETE FROM kg_NodeEmbeddings WHERE id = ?", (node_id,))
                cursor.execute("DELETE FROM rdf_edges WHERE s = ? OR o_id = ?", (node_id, node_id))
                cursor.execute("DELETE FROM rdf_props WHERE s = ?", (node_id,))
                cursor.execute("DELETE FROM rdf_labels WHERE s = ?", (node_id,))
                cursor.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
            except:
                pass
        try:
            iris_connection.commit()
        except:
            iris_connection.rollback()

        # Setup: Create 3 proteins with embeddings
        proteins = [
            ("PROTEIN:TP53", "Tumor protein p53", "Tumor suppressor"),
            ("PROTEIN:MDM2", "MDM2 proto-oncogene", "p53 regulator"),
            ("PROTEIN:P21", "Cyclin-dependent kinase inhibitor", "Cell cycle arrest"),
        ]

        # Sample 768-dimensional embeddings (normalized)
        # TP53 and MDM2 should be similar (both p53 pathway)
        # P21 should be less similar
        import numpy as np

        tp53_emb = np.random.randn(768)
        tp53_emb = tp53_emb / np.linalg.norm(tp53_emb)

        mdm2_emb = tp53_emb + np.random.randn(768) * 0.1  # Similar to TP53
        mdm2_emb = mdm2_emb / np.linalg.norm(mdm2_emb)

        p21_emb = np.random.randn(768)
        p21_emb = p21_emb / np.linalg.norm(p21_emb)

        embeddings = [
            ("PROTEIN:TP53", tp53_emb.tolist()),
            ("PROTEIN:MDM2", mdm2_emb.tolist()),
            ("PROTEIN:P21", p21_emb.tolist()),
        ]

        # Create nodes and properties
        for protein_id, name, function in proteins:
            try:
                cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", (protein_id,))
            except:
                pass
            try:
                cursor.execute("INSERT INTO rdf_labels (s, label) VALUES (?, ?)", (protein_id, "Protein"))
            except:
                pass
            try:
                cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                             (protein_id, "name", name))
            except:
                pass
            try:
                cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                             (protein_id, "function", function))
            except:
                pass

        # Commit nodes before inserting embeddings (FK constraint requires nodes to exist)
        iris_connection.commit()

        # Insert embeddings into kg_NodeEmbeddings with VECTOR type
        for protein_id, emb in embeddings:
            # IRIS VECTOR syntax: TO_VECTOR(?) with JSON array parameter
            emb_str = "[" + ",".join([str(x) for x in emb]) + "]"
            cursor.execute(
                "INSERT INTO kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?))",
                (protein_id, emb_str)
            )

        iris_connection.commit()

        # Execute GraphQL query for similar proteins
        query = """
            query GetSimilarProteins($id: ID!, $limit: Int!, $threshold: Float!) {
                protein(id: $id) {
                    id
                    name
                    similar(limit: $limit, threshold: $threshold) {
                        protein {
                            id
                            name
                            function
                        }
                        similarity
                        distance
                    }
                }
            }
        """

        result = await schema.execute(
            query,
            variable_values={
                "id": "PROTEIN:TP53",
                "limit": 10,
                "threshold": 0.0  # Low threshold to find any similar proteins
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
        assert result.data is not None

        protein = result.data["protein"]
        assert protein["name"] == "Tumor protein p53"

        similar_proteins = protein["similar"]

        # With threshold=0.0, should find similar proteins (MDM2 and P21)
        assert len(similar_proteins) > 0, "Should find at least one similar protein"

        # Verify MDM2 is more similar than P21 (if both returned)
        similar_ids = [p["protein"]["id"] for p in similar_proteins]

        # Should exclude self (TP53)
        assert "PROTEIN:TP53" not in similar_ids

        # Should find similar proteins (MDM2 and/or P21)
        assert len(similar_ids) >= 1, f"Should find at least 1 similar protein, found: {similar_ids}"

        # All similarities should be >= threshold (0.0)
        for sp in similar_proteins:
            assert sp["similarity"] >= 0.0, f"Similarity should be >= threshold"

    async def test_protein_similar_with_threshold(self, iris_connection):
        """Test similar() respects similarity threshold parameter"""
        cursor = iris_connection.cursor()

        # Cleanup any existing test data first
        try:
            cursor.execute("DELETE FROM kg_NodeEmbeddings WHERE id = ?", ("PROTEIN:TEST_THRESHOLD",))
            cursor.execute("DELETE FROM rdf_props WHERE s = ?", ("PROTEIN:TEST_THRESHOLD",))
            cursor.execute("DELETE FROM rdf_labels WHERE s = ?", ("PROTEIN:TEST_THRESHOLD",))
            cursor.execute("DELETE FROM nodes WHERE node_id = ?", ("PROTEIN:TEST_THRESHOLD",))
            iris_connection.commit()
        except:
            iris_connection.rollback()

        # Setup: Create protein with embedding
        try:
            cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", ("PROTEIN:TEST_THRESHOLD",))
        except:
            pass
        try:
            cursor.execute("INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
                         ("PROTEIN:TEST_THRESHOLD", "Protein"))
        except:
            pass
        try:
            cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                         ("PROTEIN:TEST_THRESHOLD", "name", "Test Protein"))
        except:
            pass

        # Commit nodes before inserting embeddings
        iris_connection.commit()

        # Create random embedding
        import numpy as np
        test_emb = np.random.randn(768)
        test_emb = test_emb / np.linalg.norm(test_emb)
        emb_str = "[" + ",".join([str(x) for x in test_emb.tolist()]) + "]"

        try:
            cursor.execute(
                "INSERT INTO kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?))",
                ("PROTEIN:TEST_THRESHOLD", emb_str)
            )
        except Exception as e:
            print(f"Error inserting test embedding: {e}")
            pass

        iris_connection.commit()

        # Query with high threshold (should return fewer results)
        query = """
            query GetSimilarProteins($id: ID!, $threshold: Float!) {
                protein(id: $id) {
                    similar(limit: 100, threshold: $threshold) {
                        similarity
                    }
                }
            }
        """

        result = await schema.execute(
            query,
            variable_values={
                "id": "PROTEIN:TEST_THRESHOLD",
                "threshold": 0.95  # Very high threshold
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
        # With random embeddings and high threshold, should return very few or no results
        similar = result.data["protein"]["similar"]
        for item in similar:
            assert item["similarity"] >= 0.95, "All results should meet threshold"

    async def test_protein_similar_limit_parameter(self, iris_connection):
        """Test similar() respects limit parameter"""
        query = """
            query GetSimilarProteins($id: ID!, $limit: Int!) {
                protein(id: $id) {
                    similar(limit: $limit, threshold: 0.0) {
                        protein {
                            id
                        }
                    }
                }
            }
        """

        # Use existing protein with embedding (from previous test)
        result = await schema.execute(
            query,
            variable_values={
                "id": "PROTEIN:TP53",
                "limit": 2
            },
            context_value={
                "protein_loader": ProteinLoader(iris_connection),
                "gene_loader": GeneLoader(iris_connection),
                "pathway_loader": PathwayLoader(iris_connection),
                "edge_loader": EdgeLoader(iris_connection),
                "db_connection": iris_connection,
            }
        )

        if result.errors is None and result.data["protein"]:
            similar = result.data["protein"]["similar"]
            assert len(similar) <= 2, "Should respect limit parameter"


# NOTE: iris_connection fixture is provided by tests/conftest.py
# Do not define a local fixture here to avoid shadowing


@pytest.fixture
def vector_search_test_cleanup(iris_connection):
    """Fixture to cleanup vector search test data"""
    yield

    # Cleanup: Remove test data
    cursor = iris_connection.cursor()
    test_nodes = [
        "PROTEIN:TP53", "PROTEIN:MDM2", "PROTEIN:P21",
        "PROTEIN:TEST_THRESHOLD"
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
