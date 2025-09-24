#!/usr/bin/env python3
"""
Python Graph Operators Validation Test
Tests that our Python-based graph operators work correctly
"""

import sys
import os
import pytest
import json
import iris
import numpy as np

# Add the python directory to path to import operators
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../python'))

try:
    import iris
    from iris_vector_graph_operators import IRISGraphOperators
    IRIS_AVAILABLE = True
except ImportError:
    IRIS_AVAILABLE = False
    pytest.skip("IRIS Python driver or operators not available", allow_module_level=True)


class TestPythonGraphOperators:
    """Test suite for Python-based graph operators"""

    @classmethod
    def setup_class(cls):
        """Setup Python operator tests"""
        if not IRIS_AVAILABLE:
            pytest.skip("IRIS Python driver not available")

        try:
            cls.conn = iris.connect(
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

            # Initialize operators
            cls.operators = IRISGraphOperators(cls.conn)

            print("✓ IRIS connection and Python operators initialized")

        except Exception as e:
            pytest.skip(f"IRIS database not accessible: {e}")

    @classmethod
    def teardown_class(cls):
        """Clean up Python operator tests"""
        if hasattr(cls, 'conn'):
            cls.conn.close()

    def test_kg_knn_vec_function(self):
        """Test kg_KNN_VEC Python function"""
        print("\n=== Testing kg_KNN_VEC Python Function ===")

        # Create a test vector (768 dimensions)
        test_vector = json.dumps([0.1] * 768)

        # Test without label filter
        results = self.operators.kg_KNN_VEC(test_vector, k=5)
        print(f"✓ kg_KNN_VEC (no filter): returned {len(results)} results")

        assert isinstance(results, list), "Results should be a list"
        assert len(results) <= 5, "Should return at most 5 results"

        if results:
            entity_id, score = results[0]
            assert isinstance(entity_id, str), "Entity ID should be string"
            assert isinstance(score, float), "Score should be float"
            assert 0 <= score <= 1, f"Score should be between 0 and 1, got {score}"
            print(f"  Sample result: {entity_id} = {score:.6f}")

        # Test with label filter
        results_filtered = self.operators.kg_KNN_VEC(test_vector, k=3, label_filter="protein")
        print(f"✓ kg_KNN_VEC (protein filter): returned {len(results_filtered)} results")

        # Filtered results should be subset or equal
        assert len(results_filtered) <= len(results), "Filtered results should be smaller or equal"

    def test_kg_txt_function(self):
        """Test kg_TXT Python function"""
        print("\n=== Testing kg_TXT Python Function ===")

        # Test text search
        results = self.operators.kg_TXT("protein", k=5)
        print(f"✓ kg_TXT: returned {len(results)} results")

        assert isinstance(results, list), "Results should be a list"
        assert len(results) <= 5, "Should return at most 5 results"

        if results:
            entity_id, score = results[0]
            assert isinstance(entity_id, str), "Entity ID should be string"
            assert isinstance(score, float), "Score should be float"
            assert score >= 0, f"Score should be non-negative, got {score}"
            print(f"  Sample result: {entity_id} = {score:.3f}")

    def test_kg_rrf_fuse_function(self):
        """Test kg_RRF_FUSE Python function"""
        print("\n=== Testing kg_RRF_FUSE Python Function ===")

        test_vector = json.dumps([0.1] * 768)

        # Test hybrid search
        results = self.operators.kg_RRF_FUSE(
            k=5,
            k1=10,
            k2=10,
            c=60,
            query_vector=test_vector,
            query_text="protein"
        )
        print(f"✓ kg_RRF_FUSE: returned {len(results)} results")

        assert isinstance(results, list), "Results should be a list"
        assert len(results) <= 5, "Should return at most 5 results"

        if results:
            entity_id, rrf_score, vector_score, text_score = results[0]
            assert isinstance(entity_id, str), "Entity ID should be string"
            assert isinstance(rrf_score, float), "RRF score should be float"
            assert isinstance(vector_score, float), "Vector score should be float"
            assert isinstance(text_score, float), "Text score should be float"
            assert rrf_score >= 0, f"RRF score should be non-negative, got {rrf_score}"
            print(f"  Sample result: {entity_id}")
            print(f"    RRF: {rrf_score:.3f}, Vector: {vector_score:.3f}, Text: {text_score:.3f}")

    def test_kg_graph_path_function(self):
        """Test kg_GRAPH_PATH Python function"""
        print("\n=== Testing kg_GRAPH_PATH Python Function ===")

        # Get a test entity first
        cursor = self.conn.cursor()
        cursor.execute("SELECT TOP 1 s FROM rdf_edges")
        result = cursor.fetchone()
        cursor.close()

        if result:
            test_entity = result[0]

            # Test path finding
            results = self.operators.kg_GRAPH_PATH(
                test_entity,
                "interacts_with",
                "associated_with"
            )
            print(f"✓ kg_GRAPH_PATH: returned {len(results)} path segments")

            assert isinstance(results, list), "Results should be a list"

            if results:
                path_id, step, s, p, o = results[0]
                assert isinstance(path_id, int), "Path ID should be integer"
                assert isinstance(step, int), "Step should be integer"
                assert isinstance(s, str), "Source should be string"
                assert isinstance(p, str), "Predicate should be string"
                assert isinstance(o, str), "Object should be string"
                print(f"  Sample path: {s} → {p} → {o} (step {step})")
        else:
            print("  No entities found for path testing")

    def test_kg_rerank_function(self):
        """Test kg_RERANK Python function"""
        print("\n=== Testing kg_RERANK Python Function ===")

        test_vector = json.dumps([0.1] * 768)

        # Test reranking
        results = self.operators.kg_RERANK(5, test_vector, "protein")
        print(f"✓ kg_RERANK: returned {len(results)} results")

        assert isinstance(results, list), "Results should be a list"
        assert len(results) <= 5, "Should return at most 5 results"

        if results:
            entity_id, score = results[0]
            assert isinstance(entity_id, str), "Entity ID should be string"
            assert isinstance(score, float), "Score should be float"
            assert score >= 0, f"Score should be non-negative, got {score}"
            print(f"  Sample result: {entity_id} = {score:.3f}")

    def test_performance_benchmarks(self):
        """Test performance of Python operators"""
        print("\n=== Testing Performance Benchmarks ===")

        import time
        test_vector = json.dumps([0.1] * 768)

        # Benchmark vector search
        start_time = time.time()
        vector_results = self.operators.kg_KNN_VEC(test_vector, k=10)
        vector_time = (time.time() - start_time) * 1000

        print(f"✓ Vector search (10 results): {vector_time:.2f}ms")
        assert vector_time < 30000, f"Vector search should be under 30s, got {vector_time:.2f}ms"

        # Benchmark text search
        start_time = time.time()
        text_results = self.operators.kg_TXT("gene", k=10)
        text_time = (time.time() - start_time) * 1000

        print(f"✓ Text search (10 results): {text_time:.2f}ms")
        assert text_time < 1000, f"Text search should be under 1s, got {text_time:.2f}ms"

        # Benchmark hybrid search
        start_time = time.time()
        hybrid_results = self.operators.kg_RRF_FUSE(k=5, query_vector=test_vector, query_text="protein")
        hybrid_time = (time.time() - start_time) * 1000

        print(f"✓ Hybrid search (5 results): {hybrid_time:.2f}ms")
        assert hybrid_time < 35000, f"Hybrid search should be under 35s, got {hybrid_time:.2f}ms"

    def test_data_integrity(self):
        """Test data integrity and consistency"""
        print("\n=== Testing Data Integrity ===")

        cursor = self.conn.cursor()

        # Check table counts
        cursor.execute("SELECT COUNT(*) FROM rdf_edges")
        edges_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM rdf_labels")
        labels_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM kg_NodeEmbeddings")
        embeddings_count = cursor.fetchone()[0]

        print(f"✓ Data integrity check:")
        print(f"  Edges: {edges_count:,}")
        print(f"  Labels: {labels_count:,}")
        print(f"  Embeddings: {embeddings_count:,}")

        assert edges_count > 0, "Should have relationship data"
        assert labels_count > 0, "Should have entity labels"
        assert embeddings_count > 0, "Should have vector embeddings"

        cursor.close()


if __name__ == "__main__":
    # Run tests individually for debugging
    print("Running Python Graph Operators Validation...")

    try:
        test_class = TestPythonGraphOperators()
        test_class.setup_class()

        test_class.test_kg_knn_vec_function()
        test_class.test_kg_txt_function()
        test_class.test_kg_rrf_fuse_function()
        test_class.test_kg_graph_path_function()
        test_class.test_kg_rerank_function()
        test_class.test_performance_benchmarks()
        test_class.test_data_integrity()

        test_class.teardown_class()

        print("\n✅ All Python operator tests completed successfully!")
        print("\nSummary of validated Python operators:")
        print("1. ✓ kg_KNN_VEC - Vector similarity search")
        print("2. ✓ kg_TXT - Text search in qualifiers")
        print("3. ✓ kg_RRF_FUSE - Hybrid search with RRF")
        print("4. ✓ kg_GRAPH_PATH - Graph path traversal")
        print("5. ✓ kg_RERANK - Result reranking")
        print("6. ✓ Performance benchmarks within acceptable bounds")
        print("7. ✓ Data integrity verified")

    except Exception as e:
        print(f"\n❌ Python operator validation failed: {e}")
        import traceback
        traceback.print_exc()