#!/usr/bin/env python3
"""
Performance Benchmark for IRIS Vector Graph enhancements.
Focuses on:
1. Batch Node Retrieval (get_nodes)
2. Substring indexing (iFind)
3. Transactional Mutations
"""

import time
import json
import random
import argparse
from typing import List, Dict, Any
from iris_devtester.utils.dbapi_compat import get_connection as iris_connect
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema

class PerformanceBenchmark:
    def __init__(self, entities: int = 10000):
        self.entities_count = entities
        self.conn = self._setup_connection()
        self.engine = IRISGraphEngine(self.conn)
        self.node_ids = [f"BENCH:NODE:{i}" for i in range(entities)]

    def _setup_connection(self):
        return iris_connect("localhost", 1972, "USER", "_SYSTEM", "SYS")

    def setup_data(self):
        print(f"--- Setting up {self.entities_count} entities ---")
        cursor = self.conn.cursor()
        
        # Clean up old bench data - reverse order for FKs
        cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s LIKE 'BENCH:%'")
        cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s LIKE 'BENCH:%'")
        cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE 'BENCH:%'")
        self.conn.commit()
        
        # Bulk load data
        nodes = []
        for i in range(self.entities_count):
            nodes.append({
                "id": self.node_ids[i],
                "labels": ["Benchmark", "TestNode"],
                "properties": {
                    "name": f"Node Name {i}",
                    "description": f"This is a long description for node {i} to test substring indexing capabilities in IRIS.",
                    "counter": i,
                    "metadata": {"tags": ["bench", str(i % 10)], "score": random.random()}
                }
            })
        
        start = time.time()
        self.engine.bulk_create_nodes(nodes)
        end = time.time()
        print(f"Bulk load completed in {end - start:.2f}s ({self.entities_count / (end - start):.0f} nodes/sec)")

    def test_get_nodes_performance(self):
        print("\n--- Benchmarking get_nodes() ---")
        
        batch_sizes = [1, 10, 100, 1000]
        for size in batch_sizes:
            # Pick random nodes
            sample_ids = random.sample(self.node_ids, size)
            
            start = time.time()
            nodes = self.engine.get_nodes(sample_ids)
            end = time.time()
            
            lat = (end - start) * 1000
            print(f"Batch size {size:4d}: {lat:7.2f}ms (total) | {lat/size:7.2f}ms (per node)")
            assert len(nodes) == size

    def test_substring_search(self):
        print("\n--- Benchmarking Substring Search (CONTAINS) ---")
        
        # We'll use direct SQL to measure CONTAINS performance
        # Since Cypher translation might add overhead, we want to see the DB performance
        cursor = self.conn.cursor()
        
        search_terms = ["Node Name 500", "description for node 99", "indexing capabilities"]
        
        for term in search_terms:
            start = time.time()
            cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_props WHERE val LIKE ?", [f"%{term}%"])
            count = cursor.fetchone()[0]
            end = time.time()
            
            print(f"Search for '{term}': {count} matches | {(end - start) * 1000:.2f}ms")

    def test_mutation_latency(self):
        print("\n--- Benchmarking Single Node Creation (Transactional) ---")
        
        node_id = "BENCH:SINGLE:MUT"
        labels = ["New", "Single"]
        props = {"timestamp": time.time(), "msg": "hello"}
        
        # Cleanup
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id = ?", [node_id])
        self.conn.commit()
        
        start = time.time()
        success = self.engine.create_node(node_id, labels, props)
        end = time.time()
        
        if success:
            print(f"create_node latency: {(end - start) * 1000:.2f}ms")
        else:
            print("create_node failed")

    def run_all(self):
        self.setup_data()
        self.test_get_nodes_performance()
        self.test_substring_search()
        self.test_mutation_latency()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--entities", type=int, default=10000)
    args = parser.parse_args()
    
    bench = PerformanceBenchmark(entities=args.entities)
    bench.run_all()
