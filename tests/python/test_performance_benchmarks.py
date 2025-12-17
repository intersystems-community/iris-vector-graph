#!/usr/bin/env python3
"""
Performance benchmark test suite for IRIS Graph-AI
Tests system performance at scale and compares different access methods

Uses iris-devtester for connection management.
Targets specific test container: iris_test_vector_graph_ai
"""

import pytest
import time
import json
import os
import subprocess
import numpy as np
import requests
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple
import logging

try:
    from iris_devtester.utils.dbapi_compat import get_connection as devtester_connect
    DEVTESTER_AVAILABLE = True
except ImportError:
    DEVTESTER_AVAILABLE = False
    pytest.skip("iris-devtester not available", allow_module_level=True)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# The dedicated test container name
TEST_CONTAINER_NAME = 'iris_test_vector_graph_ai'


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
        port = int(os.getenv('IRIS_TEST_PORT', '1972'))

    return devtester_connect(host, port, 'USER', '_SYSTEM', 'SYS')


class TestPerformanceBenchmarks:
    """Comprehensive performance benchmarks for IRIS Graph-AI"""

    @classmethod
    def setup_class(cls):
        """Setup performance test environment"""
        if not DEVTESTER_AVAILABLE:
            pytest.skip("iris-devtester not available")

        try:
            cls.conn = get_iris_connection()

            # Test connection
            cursor = cls.conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()

            cls.rest_api_base = "http://localhost:52773/kg"

            # Verify REST API is available
            response = requests.get(f"{cls.rest_api_base}/health", timeout=5)
            if response.status_code != 200:
                logger.warning("REST API not available, some tests will be skipped")
                cls.rest_api_available = False
            else:
                cls.rest_api_available = True

            logger.info("✓ Performance test environment ready")

        except Exception as e:
            pytest.skip(f"Test environment not available: {e}")

    @classmethod
    def teardown_class(cls):
        """Clean up performance test data"""
        if hasattr(cls, 'conn'):
            cursor = cls.conn.cursor()
            cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'PERF_%'")
            cursor.execute("DELETE FROM rdf_labels WHERE s LIKE 'PERF_%'")
            cursor.execute("DELETE FROM rdf_props WHERE s LIKE 'PERF_%'")
            cursor.execute("DELETE FROM kg_NodeEmbeddings WHERE id LIKE 'PERF_%'")
            cursor.close()
            cls.conn.close()

    def test_bulk_data_ingestion_performance(self):
        """Test large-scale data ingestion performance"""
        entity_counts = [1000, 5000, 10000]
        results = {}

        for count in entity_counts:
            logger.info(f"Testing bulk ingestion of {count} entities...")

            # Generate test data
            entities = [(f'PERF_BULK_{i:06d}', 'performance_entity') for i in range(count)]
            properties = []
            for i in range(count):
                entity_id = f'PERF_BULK_{i:06d}'
                properties.extend([
                    (entity_id, 'name', f'Entity {i}'),
                    (entity_id, 'index', str(i)),
                    (entity_id, 'category', 'bulk_test')
                ])

            cursor = self.conn.cursor()

            # Time entity insertion
            start_time = time.time()
            cursor.executemany(
                "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
                entities
            )
            entity_time = time.time() - start_time

            # Time property insertion
            start_time = time.time()
            cursor.executemany(
                "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                properties
            )
            property_time = time.time() - start_time

            cursor.close()

            total_time = entity_time + property_time
            entities_per_second = count / total_time

            results[count] = {
                'total_time': total_time,
                'entity_time': entity_time,
                'property_time': property_time,
                'entities_per_second': entities_per_second
            }

            logger.info(f"  {count} entities: {entities_per_second:.0f} entities/sec")

        # Verify performance scaling
        for count in entity_counts:
            assert results[count]['entities_per_second'] > 100, \
                f"Performance too slow for {count} entities: {results[count]['entities_per_second']:.0f} entities/sec"

        # Log performance summary
        logger.info("Bulk ingestion performance summary:")
        for count, result in results.items():
            logger.info(f"  {count:5d} entities: {result['entities_per_second']:6.0f} entities/sec "
                       f"(total: {result['total_time']:5.2f}s)")

    def test_query_performance_scaling(self):
        """Test query performance with different dataset sizes"""
        # Ensure we have test data
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label = 'performance_entity'")
        entity_count = cursor.fetchone()[0]

        if entity_count < 1000:
            pytest.skip("Insufficient test data for query performance testing")

        query_types = [
            ("Simple count", "SELECT COUNT(*) FROM rdf_labels WHERE label = 'performance_entity'"),
            ("Indexed lookup", "SELECT label FROM rdf_labels WHERE s = 'PERF_BULK_000500'"),
            ("Property search", "SELECT s FROM rdf_props WHERE key = 'category' AND val = 'bulk_test'"),
            ("Join query", """
                SELECT l.s, p.val FROM rdf_labels l
                JOIN rdf_props p ON l.s = p.s
                WHERE l.label = 'performance_entity' AND p.key = 'name'
                LIMIT 100
            """)
        ]

        results = {}

        for query_name, query in query_types:
            logger.info(f"Testing {query_name.lower()}...")

            # Run query multiple times for statistical significance
            times = []
            for _ in range(20):
                start_time = time.time()
                cursor.execute(query)
                cursor.fetchall()
                elapsed = time.time() - start_time
                times.append(elapsed)

            avg_time = statistics.mean(times)
            median_time = statistics.median(times)
            p95_time = np.percentile(times, 95)

            results[query_name] = {
                'avg_ms': avg_time * 1000,
                'median_ms': median_time * 1000,
                'p95_ms': p95_time * 1000
            }

            logger.info(f"  Avg: {avg_time*1000:.2f}ms, P95: {p95_time*1000:.2f}ms")

        cursor.close()

        # Performance assertions
        assert results['Simple count']['avg_ms'] < 100, "Simple count query too slow"
        assert results['Indexed lookup']['avg_ms'] < 10, "Indexed lookup too slow"

        # Log performance summary
        logger.info("Query performance summary:")
        for query_name, metrics in results.items():
            logger.info(f"  {query_name:15s}: avg={metrics['avg_ms']:6.2f}ms "
                       f"p95={metrics['p95_ms']:6.2f}ms")

    def test_rest_api_performance(self):
        """Test REST API performance vs direct IRIS connection - skipped if REST API not available"""
        if not getattr(self.__class__, 'rest_api_available', False):
            pytest.skip("REST API not available")
        # Generate test vector
        test_vector = np.random.rand(768).tolist()

        # Test direct IRIS vector search (if procedure exists)
        iris_times = []
        cursor = self.conn.cursor()

        try:
            for _ in range(10):
                start_time = time.time()
                cursor.execute("CALL kg_KNN_VEC(?, ?, ?)", [json.dumps(test_vector), 5, None])
                cursor.fetchall()
                elapsed = time.time() - start_time
                iris_times.append(elapsed)

            iris_avg = statistics.mean(iris_times) * 1000  # Convert to ms
            logger.info(f"Direct IRIS vector search: {iris_avg:.2f}ms avg")

        except Exception as e:
            logger.info(f"Direct IRIS vector search not available: {e}")
            iris_avg = None

        cursor.close()

        # Test REST API vector search
        rest_times = []
        for _ in range(10):
            start_time = time.time()
            response = requests.post(
                f"{self.rest_api_base}/vectorSearch",
                json={'vector': test_vector, 'k': 5},
                timeout=10
            )
            elapsed = time.time() - start_time

            if response.status_code == 200:
                rest_times.append(elapsed)
            else:
                logger.warning(f"REST API request failed: {response.status_code}")

        if rest_times:
            rest_avg = statistics.mean(rest_times) * 1000  # Convert to ms
            logger.info(f"REST API vector search: {rest_avg:.2f}ms avg")

            # Performance assertions
            assert rest_avg < 100, f"REST API too slow: {rest_avg:.2f}ms"

            # Compare if both available
            if iris_avg:
                overhead = rest_avg - iris_avg
                logger.info(f"REST API overhead: {overhead:.2f}ms ({overhead/iris_avg*100:.1f}%)")

    def test_concurrent_access_performance(self):
        """Test performance under concurrent load"""
        # Test both direct IRIS and REST API concurrency

        def iris_worker(worker_id: int, iterations: int) -> Dict:
            """Worker function for direct IRIS access"""
            local_conn = get_iris_connection()

            times = []
            errors = 0

            try:
                cursor = local_conn.cursor()

                for i in range(iterations):
                    start_time = time.time()
                    cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label = 'performance_entity'")
                    cursor.fetchone()
                    elapsed = time.time() - start_time
                    times.append(elapsed)

                cursor.close()

            except Exception as e:
                errors += 1
                logger.error(f"Worker {worker_id} error: {e}")

            finally:
                local_conn.close()

            return {
                'worker_id': worker_id,
                'times': times,
                'errors': errors,
                'avg_time': statistics.mean(times) if times else 0
            }

        def rest_worker(worker_id: int, iterations: int) -> Dict:
            """Worker function for REST API access"""
            times = []
            errors = 0

            test_vector = [0.1] * 768

            for i in range(iterations):
                try:
                    start_time = time.time()
                    response = requests.post(
                        f"{self.rest_api_base}/vectorSearch",
                        json={'vector': test_vector, 'k': 5},
                        timeout=10
                    )
                    elapsed = time.time() - start_time

                    if response.status_code == 200:
                        times.append(elapsed)
                    else:
                        errors += 1

                except Exception as e:
                    errors += 1

            return {
                'worker_id': worker_id,
                'times': times,
                'errors': errors,
                'avg_time': statistics.mean(times) if times else 0
            }

        # Test concurrent IRIS access
        logger.info("Testing concurrent IRIS access...")
        num_workers = 5
        iterations_per_worker = 20

        start_time = time.time()
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [
                executor.submit(iris_worker, i, iterations_per_worker)
                for i in range(num_workers)
            ]
            iris_results = [future.result() for future in as_completed(futures)]

        iris_duration = time.time() - start_time

        # Analyze IRIS results
        total_iris_queries = sum(len(r['times']) for r in iris_results)
        total_iris_errors = sum(r['errors'] for r in iris_results)
        iris_avg_time = statistics.mean([t for r in iris_results for t in r['times']])

        logger.info(f"IRIS concurrent test: {total_iris_queries} queries in {iris_duration:.2f}s")
        logger.info(f"  Throughput: {total_iris_queries/iris_duration:.1f} queries/sec")
        logger.info(f"  Average latency: {iris_avg_time*1000:.2f}ms")
        logger.info(f"  Errors: {total_iris_errors}")

        # Test concurrent REST API access (if available)
        if self.rest_api_available:
            logger.info("Testing concurrent REST API access...")

            start_time = time.time()
            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = [
                    executor.submit(rest_worker, i, iterations_per_worker//2)  # Fewer iterations for REST
                    for i in range(num_workers)
                ]
                rest_results = [future.result() for future in as_completed(futures)]

            rest_duration = time.time() - start_time

            # Analyze REST results
            total_rest_queries = sum(len(r['times']) for r in rest_results)
            total_rest_errors = sum(r['errors'] for r in rest_results)

            if total_rest_queries > 0:
                rest_avg_time = statistics.mean([t for r in rest_results for t in r['times']])

                logger.info(f"REST concurrent test: {total_rest_queries} queries in {rest_duration:.2f}s")
                logger.info(f"  Throughput: {total_rest_queries/rest_duration:.1f} queries/sec")
                logger.info(f"  Average latency: {rest_avg_time*1000:.2f}ms")
                logger.info(f"  Errors: {total_rest_errors}")

        # Performance assertions
        assert total_iris_errors == 0, "IRIS concurrent access should not have errors"
        assert total_iris_queries / iris_duration > 50, "IRIS throughput too low under concurrency"

    def test_memory_usage_scaling(self):
        """Test memory usage with large result sets"""
        import psutil
        import os

        # Get initial memory usage
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        cursor = self.conn.cursor()

        # Test progressively larger result sets
        result_sizes = [100, 1000, 5000]
        memory_usage = {}

        for size in result_sizes:
            # Query large result set
            cursor.execute(f"""
                SELECT s, label FROM rdf_labels
                WHERE label = 'performance_entity'
                LIMIT {size}
            """)

            results = cursor.fetchall()
            current_memory = process.memory_info().rss / 1024 / 1024  # MB
            memory_usage[size] = current_memory - initial_memory

            logger.info(f"Result set {size}: {len(results)} rows, "
                       f"memory usage: {memory_usage[size]:.1f}MB")

            # Clear results to measure incremental usage
            del results

        cursor.close()

        # Memory usage should be reasonable
        for size, memory in memory_usage.items():
            memory_per_row = memory / size * 1024  # KB per row
            assert memory_per_row < 1.0, f"Memory usage too high: {memory_per_row:.2f}KB per row"

        logger.info("Memory usage scaling test passed")

    def test_index_performance(self):
        """Test performance with and without indexes"""
        cursor = self.conn.cursor()

        # Test query performance on indexed columns
        indexed_queries = [
            ("Primary key lookup", "SELECT label FROM rdf_labels WHERE s = 'PERF_BULK_001000'"),
            ("Label index", "SELECT COUNT(*) FROM rdf_labels WHERE label = 'performance_entity'"),
        ]

        for query_name, query in indexed_queries:
            times = []
            for _ in range(10):
                start_time = time.time()
                cursor.execute(query)
                cursor.fetchall()
                elapsed = time.time() - start_time
                times.append(elapsed)

            avg_time = statistics.mean(times) * 1000  # ms
            logger.info(f"Index performance - {query_name}: {avg_time:.2f}ms avg")

            # Indexed queries should be very fast
            assert avg_time < 50, f"Indexed query too slow: {query_name} = {avg_time:.2f}ms"

        cursor.close()

    def test_vector_operations_performance(self):
        """Test vector operations performance if available"""
        cursor = self.conn.cursor()

        # Check if vector operations are available
        try:
            test_vector = json.dumps([0.1] * 768)
            cursor.execute("CALL kg_KNN_VEC(?, ?, ?)", [test_vector, 1, None])
            cursor.fetchall()
            vector_ops_available = True
        except Exception:
            vector_ops_available = False

        if not vector_ops_available:
            pytest.skip("Vector operations not available")

        # Test vector search performance
        vector_sizes = [10, 50, 100]
        vector_times = {}

        for k in vector_sizes:
            times = []
            for _ in range(5):  # Fewer iterations for vector ops
                start_time = time.time()
                cursor.execute("CALL kg_KNN_VEC(?, ?, ?)", [test_vector, k, None])
                cursor.fetchall()
                elapsed = time.time() - start_time
                times.append(elapsed)

            avg_time = statistics.mean(times) * 1000  # ms
            vector_times[k] = avg_time
            logger.info(f"Vector search k={k}: {avg_time:.2f}ms avg")

        cursor.close()

        # Vector operations should be reasonably fast
        for k, time_ms in vector_times.items():
            assert time_ms < 500, f"Vector search too slow for k={k}: {time_ms:.2f}ms"

        logger.info("Vector operations performance test completed")

    def test_batch_vs_individual_operations(self):
        """Compare batch vs individual operation performance"""
        cursor = self.conn.cursor()

        # Test individual inserts
        individual_data = [(f'PERF_INDIVIDUAL_{i:04d}', 'individual_test') for i in range(1000)]

        start_time = time.time()
        for entity_id, label in individual_data:
            cursor.execute("INSERT INTO rdf_labels (s, label) VALUES (?, ?)", [entity_id, label])
        individual_time = time.time() - start_time

        # Test batch inserts
        batch_data = [(f'PERF_BATCH_{i:04d}', 'batch_test') for i in range(1000)]

        start_time = time.time()
        cursor.executemany("INSERT INTO rdf_labels (s, label) VALUES (?, ?)", batch_data)
        batch_time = time.time() - start_time

        cursor.close()

        # Batch should be significantly faster
        speedup = individual_time / batch_time
        logger.info(f"Individual inserts: {individual_time:.2f}s ({1000/individual_time:.0f} ops/sec)")
        logger.info(f"Batch inserts: {batch_time:.2f}s ({1000/batch_time:.0f} ops/sec)")
        logger.info(f"Batch speedup: {speedup:.1f}x")

        assert speedup > 2, f"Batch operations should be faster: {speedup:.1f}x speedup"
        assert batch_time < 5, f"Batch operations too slow: {batch_time:.2f}s for 1000 inserts"


if __name__ == "__main__":
    # Run performance benchmarks
    print("Running IRIS Graph-AI Performance Benchmarks...")

    try:
        bench = TestPerformanceBenchmarks()
        bench.setup_class()

        print("\n=== Bulk Data Ingestion Performance ===")
        bench.test_bulk_data_ingestion_performance()

        print("\n=== Query Performance Scaling ===")
        bench.test_query_performance_scaling()

        print("\n=== Concurrent Access Performance ===")
        bench.test_concurrent_access_performance()

        print("\n=== Batch vs Individual Operations ===")
        bench.test_batch_vs_individual_operations()

        bench.teardown_class()

        print("\n✅ All performance benchmarks completed successfully")

    except Exception as e:
        print(f"❌ Performance benchmarks failed: {e}")
        import traceback
        traceback.print_exc()

    print("Benchmark suite completed")