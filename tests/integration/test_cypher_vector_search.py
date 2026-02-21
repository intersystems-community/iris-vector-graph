"""
Integration tests for CALL ivg.vector.search(...) YIELD node, score

Tests the SQL layer against a live IRIS instance. Uses the existing
iris_connection fixture from tests/integration/conftest.py.

Note: The conftest uses IRIS_PORT env var and hardcoded port fallback
(pre-existing Principle IV violations — tracked separately). These tests
do not introduce new violations; they use the existing fixture.

Set SKIP_IRIS_TESTS=true to skip (default: false — tests run against live IRIS).
"""

import os
import json
import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def vec_search_data(iris_connection):
    """Seed minimal test data for vector search integration tests.

    Creates 3 Gene nodes and 2 Drug nodes with 3-d embeddings in
    Graph_KG.kg_NodeEmbeddings. Nodes closest to [1, 0, 0] are
    gene-a and gene-b (by cosine similarity).

    Cleans up after the module.
    """
    cursor = iris_connection.cursor()
    prefix = "ivg_vs_test:"

    nodes = [
        (f"{prefix}gene-a", "Gene", [1.0, 0.0, 0.0]),
        (f"{prefix}gene-b", "Gene", [0.9, 0.1, 0.0]),
        (f"{prefix}gene-c", "Gene", [0.0, 1.0, 0.0]),
        (f"{prefix}drug-a", "Drug", [0.8, 0.2, 0.0]),
        (f"{prefix}drug-b", "Drug", [0.0, 0.0, 1.0]),
    ]

    try:
        for node_id, label, vec in nodes:
            # Insert node
            cursor.execute(
                "INSERT OR IGNORE INTO Graph_KG.nodes (node_id) VALUES (?)", [node_id]
            )
            cursor.execute(
                "INSERT OR IGNORE INTO Graph_KG.rdf_labels (s, label) VALUES (?, ?)",
                [node_id, label],
            )
            # Insert embedding (3-d)
            vec_json = json.dumps(vec)
            cursor.execute(
                "INSERT OR IGNORE INTO Graph_KG.kg_NodeEmbeddings (node_id, emb) "
                "VALUES (?, TO_VECTOR(?))",
                [node_id, vec_json],
            )
        iris_connection.commit()
    except Exception:
        iris_connection.rollback()
        raise

    # Ensure HNSW index exists (idempotent)
    try:
        cursor.execute(
            "CREATE INDEX ivg_vs_test_hnsw ON Graph_KG.kg_NodeEmbeddings (emb) "
            "USING HNSW"
        )
        iris_connection.commit()
    except Exception:
        # Index may already exist — not fatal
        try:
            iris_connection.rollback()
        except Exception:
            pass

    yield iris_connection

    # Cleanup
    node_ids = [n[0] for n in nodes]
    try:
        for node_id in node_ids:
            cursor.execute(
                "DELETE FROM Graph_KG.kg_NodeEmbeddings WHERE node_id = ?", [node_id]
            )
            cursor.execute(
                "DELETE FROM Graph_KG.rdf_labels WHERE s = ?", [node_id]
            )
            cursor.execute(
                "DELETE FROM Graph_KG.nodes WHERE node_id = ?", [node_id]
            )
        iris_connection.commit()
    except Exception:
        try:
            iris_connection.rollback()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _run_cypher(conn, cypher: str, params: dict = None) -> dict:
    """Parse, translate, and execute a Cypher query. Returns {columns, rows}."""
    from iris_vector_graph.cypher.parser import parse_query
    from iris_vector_graph.cypher.translator import translate_to_sql, set_schema_prefix

    set_schema_prefix("Graph_KG")
    ast = parse_query(cypher)
    sql_q = translate_to_sql(ast, params)

    cursor = conn.cursor()
    sql_str = sql_q.sql if isinstance(sql_q.sql, str) else "\n".join(sql_q.sql)
    p = sql_q.parameters[0] if sql_q.parameters else []
    cursor.execute(sql_str, p)
    columns = [desc[0] for desc in cursor.description] if cursor.description else []
    rows = cursor.fetchall()
    return {"columns": columns, "rows": rows, "sql": sql_str}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestVectorSearchSQL:
    def test_returns_rows(self, vec_search_data):
        """Vector search returns at least one row when embeddings exist."""
        conn = vec_search_data
        result = _run_cypher(
            conn,
            "CALL ivg.vector.search('Gene', 'embedding', [1.0, 0.0, 0.0], 3) "
            "YIELD node, score RETURN node, score",
        )
        assert len(result["rows"]) > 0

    def test_results_ordered_by_score_descending(self, vec_search_data):
        """Results must be ordered by similarity score descending."""
        conn = vec_search_data
        result = _run_cypher(
            conn,
            "CALL ivg.vector.search('Gene', 'embedding', [1.0, 0.0, 0.0], 3) "
            "YIELD node, score RETURN node, score",
        )
        scores = [row[result["columns"].index("score")] for row in result["rows"]]
        assert scores == sorted(scores, reverse=True), f"Scores not descending: {scores}"

    def test_label_filter_restricts_results(self, vec_search_data):
        """Only nodes with the specified label are returned."""
        conn = vec_search_data
        # Search Drug label — should not return Gene nodes
        result = _run_cypher(
            conn,
            "CALL ivg.vector.search('Drug', 'embedding', [1.0, 0.0, 0.0], 5) "
            "YIELD node, score RETURN node, score",
        )
        prefix = "ivg_vs_test:"
        node_ids = [row[0] for row in result["rows"]]
        # All returned nodes should be drug nodes (have 'drug' in their id for our test data)
        for nid in node_ids:
            assert "drug" in nid.lower() or not nid.startswith(prefix), (
                f"Unexpected gene node in Drug search: {nid}"
            )

    def test_limit_respected(self, vec_search_data):
        """Result count must not exceed the specified limit."""
        conn = vec_search_data
        result = _run_cypher(
            conn,
            "CALL ivg.vector.search('Gene', 'embedding', [1.0, 0.0, 0.0], 2) "
            "YIELD node, score RETURN node, score",
        )
        assert len(result["rows"]) <= 2

    def test_dot_product_similarity_executes(self, vec_search_data):
        """dot_product similarity option executes without error."""
        conn = vec_search_data
        result = _run_cypher(
            conn,
            "CALL ivg.vector.search('Gene', 'embedding', [1.0, 0.0, 0.0], 3, "
            "{similarity: 'dot_product'}) YIELD node, score RETURN node, score",
        )
        assert "columns" in result
        # dot_product can return any ordering but must not error

    def test_vecsearch_cte_composable_with_match(self, vec_search_data):
        """CALL followed by MATCH compiles and executes without error."""
        conn = vec_search_data
        # Even if no edges exist between test nodes, the query must not raise
        try:
            result = _run_cypher(
                conn,
                "CALL ivg.vector.search('Gene', 'embedding', [1.0, 0.0, 0.0], 3) "
                "YIELD node, score "
                "MATCH (node)-[:RELATED]->(m:Drug) "
                "RETURN node, score",
            )
            # May return 0 rows but must not crash
            assert "rows" in result
        except Exception as e:
            # If no edges, the MATCH join returns 0 rows — some IRIS versions may raise
            # on empty result sets; accept that gracefully
            if "no rows" in str(e).lower():
                pass
            else:
                raise
