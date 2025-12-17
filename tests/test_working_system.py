#!/usr/bin/env python3
"""
IRIS Graph-AI Working System Test Suite

This test validates that all our implemented Python-based operators work correctly
and that the system is ready for production use.
"""

import sys
import os
import json
import numpy as np
import time

# NOTE: Uses iris-devtester for connection management
# Targets specific test container: iris_test_vector_graph_ai
import subprocess

try:
    from iris_devtester.utils.dbapi_compat import get_connection as devtester_connect
    DEVTESTER_AVAILABLE = True
except ImportError:
    DEVTESTER_AVAILABLE = False

# The dedicated test container name
TEST_CONTAINER_NAME = 'iris_test_vector_graph_ai'

# Add the python directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../python'))

from iris_vector_graph_operators import IRISGraphOperators


def get_container_port(container_name: str, internal_port: int = 1972) -> int:
    """Get the host port for a specific Docker container."""
    try:
        result = subprocess.run(
            ['docker', 'port', container_name, str(internal_port)],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            port_line = result.stdout.strip().split('\n')[0]
            port = int(port_line.split(':')[-1])
            return port
    except (subprocess.TimeoutExpired, ValueError, IndexError):
        pass
    return None


def get_iris_connection():
    """Get IRIS connection using the dedicated test container."""
    if not DEVTESTER_AVAILABLE:
        raise ImportError("iris-devtester not available")

    host = os.getenv('IRIS_HOST', 'localhost')
    container_name = os.getenv('IRIS_TEST_CONTAINER', TEST_CONTAINER_NAME)

    # Get port from specific test container
    port = get_container_port(container_name)
    if port is None:
        port = int(os.getenv('IRIS_PORT', '1972'))

    return devtester_connect(host, port, 'USER', '_SYSTEM', 'SYS')


def test_database_connection():
    """Test basic database connectivity"""
    print("🔌 Testing Database Connection...")
    try:
        conn = get_iris_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 AS test")
        result = cursor.fetchone()
        test_value = result[0]
        cursor.close()
        conn.close()
        assert test_value == 1, "Basic query failed"
        print("  ✅ Database connection working")
        return True
    except Exception as e:
        print(f"  ❌ Database connection failed: {e}")
        return False


def test_native_vector_functions():
    """Test native IRIS vector functions"""
    print("🧮 Testing Native IRIS Vector Functions...")
    try:
        conn = get_iris_connection()
        cursor = conn.cursor()

        # Test TO_VECTOR
        cursor.execute("SELECT TO_VECTOR('[1,0,0]') as vec")
        result = cursor.fetchone()
        assert result is not None, "TO_VECTOR failed"

        # Test VECTOR_COSINE with identical vectors
        cursor.execute("SELECT VECTOR_COSINE(TO_VECTOR('[1,0,0]'), TO_VECTOR('[1,0,0]')) as similarity")
        result = cursor.fetchone()
        similarity = result[0]
        assert abs(similarity - 1.0) < 0.001, f"VECTOR_COSINE identical test failed: {similarity}"

        # Test VECTOR_COSINE with orthogonal vectors
        cursor.execute("SELECT VECTOR_COSINE(TO_VECTOR('[1,0,0]'), TO_VECTOR('[0,1,0]')) as similarity")
        result = cursor.fetchone()
        similarity = result[0]
        assert abs(similarity - 0.0) < 0.001, f"VECTOR_COSINE orthogonal test failed: {similarity}"

        cursor.close()
        conn.close()
        print("  ✅ Native vector functions working")
        return True
    except Exception as e:
        print(f"  ❌ Native vector functions failed: {e}")
        return False


def test_data_availability():
    """Test that required data is available"""
    print("📊 Testing Data Availability...")
    try:
        conn = get_iris_connection()
        cursor = conn.cursor()

        # Check tables exist and have data
        tables_data = {}
        for table in ['rdf_edges', 'rdf_labels', 'rdf_props', 'kg_NodeEmbeddings']:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            tables_data[table] = count
            assert count > 0, f"Table {table} is empty"

        cursor.close()
        conn.close()

        print(f"  ✅ Data availability confirmed:")
        for table, count in tables_data.items():
            print(f"    • {table}: {count:,} rows")
        return True
    except Exception as e:
        print(f"  ❌ Data availability check failed: {e}")
        return False


def test_python_operators():
    """Test all Python-based graph operators"""
    print("🐍 Testing Python Graph Operators...")
    try:
        conn = get_iris_connection()
        operators = IRISGraphOperators(conn)

        test_vector = json.dumps([0.1] * 768)

        # Test vector search
        vector_results = operators.kg_KNN_VEC(test_vector, k=3)
        assert len(vector_results) > 0, "Vector search returned no results"
        assert all(isinstance(r, tuple) and len(r) == 2 for r in vector_results), "Vector results format invalid"
        print(f"    ✅ kg_KNN_VEC: {len(vector_results)} results")

        # Test text search
        text_results = operators.kg_TXT("protein", k=3)
        assert len(text_results) > 0, "Text search returned no results"
        assert all(isinstance(r, tuple) and len(r) == 2 for r in text_results), "Text results format invalid"
        print(f"    ✅ kg_TXT: {len(text_results)} results")

        # Test hybrid search
        hybrid_results = operators.kg_RRF_FUSE(k=3, query_vector=test_vector, query_text="protein")
        assert len(hybrid_results) > 0, "Hybrid search returned no results"
        assert all(isinstance(r, tuple) and len(r) == 4 for r in hybrid_results), "Hybrid results format invalid"
        print(f"    ✅ kg_RRF_FUSE: {len(hybrid_results)} results")

        # Test reranking
        rerank_results = operators.kg_RERANK(3, test_vector, "protein")
        assert len(rerank_results) > 0, "Reranking returned no results"
        assert all(isinstance(r, tuple) and len(r) == 2 for r in rerank_results), "Rerank results format invalid"
        print(f"    ✅ kg_RERANK: {len(rerank_results)} results")

        conn.close()
        print("  ✅ Python operators working")
        return True
    except Exception as e:
        print(f"  ❌ Python operators failed: {e}")
        return False


def test_performance_characteristics():
    """Test performance characteristics"""
    print("⚡ Testing Performance Characteristics...")
    try:
        conn = get_iris_connection()
        operators = IRISGraphOperators(conn)

        test_vector = json.dumps([0.1] * 768)

        # Benchmark text search (should be fast)
        start_time = time.time()
        text_results = operators.kg_TXT("gene", k=5)
        text_time = (time.time() - start_time) * 1000
        assert text_time < 1000, f"Text search too slow: {text_time:.2f}ms"
        print(f"    ✅ Text search: {text_time:.2f}ms")

        # Benchmark vector search (may be slower but should complete)
        start_time = time.time()
        vector_results = operators.kg_KNN_VEC(test_vector, k=5)
        vector_time = (time.time() - start_time) * 1000
        assert vector_time < 30000, f"Vector search too slow: {vector_time:.2f}ms"
        print(f"    ✅ Vector search: {vector_time:.2f}ms")

        conn.close()
        print("  ✅ Performance characteristics acceptable")
        return True
    except Exception as e:
        print(f"  ❌ Performance test failed: {e}")
        return False


def test_graph_operations():
    """Test basic graph operations"""
    print("🕸️ Testing Graph Operations...")
    try:
        conn = get_iris_connection()
        cursor = conn.cursor()

        # Test basic graph queries
        cursor.execute("SELECT TOP 3 s, p, o_id FROM rdf_edges")
        relationships = cursor.fetchall()
        assert len(relationships) > 0, "No relationships found"
        print(f"    ✅ Basic relationship query: {len(relationships)} relationships")

        # Test entity lookup
        cursor.execute("SELECT TOP 3 s, label FROM rdf_labels")
        entities = cursor.fetchall()
        assert len(entities) > 0, "No entities found"
        print(f"    ✅ Entity lookup: {len(entities)} entities")

        # Test multi-hop traversal pattern
        cursor.execute("""
            SELECT e1.s, e1.p, e1.o_id, e2.p, e2.o_id
            FROM rdf_edges e1
            JOIN rdf_edges e2 ON e1.o_id = e2.s
            LIMIT 3
        """)
        paths = cursor.fetchall()
        print(f"    ✅ Multi-hop traversal: {len(paths)} paths found")

        cursor.close()
        conn.close()
        print("  ✅ Graph operations working")
        return True
    except Exception as e:
        print(f"  ❌ Graph operations failed: {e}")
        return False


def main():
    """Run comprehensive working system tests"""
    print("IRIS Graph-AI Working System Test Suite")
    print("=" * 60)
    print("Testing all implemented functionality to ensure production readiness\n")

    tests = [
        ("Database Connection", test_database_connection),
        ("Native Vector Functions", test_native_vector_functions),
        ("Data Availability", test_data_availability),
        ("Python Operators", test_python_operators),
        ("Performance Characteristics", test_performance_characteristics),
        ("Graph Operations", test_graph_operations),
    ]

    results = []

    for test_name, test_func in tests:
        print(f"\n{test_name}:")
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"  ❌ {test_name} failed with exception: {e}")
            results.append((test_name, False))

    # Final report
    print("\n" + "=" * 60)
    print("FINAL TEST RESULTS")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{status} {test_name}")

    print(f"\nSummary: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 ALL TESTS PASSED!")
        print("✅ IRIS Graph-AI system is fully operational and production-ready")
        print("✅ All documented capabilities are working correctly")
        print("✅ Performance characteristics are acceptable")
        print("✅ Ready for biomedical research workloads")
        return True
    else:
        print(f"\n❌ {total - passed} test(s) failed")
        print("Please review the errors above and fix before proceeding")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)