#!/usr/bin/env python3
"""
Test suite for NetworkX loader CLI tool
Tests all format support and CLI functionality
"""

import pytest
import tempfile
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    import networkx as nx
    NETWORKX_AVAILABLE = True
except ImportError:
    NETWORKX_AVAILABLE = False
    pytest.skip("NetworkX not available", allow_module_level=True)


NODE_UPSERT_SQL = (
    "INSERT INTO Graph_KG.nodes (node_id) "
    "SELECT ? "
    "WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.nodes WHERE node_id = ?)"
)


def ensure_nodes_exist(cursor, node_ids):
    seen = set()
    for node_id in node_ids:
        if node_id in seen:
            continue
        seen.add(node_id)
        cursor.execute(NODE_UPSERT_SQL, (node_id, node_id))


@pytest.fixture(scope="module", autouse=True)
def inject_iris_connection(iris_connection):
    """Inject the shared iris_connection into the NetworkX loader tests."""
    TestNetworkXLoader.conn = iris_connection


ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))
from scripts.ingest.networkx_loader import NetworkXIRISLoader


class TestNetworkXLoader:
    """Test NetworkX loader API integration"""

    conn: Any = None

    @classmethod
    def setup_class(cls):
        """Setup test class"""
        assert cls.conn is not None, "iris_connection fixture did not inject a connection"

    @classmethod
    def teardown_class(cls):
        """Clean up test data"""
        try:
            cursor = cls.conn.cursor()
            cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s LIKE 'LOADER_%'")
            cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s LIKE 'LOADER_%'")
            cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s LIKE 'LOADER_%'")
            cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE 'LOADER_%'")
            cursor.close()
        except Exception:
            pass

    def _create_loader(self):
        loader = NetworkXIRISLoader.__new__(NetworkXIRISLoader)
        loader.conn = self.conn
        return loader

    def test_tsv_loading(self):
        """Test loading TSV format via API"""
        # Create test TSV file
        tsv_data = """source\tpredicate\ttarget\tconfidence\tevidence
LOADER_PROTEIN_A\tinteracts_with\tLOADER_PROTEIN_B\t0.95\texperimental
LOADER_PROTEIN_B\tinteracts_with\tLOADER_PROTEIN_C\t0.87\tcomputational
LOADER_PROTEIN_A\tregulates\tLOADER_PROTEIN_C\t0.72\tliterature"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.tsv', delete=False) as f:
            f.write(tsv_data)
            tsv_file = f.name

        try:
            loader = self._create_loader()
            G = loader.load_format(tsv_file, format_type='tsv')
            result = loader.import_graph(G, node_type='test_protein', batch_size=1000)
            assert result['success'], f"Import failed: {result.get('error')}"

            # Verify data was loaded
            conn = self.conn
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE label = 'test_protein'")
            entity_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s LIKE 'LOADER_%'")
            edge_count = cursor.fetchone()[0]

            cursor.close()

            assert entity_count == 3  # 3 unique proteins
            assert edge_count == 3    # 3 relationships

        finally:
            os.unlink(tsv_file)

    def test_csv_loading(self):
        """Test loading CSV format via API"""
        # Create test CSV file
        csv_data = """gene1,gene2,interaction_type,score
LOADER_GENE_X,LOADER_GENE_Y,co_expression,0.89
LOADER_GENE_Y,LOADER_GENE_Z,regulatory,0.76
LOADER_GENE_X,LOADER_GENE_Z,protein_interaction,0.93"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_data)
            csv_file = f.name

        try:
            loader = self._create_loader()
            G = loader.load_format(csv_file, format_type='csv', source_col='gene1', target_col='gene2')
            result = loader.import_graph(G, node_type='test_gene')
            assert result['success'], f"Import failed: {result.get('error')}"

            # Verify data was loaded
            conn = self.conn
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE label = 'test_gene'")
            entity_count = cursor.fetchone()[0]

            cursor.close()

            assert entity_count == 3  # 3 unique genes

        finally:
            os.unlink(csv_file)

    def test_jsonl_loading(self):
        """Test loading JSONL format via API"""
        # Create test JSONL file
        jsonl_data = [
            {"source": "LOADER_DRUG_A", "target": "LOADER_TARGET_1", "interaction": "inhibits", "ic50": 0.05},
            {"source": "LOADER_DRUG_B", "target": "LOADER_TARGET_2", "interaction": "activates", "efficacy": 0.87},
            {"source": "LOADER_DRUG_A", "target": "LOADER_TARGET_2", "interaction": "binds", "affinity": 0.23}
        ]

        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            for item in jsonl_data:
                f.write(json.dumps(item) + '\n')
            jsonl_file = f.name

        try:
            loader = self._create_loader()
            G = loader.load_format(jsonl_file, format_type='jsonl')
            result = loader.import_graph(G, node_type='test_entity')
            assert result['success'], f"Import failed: {result.get('error')}"

            # Verify data was loaded
            conn = self.conn
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE label = 'test_entity'")
            entity_count = cursor.fetchone()[0]

            # Check edge attributes
            cursor.execute("""
                SELECT qualifiers FROM Graph_KG.rdf_edges
                WHERE s = 'LOADER_DRUG_A' AND o_id = 'LOADER_TARGET_1'
            """)
            qualifiers_result = cursor.fetchone()

            assert entity_count == 4  # 2 drugs + 2 targets
            assert qualifiers_result is not None

            # Verify qualifiers contain expected attributes
            qualifiers = json.loads(qualifiers_result[0])
            assert 'ic50' in qualifiers
            assert qualifiers['ic50'] == 0.05

            cursor.close()

        finally:
            os.unlink(jsonl_file)

    def test_graphml_loading(self):
        """Test loading GraphML format via API"""
        # Create test NetworkX graph
        G = nx.DiGraph()
        G.add_edge('LOADER_NODE_1', 'LOADER_NODE_2', weight=0.8, type='strong')
        G.add_edge('LOADER_NODE_2', 'LOADER_NODE_3', weight=0.6, type='weak')
        G.add_edge('LOADER_NODE_1', 'LOADER_NODE_3', weight=0.9, type='direct')

        # Add node attributes
        G.nodes['LOADER_NODE_1']['category'] = 'source'
        G.nodes['LOADER_NODE_2']['category'] = 'intermediate'
        G.nodes['LOADER_NODE_3']['category'] = 'target'

        with tempfile.NamedTemporaryFile(suffix='.graphml', delete=False) as f:
            graphml_file = f.name

        try:
            # Write GraphML file
            nx.write_graphml(G, graphml_file)

            loader = self._create_loader()
            graph = loader.load_format(graphml_file, format_type='graphml')
            result = loader.import_graph(graph, node_type='test_node')
            assert result['success'], f"Import failed: {result.get('error')}"

            # Verify data was loaded
            conn = self.conn
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE label = 'test_node'")
            entity_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s LIKE 'LOADER_NODE_%'")
            edge_count = cursor.fetchone()[0]

            # Check node properties
            cursor.execute("""
                SELECT COUNT(*) FROM Graph_KG.rdf_props
                WHERE s LIKE 'LOADER_NODE_%' AND key = 'category'
            """)
            prop_count = cursor.fetchone()[0]

            cursor.close()

            assert entity_count == 3  # 3 nodes
            assert edge_count == 3    # 3 edges
            assert prop_count == 3    # 3 category properties

        finally:
            os.unlink(graphml_file)

    def test_export_functionality(self):
        """Test exporting IRIS graph to file"""
        # First ensure we have some test data
        conn = self.conn
        cursor = conn.cursor()

        # Insert test entities
        test_entities = [
            ('LOADER_EXPORT_A', 'export_test'),
            ('LOADER_EXPORT_B', 'export_test'),
            ('LOADER_EXPORT_C', 'export_test')
        ]
        ensure_nodes_exist(cursor, [node for node, _ in test_entities])
        cursor.executemany(
            "INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, ?)",
            test_entities
        )

        # Insert test edges
        test_edges = [
            ('LOADER_EXPORT_A', 'connects', 'LOADER_EXPORT_B', '{"weight": 0.8}'),
            ('LOADER_EXPORT_B', 'connects', 'LOADER_EXPORT_C', '{"weight": 0.9}')
        ]
        cursor.executemany(
            "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)",
            test_edges
        )

        cursor.close()

        with tempfile.NamedTemporaryFile(suffix='.graphml', delete=False) as f:
            export_file = f.name

        try:
            loader = self._create_loader()
            success = loader.export_graph(export_file, format_type='graphml', node_filter='export_test', limit=10)
            assert success, "Export failed"

            # Verify exported file exists and is valid
            assert os.path.exists(export_file)
            assert os.path.getsize(export_file) > 0

            # Load exported graph with NetworkX to verify format
            G = nx.read_graphml(export_file)
            assert G.number_of_nodes() == 3
            assert G.number_of_edges() == 2

            # Check that exported graph has expected nodes
            expected_nodes = {'LOADER_EXPORT_A', 'LOADER_EXPORT_B', 'LOADER_EXPORT_C'}
            assert set(G.nodes()) == expected_nodes

        finally:
            if os.path.exists(export_file):
                os.unlink(export_file)

    def test_clear_existing_flag(self):
        """Test --clear-existing functionality"""
        # First, create some existing test data
        conn = self.conn
        cursor = conn.cursor()

        ensure_nodes_exist(cursor, ['LOADER_EXISTING'])
        cursor.execute(
            "INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, ?)",
            ['LOADER_EXISTING', 'existing_data']
        )

        cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE label = 'existing_data'")
        initial_count = cursor.fetchone()[0]
        assert initial_count == 1

        cursor.close()

        # Create new test data file
        tsv_data = "source\tpredicate\ttarget\nLOADER_NEW_A\tconnects\tLOADER_NEW_B"

        with tempfile.NamedTemporaryFile(mode='w', suffix='.tsv', delete=False) as f:
            f.write(tsv_data)
            tsv_file = f.name

        try:
            loader = self._create_loader()
            G = loader.load_format(tsv_file, format_type='tsv')
            result = loader.import_graph(G, node_type='new_data', clear_existing=True)
            assert result['success'], f"Import failed: {result.get('error')}"

            # Verify existing data was cleared
            conn = self.conn
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE label = 'existing_data'")
            existing_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE label = 'new_data'")
            new_count = cursor.fetchone()[0]

            cursor.close()

            assert existing_count == 0  # Existing data should be cleared
            assert new_count == 2       # New data should be loaded

        finally:
            os.unlink(tsv_file)

    def test_auto_format_detection(self):
        """Test automatic format detection"""
        # Create TSV file without specifying format
        tsv_data = "source\tpredicate\ttarget\nLOADER_AUTO_A\tconnects\tLOADER_AUTO_B"

        with tempfile.NamedTemporaryFile(mode='w', suffix='.tsv', delete=False) as f:
            f.write(tsv_data)
            tsv_file = f.name

        try:
            loader = self._create_loader()
            graph = loader.load_format(tsv_file)
            result = loader.import_graph(graph, node_type='auto_detected')
            assert result['success'], f"Import failed: {result.get('error')}"

            # Verify data was loaded correctly
            conn = self.conn
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE label = 'auto_detected'")
            count = cursor.fetchone()[0]

            cursor.close()

            assert count == 2  # 2 entities should be loaded

        finally:
            os.unlink(tsv_file)

    def test_error_handling(self):
        """Test error handling for invalid inputs"""
        loader = NetworkXIRISLoader.__new__(NetworkXIRISLoader)

        with pytest.raises(FileNotFoundError):
            loader.load_format('/nonexistent/file.tsv')

        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("invalid data")
            invalid_file = f.name

        try:
            with pytest.raises(ValueError):
                loader.load_format(invalid_file, format_type='unsupported_format')

        finally:
            os.unlink(invalid_file)
