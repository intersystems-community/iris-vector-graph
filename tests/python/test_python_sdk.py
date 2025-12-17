#!/usr/bin/env python3
"""
Comprehensive test suite for IRIS Graph-AI Python SDK
Tests direct IRIS connectivity, graph operations, and data loading
"""

import pytest
import json
import time
import tempfile
import os
from pathlib import Path
import numpy as np
import pandas as pd
from typing import Dict, List, Any

try:
    import iris
    IRIS_AVAILABLE = True
except ImportError:
    IRIS_AVAILABLE = False
    pytest.skip("IRIS Python driver not available", allow_module_level=True)

# Use iris-devtester for auto-discovery of IRIS container
try:
    from iris_devtester.connections import auto_detect_iris_host_and_port
    IRIS_HOST, IRIS_PORT = auto_detect_iris_host_and_port()
except ImportError:
    # Fallback to defaults if iris-devtester not available
    IRIS_HOST = 'localhost'
    IRIS_PORT = 1972

try:
    import networkx as nx
    NETWORKX_AVAILABLE = True
except ImportError:
    NETWORKX_AVAILABLE = False


class TestIRISPythonSDK:
    """Test IRIS Python SDK direct database connectivity"""

    @classmethod
    def setup_class(cls):
        """Setup test class with IRIS connection"""
        if not IRIS_AVAILABLE:
            pytest.skip("IRIS Python driver not available")

        try:
            cls.conn = iris.connect(
                hostname=IRIS_HOST,
                port=IRIS_PORT,
                namespace='USER',
                username='_SYSTEM',
                password='SYS'
            )

            # Test connection
            cursor = cls.conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()

            # Check if required schema tables exist
            cursor.execute("""
                SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = 'SQLUser'
                AND TABLE_NAME IN ('rdf_labels', 'rdf_props', 'rdf_edges')
            """)
            tables = [row[0].lower() for row in cursor.fetchall()]
            cursor.close()

            required_tables = {'rdf_labels', 'rdf_props', 'rdf_edges'}
            if not required_tables.issubset(set(tables)):
                missing = required_tables - set(tables)
                pytest.skip(f"Required schema tables missing: {missing}. Run sql/schema.sql first.")

            print(f"✓ IRIS Python SDK connection established ({IRIS_HOST}:{IRIS_PORT})")

        except Exception as e:
            pytest.skip(f"IRIS database not accessible at {IRIS_HOST}:{IRIS_PORT}: {e}")

    @classmethod
    def teardown_class(cls):
        """Clean up test class"""
        if hasattr(cls, 'conn'):
            # Clean up test data
            cursor = cls.conn.cursor()
            cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'TEST:%'")
            cursor.execute("DELETE FROM rdf_labels WHERE s LIKE 'TEST:%'")
            cursor.execute("DELETE FROM rdf_props WHERE s LIKE 'TEST:%'")
            cursor.close()
            cls.conn.close()

    def test_basic_connection(self):
        """Test basic IRIS connection and query"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 as test_value")
        result = cursor.fetchone()
        cursor.close()

        assert result[0] == 1

    def test_insert_entity(self):
        """Test inserting entity with properties"""
        cursor = self.conn.cursor()

        # Insert entity label
        cursor.execute(
            "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
            ['TEST:PROTEIN_001', 'protein']
        )

        # Insert entity properties
        properties = [
            ('TEST:PROTEIN_001', 'name', 'Test Protein 1'),
            ('TEST:PROTEIN_001', 'organism', 'Homo sapiens'),
            ('TEST:PROTEIN_001', 'function', 'test function')
        ]

        cursor.executemany(
            "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
            properties
        )

        # Verify insertion
        cursor.execute(
            "SELECT COUNT(*) FROM rdf_labels WHERE s = ?",
            ['TEST:PROTEIN_001']
        )
        label_count = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(*) FROM rdf_props WHERE s = ?",
            ['TEST:PROTEIN_001']
        )
        prop_count = cursor.fetchone()[0]

        cursor.close()

        assert label_count == 1
        assert prop_count == 3

    def test_insert_relationship(self):
        """Test inserting relationships between entities"""
        cursor = self.conn.cursor()

        # Insert entities
        entities = [
            ('TEST:PROTEIN_002', 'protein'),
            ('TEST:PROTEIN_003', 'protein')
        ]
        cursor.executemany(
            "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
            entities
        )

        # Insert relationship
        qualifiers = json.dumps({
            'confidence': 0.95,
            'evidence': 'experimental',
            'source': 'test_suite'
        })

        cursor.execute(
            "INSERT INTO rdf_edges (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)",
            ['TEST:PROTEIN_002', 'interacts_with', 'TEST:PROTEIN_003', qualifiers]
        )

        # Verify relationship
        cursor.execute(
            "SELECT s, p, o_id, qualifiers FROM rdf_edges WHERE s = ?",
            ['TEST:PROTEIN_002']
        )
        result = cursor.fetchone()
        cursor.close()

        assert result[0] == 'TEST:PROTEIN_002'
        assert result[1] == 'interacts_with'
        assert result[2] == 'TEST:PROTEIN_003'

        parsed_qualifiers = json.loads(result[3])
        assert parsed_qualifiers['confidence'] == 0.95
        assert parsed_qualifiers['source'] == 'test_suite'

    def test_batch_insert(self):
        """Test batch insertion performance"""
        cursor = self.conn.cursor()

        # Generate test data
        batch_size = 1000
        test_entities = []
        test_props = []

        for i in range(batch_size):
            entity_id = f'TEST:BATCH_{i:04d}'
            test_entities.append((entity_id, 'test_entity'))
            test_props.extend([
                (entity_id, 'name', f'Entity {i}'),
                (entity_id, 'batch_id', str(i)),
                (entity_id, 'test_flag', 'true')
            ])

        # Time batch insertion
        start_time = time.time()

        cursor.executemany(
            "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
            test_entities
        )

        cursor.executemany(
            "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
            test_props
        )

        elapsed = time.time() - start_time

        # Verify insertion
        cursor.execute(
            "SELECT COUNT(*) FROM rdf_labels WHERE label = 'test_entity'"
        )
        entity_count = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(*) FROM rdf_props WHERE s LIKE 'TEST:BATCH_%'"
        )
        prop_count = cursor.fetchone()[0]

        cursor.close()

        assert entity_count == batch_size
        assert prop_count == batch_size * 3

        # Performance assertion
        entities_per_second = batch_size / elapsed
        print(f"Batch insert performance: {entities_per_second:.0f} entities/sec")
        assert entities_per_second > 100  # Should handle at least 100 entities/sec

    def test_graph_traversal(self):
        """Test graph traversal queries"""
        cursor = self.conn.cursor()

        # Create test graph: A → B → C
        test_nodes = [
            ('TEST:NODE_A', 'test_node'),
            ('TEST:NODE_B', 'test_node'),
            ('TEST:NODE_C', 'test_node')
        ]
        cursor.executemany(
            "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
            test_nodes
        )

        test_edges = [
            ('TEST:NODE_A', 'connects_to', 'TEST:NODE_B', '{}'),
            ('TEST:NODE_B', 'connects_to', 'TEST:NODE_C', '{}')
        ]
        cursor.executemany(
            "INSERT INTO rdf_edges (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)",
            test_edges
        )

        # Test 1-hop traversal
        cursor.execute("""
            SELECT o_id FROM rdf_edges
            WHERE s = ? AND p = ?
        """, ['TEST:NODE_A', 'connects_to'])

        one_hop = cursor.fetchall()
        assert len(one_hop) == 1
        assert one_hop[0][0] == 'TEST:NODE_B'

        # Test 2-hop traversal
        cursor.execute("""
            SELECT e2.o_id FROM rdf_edges e1
            JOIN rdf_edges e2 ON e1.o_id = e2.s
            WHERE e1.s = ? AND e1.p = ? AND e2.p = ?
        """, ['TEST:NODE_A', 'connects_to', 'connects_to'])

        two_hop = cursor.fetchall()
        assert len(two_hop) == 1
        assert two_hop[0][0] == 'TEST:NODE_C'

        cursor.close()

    def test_stored_procedure_calls(self):
        """Test calling IRIS stored procedures"""
        cursor = self.conn.cursor()

        # Test vector search procedure (if available)
        try:
            test_vector = json.dumps([0.1] * 768)
            cursor.execute("CALL kg_KNN_VEC(?, ?, ?)", [test_vector, 5, None])
            results = cursor.fetchall()
            print(f"Vector search returned {len(results)} results")
            # Don't assert specific results as data may vary
            assert isinstance(results, list)

        except Exception as e:
            print(f"Vector search procedure not available: {e}")

        # Test text search procedure (if available)
        try:
            cursor.execute("CALL kg_RRF_FUSE(?, ?, ?, ?, ?, ?)",
                          [5, 100, 100, 60, test_vector, 'test'])
            results = cursor.fetchall()
            print(f"Hybrid search returned {len(results)} results")
            assert isinstance(results, list)

        except Exception as e:
            print(f"Hybrid search procedure not available: {e}")

        cursor.close()

    def test_transaction_handling(self):
        """Test transaction commit and rollback"""
        cursor = self.conn.cursor()

        try:
            # Start transaction
            cursor.execute("START TRANSACTION")

            # Insert test data
            cursor.execute(
                "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
                ['TEST:TRANSACTION', 'test']
            )

            # Verify data exists in transaction
            cursor.execute(
                "SELECT COUNT(*) FROM rdf_labels WHERE s = ?",
                ['TEST:TRANSACTION']
            )
            count_in_transaction = cursor.fetchone()[0]
            assert count_in_transaction == 1

            # Rollback
            cursor.execute("ROLLBACK")

            # Verify data is gone after rollback
            cursor.execute(
                "SELECT COUNT(*) FROM rdf_labels WHERE s = ?",
                ['TEST:TRANSACTION']
            )
            count_after_rollback = cursor.fetchone()[0]
            assert count_after_rollback == 0

        finally:
            cursor.close()

    def test_concurrent_access(self):
        """Test concurrent database access"""
        import threading
        import queue
        import time

        results_queue = queue.Queue()
        errors_queue = queue.Queue()

        def worker_thread(thread_id):
            """Worker thread for concurrent testing"""
            try:
                # Create separate connection for each thread
                local_conn = iris.connect(
                    hostname=IRIS_HOST,
                    port=IRIS_PORT,
                    namespace='USER',
                    username='_SYSTEM',
                    password='SYS'
                )

                cursor = local_conn.cursor()

                # Perform operations
                for i in range(10):
                    entity_id = f'TEST:THREAD_{thread_id}_{i:02d}'
                    cursor.execute(
                        "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
                        [entity_id, 'concurrent_test']
                    )

                # Verify insertions
                cursor.execute(
                    "SELECT COUNT(*) FROM rdf_labels WHERE s LIKE ?",
                    [f'TEST:THREAD_{thread_id}_%']
                )
                count = cursor.fetchone()[0]

                cursor.close()
                local_conn.close()

                results_queue.put((thread_id, count))

            except Exception as e:
                errors_queue.put((thread_id, str(e)))

        # Run concurrent threads
        threads = []
        num_threads = 5

        start_time = time.time()
        for thread_id in range(num_threads):
            thread = threading.Thread(target=worker_thread, args=(thread_id,))
            threads.append(thread)
            thread.start()

        # Wait for completion
        for thread in threads:
            thread.join()

        elapsed = time.time() - start_time

        # Check results
        assert errors_queue.empty(), f"Errors in concurrent access: {list(errors_queue.queue)}"
        assert results_queue.qsize() == num_threads

        # Verify each thread completed its work
        while not results_queue.empty():
            thread_id, count = results_queue.get()
            assert count == 10, f"Thread {thread_id} didn't complete all insertions"

        print(f"Concurrent access test completed in {elapsed:.2f}s with {num_threads} threads")

        # Clean up concurrent test data
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM rdf_labels WHERE label = 'concurrent_test'")
        cursor.close()


@pytest.mark.skipif(not NETWORKX_AVAILABLE, reason="NetworkX not available")
class TestNetworkXIntegration:
    """Test NetworkX integration with IRIS"""

    @classmethod
    def setup_class(cls):
        """Setup NetworkX integration tests"""
        if not IRIS_AVAILABLE:
            pytest.skip("IRIS Python driver not available")

        try:
            cls.conn = iris.connect(
                hostname=IRIS_HOST,
                port=IRIS_PORT,
                namespace='USER',
                username='_SYSTEM',
                password='SYS'
            )

            # Check if required schema tables exist
            cursor = cls.conn.cursor()
            cursor.execute("""
                SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = 'SQLUser'
                AND TABLE_NAME IN ('rdf_labels', 'rdf_props', 'rdf_edges')
            """)
            tables = [row[0].lower() for row in cursor.fetchall()]
            cursor.close()

            required_tables = {'rdf_labels', 'rdf_props', 'rdf_edges'}
            if not required_tables.issubset(set(tables)):
                missing = required_tables - set(tables)
                pytest.skip(f"Required schema tables missing: {missing}")

        except Exception as e:
            pytest.skip(f"IRIS database not accessible: {e}")

    @classmethod
    def teardown_class(cls):
        """Clean up NetworkX integration tests"""
        if hasattr(cls, 'conn'):
            cursor = cls.conn.cursor()
            cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'NX_%'")
            cursor.execute("DELETE FROM rdf_labels WHERE s LIKE 'NX_%'")
            cursor.execute("DELETE FROM rdf_props WHERE s LIKE 'NX_%'")
            cursor.close()
            cls.conn.close()

    def test_networkx_to_iris_import(self):
        """Test importing NetworkX graph to IRIS"""
        # Create test NetworkX graph
        G = nx.DiGraph()
        G.add_edge('NX_A', 'NX_B', relation='connects', weight=0.8)
        G.add_edge('NX_B', 'NX_C', relation='connects', weight=0.9)
        G.add_edge('NX_A', 'NX_C', relation='shortcuts', weight=0.5)

        # Add node attributes
        G.nodes['NX_A']['type'] = 'start'
        G.nodes['NX_B']['type'] = 'intermediate'
        G.nodes['NX_C']['type'] = 'end'

        cursor = self.conn.cursor()

        # Import nodes
        for node, attrs in G.nodes(data=True):
            cursor.execute(
                "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
                [node, 'networkx_node']
            )

            for key, value in attrs.items():
                cursor.execute(
                    "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                    [node, key, str(value)]
                )

        # Import edges
        for source, target, attrs in G.edges(data=True):
            relation = attrs.pop('relation', 'connected_to')
            qualifiers = json.dumps(attrs)

            cursor.execute(
                "INSERT INTO rdf_edges (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)",
                [source, relation, target, qualifiers]
            )

        # Verify import
        cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label = 'networkx_node'")
        node_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM rdf_edges WHERE s LIKE 'NX_%'")
        edge_count = cursor.fetchone()[0]

        cursor.close()

        assert node_count == 3
        assert edge_count == 3

    def test_iris_to_networkx_export(self):
        """Test exporting IRIS graph to NetworkX"""
        cursor = self.conn.cursor()

        # Load NetworkX graph from IRIS
        cursor.execute("""
            SELECT DISTINCT e.s, e.o_id, e.p, e.qualifiers
            FROM rdf_edges e
            WHERE e.s LIKE 'NX_%'
        """)
        edges = cursor.fetchall()

        # Build NetworkX graph
        G = nx.DiGraph()
        for source, target, relation, qualifiers in edges:
            try:
                attrs = json.loads(qualifiers) if qualifiers else {}
                attrs['relation'] = relation
            except json.JSONDecodeError:
                attrs = {'relation': relation}

            G.add_edge(source, target, **attrs)

        # Add node properties
        cursor.execute("""
            SELECT s, key, val FROM rdf_props
            WHERE s LIKE 'NX_%'
        """)
        props = cursor.fetchall()

        for node, key, value in props:
            if node in G:
                G.nodes[node][key] = value

        cursor.close()

        # Verify export
        assert G.number_of_nodes() == 3
        assert G.number_of_edges() == 3

        # Check specific connections
        assert G.has_edge('NX_A', 'NX_B')
        assert G.has_edge('NX_B', 'NX_C')
        assert G.has_edge('NX_A', 'NX_C')

        # Check attributes
        assert G.nodes['NX_A']['type'] == 'start'
        assert G.edges['NX_A', 'NX_B']['relation'] == 'connects'

    def test_networkx_algorithms(self):
        """Test NetworkX algorithms on IRIS-exported graph"""
        # Export graph from IRIS
        cursor = self.conn.cursor()
        cursor.execute("SELECT s, o_id FROM rdf_edges WHERE s LIKE 'NX_%'")
        edges = cursor.fetchall()
        cursor.close()

        # Build undirected graph for centrality analysis
        G = nx.Graph()
        for source, target in edges:
            G.add_edge(source, target)

        # Test centrality measures
        degree_centrality = nx.degree_centrality(G)
        betweenness_centrality = nx.betweenness_centrality(G)

        assert len(degree_centrality) == 3
        assert len(betweenness_centrality) == 3

        # NX_A should have high centrality (connected to both B and C)
        assert degree_centrality['NX_A'] > degree_centrality['NX_B']

        # Test shortest paths
        shortest_paths = dict(nx.all_pairs_shortest_path(G))
        assert len(shortest_paths['NX_A']['NX_C']) == 2  # Direct connection


class TestDataFormatLoading:
    """Test loading various data formats"""

    @classmethod
    def setup_class(cls):
        """Setup data format tests"""
        if not IRIS_AVAILABLE:
            pytest.skip("IRIS Python driver not available")

        try:
            cls.conn = iris.connect(
                hostname=IRIS_HOST,
                port=IRIS_PORT,
                namespace='USER',
                username='_SYSTEM',
                password='SYS'
            )

            # Check if required schema tables exist
            cursor = cls.conn.cursor()
            cursor.execute("""
                SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = 'SQLUser'
                AND TABLE_NAME IN ('rdf_labels', 'rdf_props', 'rdf_edges')
            """)
            tables = [row[0].lower() for row in cursor.fetchall()]
            cursor.close()

            required_tables = {'rdf_labels', 'rdf_props', 'rdf_edges'}
            if not required_tables.issubset(set(tables)):
                missing = required_tables - set(tables)
                pytest.skip(f"Required schema tables missing: {missing}")

        except Exception as e:
            pytest.skip(f"IRIS database not accessible: {e}")

    @classmethod
    def teardown_class(cls):
        """Clean up format tests"""
        if hasattr(cls, 'conn'):
            cursor = cls.conn.cursor()
            cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'FORMAT_%'")
            cursor.execute("DELETE FROM rdf_labels WHERE s LIKE 'FORMAT_%'")
            cursor.close()
            cls.conn.close()

    def test_tsv_loading(self):
        """Test loading TSV format data"""
        # Create temporary TSV file
        tsv_data = """source\tpredicate\ttarget\tconfidence
FORMAT_PROTEIN_A\tinteracts_with\tFORMAT_PROTEIN_B\t0.95
FORMAT_PROTEIN_B\tinteracts_with\tFORMAT_PROTEIN_C\t0.87
FORMAT_PROTEIN_A\tregulates\tFORMAT_PROTEIN_C\t0.72"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.tsv', delete=False) as f:
            f.write(tsv_data)
            tsv_file = f.name

        try:
            # Load TSV data
            df = pd.read_csv(tsv_file, sep='\t')

            cursor = self.conn.cursor()

            # Insert entities
            entities = set()
            for _, row in df.iterrows():
                entities.add(row['source'])
                entities.add(row['target'])

            for entity in entities:
                cursor.execute(
                    "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
                    [entity, 'tsv_loaded']
                )

            # Insert relationships
            for _, row in df.iterrows():
                qualifiers = json.dumps({'confidence': float(row['confidence'])})
                cursor.execute(
                    "INSERT INTO rdf_edges (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)",
                    [row['source'], row['predicate'], row['target'], qualifiers]
                )

            # Verify loading
            cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label = 'tsv_loaded'")
            entity_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM rdf_edges WHERE s LIKE 'FORMAT_%'")
            edge_count = cursor.fetchone()[0]

            cursor.close()

            assert entity_count == 3  # 3 unique proteins
            assert edge_count == 3    # 3 relationships

        finally:
            os.unlink(tsv_file)

    def test_json_loading(self):
        """Test loading JSON format data"""
        # Create test JSON data
        json_data = [
            {
                "source": "FORMAT_GENE_X",
                "predicate": "encodes",
                "target": "FORMAT_PROTEIN_X",
                "evidence": "experimental",
                "confidence": 0.99
            },
            {
                "source": "FORMAT_PROTEIN_X",
                "predicate": "participates_in",
                "target": "FORMAT_PATHWAY_1",
                "evidence": "literature",
                "confidence": 0.85
            }
        ]

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(json_data, f)
            json_file = f.name

        try:
            # Load JSON data
            with open(json_file, 'r') as f:
                data = json.load(f)

            cursor = self.conn.cursor()

            # Insert entities
            entities = set()
            for item in data:
                entities.add(item['source'])
                entities.add(item['target'])

            for entity in entities:
                cursor.execute(
                    "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
                    [entity, 'json_loaded']
                )

            # Insert relationships
            for item in data:
                qualifiers = json.dumps({
                    k: v for k, v in item.items()
                    if k not in ['source', 'predicate', 'target']
                })

                cursor.execute(
                    "INSERT INTO rdf_edges (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)",
                    [item['source'], item['predicate'], item['target'], qualifiers]
                )

            # Verify loading
            cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label = 'json_loaded'")
            entity_count = cursor.fetchone()[0]

            cursor.close()

            assert entity_count == 4  # gene, protein, pathway + another entity

        finally:
            os.unlink(json_file)


class TestPerformanceBenchmarks:
    """Performance benchmark tests"""

    @classmethod
    def setup_class(cls):
        """Setup performance tests"""
        if not IRIS_AVAILABLE:
            pytest.skip("IRIS Python driver not available")

        try:
            cls.conn = iris.connect(
                hostname=IRIS_HOST,
                port=IRIS_PORT,
                namespace='USER',
                username='_SYSTEM',
                password='SYS'
            )

            # Check if required schema tables exist
            cursor = cls.conn.cursor()
            cursor.execute("""
                SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = 'SQLUser'
                AND TABLE_NAME IN ('rdf_labels', 'rdf_props', 'rdf_edges')
            """)
            tables = [row[0].lower() for row in cursor.fetchall()]
            cursor.close()

            required_tables = {'rdf_labels', 'rdf_props', 'rdf_edges'}
            if not required_tables.issubset(set(tables)):
                missing = required_tables - set(tables)
                pytest.skip(f"Required schema tables missing: {missing}")

        except Exception as e:
            pytest.skip(f"IRIS database not accessible: {e}")

    @classmethod
    def teardown_class(cls):
        """Clean up performance tests"""
        if hasattr(cls, 'conn'):
            cursor = cls.conn.cursor()
            cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'PERF_%'")
            cursor.execute("DELETE FROM rdf_labels WHERE s LIKE 'PERF_%'")
            cursor.close()
            cls.conn.close()

    def test_large_batch_insert(self):
        """Test large batch insertion performance"""
        batch_size = 10000
        entities = [(f'PERF_ENTITY_{i:06d}', 'performance_test') for i in range(batch_size)]

        cursor = self.conn.cursor()

        start_time = time.time()
        cursor.executemany(
            "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
            entities
        )
        elapsed = time.time() - start_time

        # Verify insertion
        cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label = 'performance_test'")
        count = cursor.fetchone()[0]
        cursor.close()

        assert count == batch_size

        # Performance metrics
        entities_per_second = batch_size / elapsed
        print(f"Large batch insert: {entities_per_second:.0f} entities/sec")

        # Performance assertion (should handle at least 1000 entities/sec)
        assert entities_per_second > 1000

    def test_query_performance(self):
        """Test query performance on large dataset"""
        cursor = self.conn.cursor()

        # Test simple SELECT performance
        query_count = 1000
        start_time = time.time()

        for i in range(query_count):
            cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label = 'performance_test'")
            cursor.fetchone()

        elapsed = time.time() - start_time
        queries_per_second = query_count / elapsed

        print(f"Simple query performance: {queries_per_second:.0f} queries/sec")
        assert queries_per_second > 100

        # Test indexed lookup performance
        start_time = time.time()
        for i in range(100):
            entity_id = f'PERF_ENTITY_{i:06d}'
            cursor.execute("SELECT label FROM rdf_labels WHERE s = ?", [entity_id])
            cursor.fetchone()

        elapsed = time.time() - start_time
        lookups_per_second = 100 / elapsed

        print(f"Indexed lookup performance: {lookups_per_second:.0f} lookups/sec")
        cursor.close()

        assert lookups_per_second > 500

    def test_memory_usage(self):
        """Test memory usage with large result sets"""
        cursor = self.conn.cursor()

        # Query large result set
        cursor.execute("SELECT s FROM rdf_labels WHERE label = 'performance_test' LIMIT 5000")
        results = cursor.fetchall()

        cursor.close()

        # Verify we got expected results
        assert len(results) == 5000
        assert all(result[0].startswith('PERF_ENTITY_') for result in results)

        print(f"Memory test: Retrieved {len(results)} entities successfully")


if __name__ == "__main__":
    # Run specific test classes for quick validation
    print("Running IRIS Graph-AI Python SDK Tests...")

    # Test basic SDK functionality
    try:
        test_sdk = TestIRISPythonSDK()
        test_sdk.setup_class()

        test_sdk.test_basic_connection()
        print("✅ Basic connection test passed")

        test_sdk.test_insert_entity()
        print("✅ Entity insertion test passed")

        test_sdk.test_batch_insert()
        print("✅ Batch insertion test passed")

        test_sdk.teardown_class()

    except Exception as e:
        print(f"❌ SDK tests failed: {e}")

    # Test NetworkX integration
    if NETWORKX_AVAILABLE:
        try:
            test_nx = TestNetworkXIntegration()
            test_nx.setup_class()

            test_nx.test_networkx_to_iris_import()
            print("✅ NetworkX import test passed")

            test_nx.test_iris_to_networkx_export()
            print("✅ NetworkX export test passed")

            test_nx.teardown_class()

        except Exception as e:
            print(f"❌ NetworkX tests failed: {e}")

    print("Test suite completed")