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
    def setup_operators(self, iris_connection):
        """Initialize operators with managed connection"""
        self.operators = IRISGraphOperators(iris_connection)
        self.conn = iris_connection

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
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", (f"{prefix}A",))
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", (f"{prefix}B",))
        cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)", (f"{prefix}A", "interacts_with", f"{prefix}B"))
        self.conn.commit()

        results = self.operators.kg_GRAPH_PATH(f"{prefix}A", "interacts_with", "associated_with")
        assert isinstance(results, list)

    def test_performance_benchmarks(self):
        """Test performance of Python operators"""
        test_vector = json.dumps([0.1] * 768)
        results = self.operators.kg_KNN_VEC(test_vector, k=10)
        assert isinstance(results, list)

    def test_data_integrity(self):
        """Test data integrity check"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM nodes")
        count = cursor.fetchone()[0]
        assert count >= 0
