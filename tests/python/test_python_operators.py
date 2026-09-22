#!/usr/bin/env python3
"""
Python Graph Operators Validation Test
Tests that our Python-based graph operators work correctly
"""

import sys
import os
import pytest
import json
import importlib
import numpy as np
from iris_vector_graph.operators import IRISGraphOperators

# Mark all tests as requiring live database
pytestmark = pytest.mark.requires_database


class TestPythonGraphOperators:
    """Test suite for Python-based graph operators using managed container"""

    @pytest.fixture(autouse=True)
    def setup_operators(self, engine):
        """Initialize operators with the managed connection.

        `engine` used to come from `tests/integration/conftest.py`, which this directory
        cannot see, so every test here ended in `fixture 'engine' not found` — and the
        body referenced a bare `iris_connection` that was never a parameter, so even with
        the fixture present it would have raised NameError. Both go through `engine.conn`
        now, and the fixture lives in `tests/python/conftest.py`.
        """
        self.engine = engine
        self.operators = IRISGraphOperators(engine.conn)
        self.conn = engine.conn

    def test_kg_knn_vec_function(self):
        """Test kg_KNN_VEC Python function"""
        # Create a test vector (768 dimensions)
        test_vector = json.dumps([0.1] * 768)

        # Test without label filter
        results = self.operators.kg_KNN_VEC(test_vector, k=5)
        assert isinstance(results, list)
        assert len(results) <= 5

    def test_kg_txt_function(self):
        """Test kg_TXT Python function"""
        results = self.operators.kg_TXT("protein", k=5)
        assert isinstance(results, list)
        assert len(results) <= 5

    def test_kg_rrf_fuse_function(self):
        """Test kg_RRF_FUSE Python function"""
        test_vector = json.dumps([0.1] * 768)
        results = self.operators.kg_RRF_FUSE(
            k=5, k1=10, k2=10, c=60,
            query_vector=test_vector,
            query_text="protein"
        )
        assert isinstance(results, list)
        assert len(results) <= 5

    def test_kg_graph_path_function(self, clean_test_data):
        """Test kg_GRAPH_PATH Python function"""
        prefix = clean_test_data
        cursor = self.conn.cursor()
        
        # Create test data
        # Unqualified `nodes` / `rdf_edges` resolve to SQLUser, not Graph_KG, and 4.0.0's
        # edge FKs are composite — (graph_id, s) and (graph_id, o_id) against nodes — so
        # a raw edge INSERT needs both endpoints registered in the same graph. The engine
        # does all of that, and writes the ^KG adjacency the operators read besides.
        # `kg_GRAPH_PATH` matches two hops, A-[pred1]->B-[pred2]->C, so one edge could
        # never satisfy it: the old data made the result empty by construction and
        # `isinstance(results, list)` passed on that empty list.
        self.engine.create_node(f"{prefix}A")
        self.engine.create_node(f"{prefix}B")
        self.engine.create_node(f"{prefix}C")
        self.engine.create_edge(f"{prefix}A", "interacts_with", f"{prefix}B")
        self.engine.create_edge(f"{prefix}B", "associated_with", f"{prefix}C")
        self.engine.sync()

        results = self.operators.kg_GRAPH_PATH(f"{prefix}A", "interacts_with", "associated_with")
        assert isinstance(results, list)
        steps = {(row[1], row[2], row[3], row[4]) for row in results}
        assert (1, f"{prefix}A", "interacts_with", f"{prefix}B") in steps, results
        assert (2, f"{prefix}B", "associated_with", f"{prefix}C") in steps, results

    def test_performance_benchmarks(self):
        """Test performance of Python operators"""
        test_vector = json.dumps([0.1] * 768)
        results = self.operators.kg_KNN_VEC(test_vector, k=10)
        assert isinstance(results, list)

    def test_data_integrity(self):
        """Test data integrity check"""
        cursor = self.conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {self.engine._t('nodes')}")
        count = cursor.fetchone()[0]
        assert count >= 0
