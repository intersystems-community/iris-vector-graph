#!/usr/bin/env python3
"""
Vector Functions Validation Test
Tests that native IRIS vector functions and custom procedures work correctly
"""

import pytest
import json
import importlib
import numpy as np

# NOTE: Use importlib to avoid conflict with iris/ directory in project
try:
    iris_module = importlib.import_module('intersystems_irispython.iris')
    IRIS_AVAILABLE = True
except ImportError:
    IRIS_AVAILABLE = False
    pytest.skip("IRIS Python driver not available", allow_module_level=True)


class TestVectorFunctions:
    """Test suite for IRIS vector functions and procedures"""

    @classmethod
    def setup_class(cls):
        """Setup vector function tests"""
        if not IRIS_AVAILABLE:
            pytest.skip("IRIS Python driver not available")

        try:
            cls.conn = iris_module.connect(
                hostname='localhost',
                port=1973,
                namespace='USER',
                username='_SYSTEM',
                password='SYS'
            )

            # Test connection
            cursor = cls.conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()

            print("✓ IRIS connection for vector function testing established")

        except Exception as e:
            pytest.skip(f"IRIS database not accessible: {e}")

    @classmethod
    def teardown_class(cls):
        """Clean up vector function tests"""
        if hasattr(cls, 'conn'):
            cls.conn.close()

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

    def test_kg_node_embeddings_table_exists(self):
        """Test that kg_NodeEmbeddings table exists and has data"""
        cursor = self.conn.cursor()

        # Check table exists and has sample data
        cursor.execute("SELECT COUNT(*) FROM kg_NodeEmbeddings")
        count = cursor.fetchone()[0]

        assert count > 0, "kg_NodeEmbeddings table should have sample data"
        print(f"✓ kg_NodeEmbeddings table has {count} embeddings")

        cursor.close()

    def test_kg_knn_vec_procedure_exists(self):
        """Test that kg_KNN_VEC procedure exists and works"""
        cursor = self.conn.cursor()

        try:
            # Test with a simple vector search
            test_vector = [0.1] * 768  # 768-dimensional vector
            cursor.execute("CALL kg_KNN_VEC(?, ?, ?)", [
                json.dumps(test_vector),
                3,  # top 3 results
                None  # no label filter
            ])

            results = cursor.fetchall()
            assert len(results) >= 0  # Should not error, may have 0 results if no data

            print(f"✓ kg_KNN_VEC procedure works: returned {len(results)} results")

            # Print sample results
            for i, (entity_id, score) in enumerate(results[:3]):
                print(f"  {i+1}. {entity_id}: similarity = {score:.3f}")

        except Exception as e:
            pytest.fail(f"kg_KNN_VEC procedure failed: {e}")

        cursor.close()

    def test_kg_rrf_fuse_procedure_exists(self):
        """Test that kg_RRF_FUSE procedure exists and works"""
        cursor = self.conn.cursor()

        try:
            # Test with hybrid search
            test_vector = [0.1] * 768
            cursor.execute("CALL kg_RRF_FUSE(?, ?, ?, ?, ?, ?)", [
                5,  # k results
                10,  # k1 vector results
                10,  # k2 text results
                60,  # c parameter
                json.dumps(test_vector),
                'gene'  # text query
            ])

            results = cursor.fetchall()
            assert len(results) >= 0  # Should not error

            print(f"✓ kg_RRF_FUSE procedure works: returned {len(results)} results")

            # Print sample results
            for i, (entity_id, rrf_score, vs_score, bm25_score) in enumerate(results[:3]):
                print(f"  {i+1}. {entity_id}: RRF={rrf_score:.3f}, Vector={vs_score:.3f}, Text={bm25_score:.3f}")

        except Exception as e:
            pytest.fail(f"kg_RRF_FUSE procedure failed: {e}")

        cursor.close()

    def test_vector_search_with_sample_data(self):
        """Test vector search against sample embeddings"""
        cursor = self.conn.cursor()

        # First check if we have sample data
        cursor.execute("SELECT id, emb FROM kg_NodeEmbeddings LIMIT 1")
        sample = cursor.fetchone()

        if sample is None:
            pytest.skip("No sample data in kg_NodeEmbeddings table")

        sample_id, sample_embedding = sample
        print(f"✓ Found sample embedding for: {sample_id}")

        # Test similarity search using the sample embedding
        cursor.execute("CALL kg_KNN_VEC(?, ?, ?)", [
            json.dumps(sample_embedding),  # Use actual sample embedding
            5,  # top 5 results
            None
        ])

        results = cursor.fetchall()
        assert len(results) > 0, "Should find at least the sample itself"

        # First result should be the sample itself with perfect similarity
        top_result = results[0]
        assert top_result[0] == sample_id, "Top result should be the sample itself"
        assert abs(top_result[1] - 1.0) < 0.001, "Self-similarity should be ~1.0"

        print(f"✓ Vector search with sample data works: {len(results)} results")
        print(f"  Top result: {top_result[0]} (similarity: {top_result[1]:.6f})")

        cursor.close()

    def test_performance_vector_search(self):
        """Test vector search performance"""
        import time
        cursor = self.conn.cursor()

        # Test performance with multiple searches
        test_vector = [0.1] * 768

        start_time = time.time()
        for _ in range(10):  # 10 searches
            cursor.execute("CALL kg_KNN_VEC(?, ?, ?)", [
                json.dumps(test_vector),
                10,
                None
            ])
            cursor.fetchall()
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