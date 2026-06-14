"""
Integration tests for GraphQL vector similarity search.

Tests the similar() field resolver using HNSW index.
kg_NodeEmbeddings.emb is defined as %Library.Vector(DATATYPE=DOUBLE, LEN=128),
so all test embeddings must be 128-dimensional.
"""

import pytest
import numpy as np
from typing import Optional

try:
    from api.gql.schema import schema
    from api.gql.loaders import ProteinLoader, GeneLoader, PathwayLoader, EdgeLoader
    SCHEMA_EXISTS = True
except ImportError:
    SCHEMA_EXISTS = False
    schema = None

# Dimensionality dictated by compiled Graph.KG.kgNodeEmbeddings class (LEN=128)
_EMB_DIM = 128


def _rand_emb():
    v = np.random.randn(_EMB_DIM)
    v /= np.linalg.norm(v)
    return v


def _insert_protein(cursor, conn, protein_id, name, function_val=None, emb=None):
    """Insert a protein node (and optional embedding) into the test DB."""
    try:
        cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", (protein_id,))
    except Exception:
        pass
    try:
        cursor.execute("INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, ?)", (protein_id, "Protein"))
    except Exception:
        pass
    try:
        cursor.execute("INSERT INTO Graph_KG.rdf_props (s, key, val) VALUES (?, ?, ?)", (protein_id, "name", name))
    except Exception:
        pass
    if function_val:
        try:
            cursor.execute("INSERT INTO Graph_KG.rdf_props (s, key, val) VALUES (?, ?, ?)", (protein_id, "function", function_val))
        except Exception:
            pass
    conn.commit()

    if emb is not None:
        emb_str = ",".join(str(x) for x in emb)
        try:
            cursor.execute(
                "INSERT INTO Graph_KG.kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?, DOUBLE))",
                (protein_id, emb_str),
            )
            conn.commit()
        except Exception as e:
            print(f"Embedding insert error for {protein_id}: {e}")


def _cleanup(cursor, conn, protein_ids):
    for pid in protein_ids:
        for sql in [
            "DELETE FROM Graph_KG.kg_NodeEmbeddings WHERE id = ?",
            "DELETE FROM Graph_KG.rdf_edges WHERE s = ? OR o_id = ?",
            "DELETE FROM Graph_KG.rdf_props WHERE s = ?",
            "DELETE FROM Graph_KG.rdf_labels WHERE s = ?",
            "DELETE FROM Graph_KG.nodes WHERE node_id = ?",
        ]:
            try:
                if "o_id" in sql:
                    cursor.execute(sql, (pid, pid))
                else:
                    cursor.execute(sql, (pid,))
            except Exception:
                pass
    try:
        conn.commit()
    except Exception:
        pass


@pytest.mark.requires_database
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not SCHEMA_EXISTS, reason="Schema not implemented yet - TDD gate")
class TestVectorSimilarityResolver:
    """Integration tests for Protein.similar() field resolver"""

    async def test_protein_similar_basic_search(self, iris_connection):
        """similar() returns semantically similar proteins"""
        test_nodes = ["PROTEIN:VSIM_TP53", "PROTEIN:VSIM_MDM2", "PROTEIN:VSIM_P21"]
        cursor = iris_connection.cursor()
        _cleanup(cursor, iris_connection, test_nodes)

        tp53_emb = _rand_emb()
        mdm2_emb = tp53_emb + np.random.randn(_EMB_DIM) * 0.05
        mdm2_emb /= np.linalg.norm(mdm2_emb)
        p21_emb = _rand_emb()

        _insert_protein(cursor, iris_connection, "PROTEIN:VSIM_TP53", "Tumor protein p53", "Tumor suppressor", tp53_emb)
        _insert_protein(cursor, iris_connection, "PROTEIN:VSIM_MDM2", "MDM2 proto-oncogene", "p53 regulator", mdm2_emb)
        _insert_protein(cursor, iris_connection, "PROTEIN:VSIM_P21", "Cyclin-dependent kinase inhibitor", "Cell cycle arrest", p21_emb)

        query = """
            query GetSimilarProteins($id: ID!, $limit: Int!, $threshold: Float!) {
                protein(id: $id) {
                    id
                    name
                    similar(limit: $limit, threshold: $threshold) {
                        protein { id name function }
                        similarity
                    }
                }
            }
        """

        result = await schema.execute(
            query,
            variable_values={"id": "PROTEIN:VSIM_TP53", "limit": 10, "threshold": 0.0},
            context_value={
                "protein_loader": ProteinLoader(iris_connection),
                "gene_loader": GeneLoader(iris_connection),
                "pathway_loader": PathwayLoader(iris_connection),
                "edge_loader": EdgeLoader(iris_connection),
                "db_connection": iris_connection,
            },
        )

        _cleanup(cursor, iris_connection, test_nodes)

        assert result.errors is None, f"GraphQL errors: {result.errors}"
        protein = result.data["protein"]
        assert protein["name"] == "Tumor protein p53"

        similar_proteins = protein["similar"]
        assert len(similar_proteins) > 0, "Should find at least one similar protein"

        similar_ids = [p["protein"]["id"] for p in similar_proteins]
        assert "PROTEIN:VSIM_TP53" not in similar_ids, "Should exclude self"

        for sp in similar_proteins:
            assert sp["similarity"] >= 0.0

    async def test_protein_similar_with_threshold(self, iris_connection):
        """similar() respects similarity threshold"""
        test_nodes = ["PROTEIN:VSIM_THRESH"]
        cursor = iris_connection.cursor()
        _cleanup(cursor, iris_connection, test_nodes)

        _insert_protein(cursor, iris_connection, "PROTEIN:VSIM_THRESH", "Test Protein", None, _rand_emb())

        query = """
            query GetSimilarProteins($id: ID!, $threshold: Float!) {
                protein(id: $id) {
                    similar(limit: 100, threshold: $threshold) { similarity }
                }
            }
        """

        result = await schema.execute(
            query,
            variable_values={"id": "PROTEIN:VSIM_THRESH", "threshold": 0.95},
            context_value={
                "protein_loader": ProteinLoader(iris_connection),
                "gene_loader": GeneLoader(iris_connection),
                "pathway_loader": PathwayLoader(iris_connection),
                "edge_loader": EdgeLoader(iris_connection),
                "db_connection": iris_connection,
            },
        )

        _cleanup(cursor, iris_connection, test_nodes)

        assert result.errors is None
        for item in result.data["protein"]["similar"]:
            assert item["similarity"] >= 0.95

    async def test_protein_similar_limit_parameter(self, iris_connection):
        """similar() respects limit parameter"""
        test_nodes = ["PROTEIN:VSIM_LIM1", "PROTEIN:VSIM_LIM2", "PROTEIN:VSIM_LIM3", "PROTEIN:VSIM_LIM4"]
        cursor = iris_connection.cursor()
        _cleanup(cursor, iris_connection, test_nodes)

        base = _rand_emb()
        for pid, name in [
            ("PROTEIN:VSIM_LIM1", "Limit Test 1"),
            ("PROTEIN:VSIM_LIM2", "Limit Test 2"),
            ("PROTEIN:VSIM_LIM3", "Limit Test 3"),
            ("PROTEIN:VSIM_LIM4", "Limit Test 4"),
        ]:
            emb = base + np.random.randn(_EMB_DIM) * 0.01
            emb /= np.linalg.norm(emb)
            _insert_protein(cursor, iris_connection, pid, name, None, emb)

        query = """
            query GetSimilarProteins($id: ID!, $limit: Int!) {
                protein(id: $id) {
                    similar(limit: $limit, threshold: 0.0) { protein { id } }
                }
            }
        """

        result = await schema.execute(
            query,
            variable_values={"id": "PROTEIN:VSIM_LIM1", "limit": 2},
            context_value={
                "protein_loader": ProteinLoader(iris_connection),
                "gene_loader": GeneLoader(iris_connection),
                "pathway_loader": PathwayLoader(iris_connection),
                "edge_loader": EdgeLoader(iris_connection),
                "db_connection": iris_connection,
            },
        )

        _cleanup(cursor, iris_connection, test_nodes)

        assert result.errors is None
        if result.data["protein"]:
            assert len(result.data["protein"]["similar"]) <= 2
