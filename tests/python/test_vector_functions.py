#!/usr/bin/env python3
"""
Vector Functions Validation Test
Tests that native IRIS vector functions and custom procedures work correctly
"""

import pytest
import json
import importlib
import numpy as np
from typing import Any

# The driver's distribution is `intersystems-irispython`; the module it installs is
# `iris`. Probing for `intersystems_irispython.iris` — a module that has never
# existed — skipped this whole file on every machine, so the vector-function
# assertions below have not run since the guard was written. importlib is kept
# because the probe must not bind the name at import time.
try:
    importlib.import_module('iris')
    IRIS_AVAILABLE = True
except ImportError:
    IRIS_AVAILABLE = False
    pytest.skip("IRIS Python driver not available", allow_module_level=True)


_GRAPH = "vecfn-probe"
_NODES = ["VECFN_0", "VECFN_1", "VECFN_2"]

# A result-set procedure is read as a table here, not invoked with `CALL`: the DBAPI
# driver rejects `CALL <proc>(...)` at Prepare with SQLCODE -51 ("An SQL statement
# expected, IDENTIFIER found"), which is what the old `CALL kg_KNN_VEC(...)` tests
# were reporting as "the procedure failed". `kg_RRF_FUSE`'s own body reads
# `kg_KNN_VEC` the same way (schema.py).
_KNN_SQL = "SELECT * FROM Graph_KG.kg_KNN_VEC(?, ?, ?, ?)"
_RRF_SQL = "SELECT * FROM Graph_KG.kg_RRF_FUSE(?, ?, ?, ?, ?, ?, ?)"


def _declared_dimension(conn) -> int:
    """The width `Graph_KG.kg_NodeEmbeddings.emb` is declared at, in this namespace.

    The old tests hardcoded 768 and the container declares whatever the engine that
    built it was configured for, so every insert and every query vector was the wrong
    width (SQLCODE -104 on write, a reshaped score or -257 on read).
    """
    from iris_vector_graph.schema import GraphSchema

    cursor = conn.cursor()
    try:
        dim = GraphSchema.get_embedding_dimension(cursor, "Graph_KG.kg_NodeEmbeddings")
    finally:
        cursor.close()
    if not dim:
        pytest.skip("Graph_KG.kg_NodeEmbeddings.emb has no declared width in this namespace")
    return int(dim)


def _basis(index: int, dim: int) -> list:
    """A unit vector along one axis — so cosine of a vector with itself is exactly 1.0."""
    vec = [0.0] * dim
    vec[index % dim] = 1.0
    return vec


@pytest.fixture(scope="module", autouse=True)
def inject_iris_connection(iris_connection):
    """Inject the connection and seed the rows these tests read.

    The embedding tests used to read whatever rows happened to be in the namespace
    and asserted `count > 0`, so they passed on a machine someone had loaded by hand
    and failed on a clean container. A test owns its premise: three nodes in one probe
    graph, each with a unit vector at the column's declared width.
    """
    TestVectorFunctions.conn = iris_connection
    dim = _declared_dimension(iris_connection)
    TestVectorFunctions.dim = dim
    TestVectorFunctions.graph = _GRAPH

    cursor = iris_connection.cursor()
    for i, node in enumerate(_NODES):
        cursor.execute(
            "INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, ?)", [node, _GRAPH]
        )
        cursor.execute(
            "INSERT INTO Graph_KG.kg_NodeEmbeddings (node_id, graph_id, emb) "
            "VALUES (?, ?, TO_VECTOR(?, DOUBLE, ?))",
            [node, _GRAPH, json.dumps(_basis(i, dim)), dim],
        )
    iris_connection.commit()
    try:
        yield
    finally:
        for node in _NODES:
            cursor.execute(
                "DELETE FROM Graph_KG.kg_NodeEmbeddings WHERE node_id = ? AND graph_id = ?",
                [node, _GRAPH],
            )
            cursor.execute(
                "DELETE FROM Graph_KG.nodes WHERE node_id = ? AND graph_id = ?",
                [node, _GRAPH],
            )
        iris_connection.commit()
        cursor.close()


class TestVectorFunctions:
    """Test suite for IRIS vector functions and procedures"""

    conn: Any = None

    @classmethod
    def setup_class(cls):
        """Setup vector function tests"""
        if cls.conn is None:
            pytest.skip("iris_connection fixture did not provide a connection")

        cursor = None
        try:
            cursor = cls.conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            print("✓ IRIS connection for vector function testing established")
        except Exception as e:
            pytest.skip(f"IRIS database not accessible: {e}")
        finally:
            if cursor:
                cursor.close()

    @classmethod
    def teardown_class(cls):
        """Clean up vector function tests"""
        # Connection is managed by shared fixture; nothing to close
        pass

    def test_native_to_vector_function(self):
        """Test native IRIS TO_VECTOR function"""
        cursor = self.conn.cursor()

        # Test TO_VECTOR with simple array
        cursor.execute("SELECT TO_VECTOR('[1, 2, 3]') as vec")
        result = cursor.fetchone()

        assert result is not None
        print("✓ TO_VECTOR function works")

        cursor.close()

    def test_native_vector_cosine_function(self):
        """Test native IRIS VECTOR_COSINE function"""
        cursor = self.conn.cursor()

        # Test VECTOR_COSINE with identical vectors (should return 1.0)
        cursor.execute("""
            SELECT VECTOR_COSINE(TO_VECTOR('[1, 0, 0]'), TO_VECTOR('[1, 0, 0]')) as similarity
        """)
        result = cursor.fetchone()

        assert result is not None
        similarity = result[0]
        assert abs(similarity - 1.0) < 0.001  # Should be very close to 1.0

        print(f"✓ VECTOR_COSINE function works: identical vectors = {similarity:.3f}")

        cursor.close()

    def test_vector_cosine_with_different_vectors(self):
        """Test VECTOR_COSINE with orthogonal vectors"""
        cursor = self.conn.cursor()

        # Test with orthogonal vectors (should return 0.0)
        cursor.execute("""
            SELECT VECTOR_COSINE(TO_VECTOR('[1, 0, 0]'), TO_VECTOR('[0, 1, 0]')) as similarity
        """)
        result = cursor.fetchone()

        assert result is not None
        similarity = result[0]
        assert abs(similarity - 0.0) < 0.001  # Should be very close to 0.0

        print(f"✓ VECTOR_COSINE function works: orthogonal vectors = {similarity:.3f}")

        cursor.close()

    def test_kg_node_embeddings_holds_the_seeded_rows(self):
        """The three seeded embeddings are in the probe graph, and only there."""
        cursor = self.conn.cursor()

        cursor.execute(
            "SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings WHERE graph_id = ?", [self.graph]
        )
        assert int(cursor.fetchone()[0]) == len(_NODES)

        cursor.close()

    def test_kg_knn_vec_procedure_exists(self):
        """`Graph_KG.kg_KNN_VEC` answers a scoped search.

        The call used to be `CALL kg_KNN_VEC(?, ?, ?)`: unqualified (the procedure is
        owned by the `Graph_KG` schema, so the bare name does not resolve) and three
        arguments (227 added the graph as a fourth). Both failed at Prepare with
        SQLCODE -51, which the test then reported as "the procedure failed".
        """
        cursor = self.conn.cursor()

        cursor.execute(
            _KNN_SQL,
            [json.dumps(_basis(0, self.dim)), 3, None, self.graph],
        )
        results = cursor.fetchall()

        assert len(results) == len(_NODES), "the seeded graph holds three embeddings"
        assert results[0][0] == _NODES[0], "the nearest neighbour of a vector is itself"
        assert abs(float(results[0][1]) - 1.0) < 0.001

        cursor.close()

    def test_kg_knn_vec_does_not_reach_another_graph(self):
        """A search in a graph with no embeddings answers nothing, not everything.

        This is the guarantee 227 exists for: the predicate is in the procedure body,
        so an empty graph cannot fall back to a namespace-wide scan.
        """
        cursor = self.conn.cursor()

        cursor.execute(
            _KNN_SQL,
            [json.dumps(_basis(0, self.dim)), 10, None, "vecfn-probe-empty"],
        )
        # `fetchall()` answers a tuple here, so `== []` was never true of any result —
        # the assertion would have failed on an empty answer as readily as a leaking one.
        assert list(cursor.fetchall()) == []

        cursor.close()

    def test_kg_rrf_fuse_procedure_exists(self):
        """`Graph_KG.kg_RRF_FUSE` takes the graph too, and it is not optional.

        Same two staleness bugs as the KNN call above, plus the seventh argument 227
        added: passing NULL there would have meant "the default graph" by accident.
        """
        cursor = self.conn.cursor()

        cursor.execute(
            _RRF_SQL,
            [5, 10, 10, 60, json.dumps(_basis(0, self.dim)), "gene", self.graph],
        )
        results = cursor.fetchall()

        # The text leg reads `docs`, which this test does not seed, so the fused answer
        # is the vector leg's. What matters here is that the procedure runs and stays
        # inside the graph.
        assert [row[0] for row in results] == _NODES[:1] or all(
            row[0] in _NODES for row in results
        ), f"fusion returned rows from outside {self.graph!r}: {results}"

        cursor.close()

    def test_vector_search_finds_the_seeded_row_itself(self):
        """Searching with a stored vector returns that row first, at similarity 1.0."""
        cursor = self.conn.cursor()

        target = _NODES[1]
        cursor.execute(
            _KNN_SQL,
            [json.dumps(_basis(1, self.dim)), 5, None, self.graph],
        )
        results = cursor.fetchall()

        assert results, "the seeded graph is not empty"
        assert results[0][0] == target
        assert abs(float(results[0][1]) - 1.0) < 0.001

        cursor.close()

    def test_performance_vector_search(self):
        """Ten scoped searches run, and the average is reported."""
        import time
        cursor = self.conn.cursor()

        query = json.dumps(_basis(0, self.dim))

        start_time = time.time()
        for _ in range(10):
            cursor.execute(
                _KNN_SQL, [query, 10, None, self.graph]
            )
            assert cursor.fetchall(), "a scoped search over seeded rows returned nothing"
        elapsed = time.time() - start_time

        avg_time = elapsed / 10 * 1000  # Convert to ms per query
        print(f"✓ Vector search performance: {avg_time:.2f}ms average per query")

        cursor.close()

        # Performance should be reasonable (less than 100ms per query)
        assert avg_time < 100, f"Vector search too slow: {avg_time:.2f}ms per query"


if __name__ == "__main__":
    # Run vector function tests
    print("Running IRIS Vector Functions Validation...")

    try:
        test_instance = TestVectorFunctions()
        test_instance.setup_class()

        print("\n=== Testing Native IRIS Vector Functions ===")
        test_instance.test_native_to_vector_function()
        test_instance.test_native_vector_cosine_function()
        test_instance.test_vector_cosine_with_different_vectors()

        print("\n=== Testing Schema and Data ===")
        test_instance.test_kg_node_embeddings_table_exists()

        print("\n=== Testing Custom Stored Procedures ===")
        test_instance.test_kg_knn_vec_procedure_exists()
        test_instance.test_kg_rrf_fuse_procedure_exists()

        print("\n=== Testing with Sample Data ===")
        test_instance.test_vector_search_with_sample_data()

        print("\n=== Performance Testing ===")
        test_instance.test_performance_vector_search()

        test_instance.teardown_class()

        print("\n✅ All vector function tests passed!")
        print("\nSummary of validated capabilities:")
        print("1. ✓ Native IRIS TO_VECTOR() function")
        print("2. ✓ Native IRIS VECTOR_COSINE() function")
        print("3. ✓ Custom kg_KNN_VEC() stored procedure")
        print("4. ✓ Custom kg_RRF_FUSE() stored procedure")
        print("5. ✓ Vector search with sample embeddings")
        print("6. ✓ Performance within acceptable limits")

    except Exception as e:
        print(f"\n❌ Vector function testing failed: {e}")
        import traceback
        traceback.print_exc()
