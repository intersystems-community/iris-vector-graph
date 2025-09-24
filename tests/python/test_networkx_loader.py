#!/usr/bin/env python3
"""
Test suite for NetworkX loader CLI tool
Tests all format support and CLI functionality
"""

import pytest
import tempfile
import json
import os
import subprocess
import sys
from pathlib import Path
import pandas as pd

try:
    import networkx as nx
    NETWORKX_AVAILABLE = True
except ImportError:
    NETWORKX_AVAILABLE = False
    pytest.skip("NetworkX not available", allow_module_level=True)

try:
    import iris
    IRIS_AVAILABLE = True
except ImportError:
    IRIS_AVAILABLE = False
    pytest.skip("IRIS Python driver not available", allow_module_level=True)


class TestNetworkXLoader:
    """Test NetworkX loader CLI tool"""

    @classmethod
    def setup_class(cls):
        """Setup test class"""
        cls.loader_script = Path(__file__).parent.parent.parent / "scripts" / "ingest" / "networkx_loader.py"
        assert cls.loader_script.exists(), f"NetworkX loader script not found: {cls.loader_script}"

        # Test IRIS connection
        try:
            cls.conn = iris.connect(
                hostname='localhost',
                port=1973,
                namespace='USER',
                username='_SYSTEM',
                password='SYS'
            )
            cls.conn.close()
        except Exception as e:
            pytest.skip(f"IRIS database not accessible: {e}")

    @classmethod
    def teardown_class(cls):
        """Clean up test data"""
        try:
            conn = iris.connect(
                hostname='localhost',
                port=1973,
                namespace='USER',
                username='_SYSTEM',
                password='SYS'
            )
            cursor = conn.cursor()
            cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'LOADER_%'")
            cursor.execute("DELETE FROM rdf_labels WHERE s LIKE 'LOADER_%'")
            cursor.execute("DELETE FROM rdf_props WHERE s LIKE 'LOADER_%'")
            cursor.close()
            conn.close()
        except Exception:
            pass

    def test_tsv_loading(self):
        """Test loading TSV format via CLI"""
        # Create test TSV file
        tsv_data = """source\tpredicate\ttarget\tconfidence\tevidence
LOADER_PROTEIN_A\tinteracts_with\tLOADER_PROTEIN_B\t0.95\texperimental
LOADER_PROTEIN_B\tinteracts_with\tLOADER_PROTEIN_C\t0.87\tcomputational
LOADER_PROTEIN_A\tregulates\tLOADER_PROTEIN_C\t0.72\tliterature"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.tsv', delete=False) as f:
            f.write(tsv_data)
            tsv_file = f.name

        try:
            # Run loader CLI
            cmd = [
                sys.executable, str(self.loader_script),
                'load', tsv_file,
                '--format', 'tsv',
                '--node-type', 'test_protein',
                '--batch-size', '1000'
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            if result.returncode != 0:
                print(f"STDOUT: {result.stdout}")
                print(f"STDERR: {result.stderr}")

            assert result.returncode == 0, f"CLI command failed: {result.stderr}"

            # Verify data was loaded
            conn = iris.connect(
                hostname='localhost',
                port=1973,
                namespace='USER',
                username='_SYSTEM',
                password='SYS'
            )
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label = 'test_protein'")
            entity_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM rdf_edges WHERE s LIKE 'LOADER_%'")
            edge_count = cursor.fetchone()[0]

            cursor.close()
            conn.close()

            assert entity_count == 3  # 3 unique proteins
            assert edge_count == 3    # 3 relationships

        finally:
            os.unlink(tsv_file)

    def test_csv_loading(self):
        """Test loading CSV format via CLI"""
        # Create test CSV file
        csv_data = """gene1,gene2,interaction_type,score
LOADER_GENE_X,LOADER_GENE_Y,co_expression,0.89
LOADER_GENE_Y,LOADER_GENE_Z,regulatory,0.76
LOADER_GENE_X,LOADER_GENE_Z,protein_interaction,0.93"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(csv_data)
            csv_file = f.name

        try:
            # Run loader CLI with custom column mapping
            cmd = [
                sys.executable, str(self.loader_script),
                'load', csv_file,
                '--format', 'csv',
                '--source-col', 'gene1',
                '--target-col', 'gene2',
                '--node-type', 'test_gene'
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            assert result.returncode == 0, f"CLI command failed: {result.stderr}"

            # Verify data was loaded
            conn = iris.connect(
                hostname='localhost',
                port=1973,
                namespace='USER',
                username='_SYSTEM',
                password='SYS'
            )
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label = 'test_gene'")
            entity_count = cursor.fetchone()[0]

            cursor.close()
            conn.close()

            assert entity_count == 3  # 3 unique genes

        finally:
            os.unlink(csv_file)

    def test_jsonl_loading(self):
        """Test loading JSONL format via CLI"""
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
            # Run loader CLI
            cmd = [
                sys.executable, str(self.loader_script),
                'load', jsonl_file,
                '--format', 'jsonl',
                '--node-type', 'test_entity'
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            assert result.returncode == 0, f"CLI command failed: {result.stderr}"

            # Verify data was loaded
            conn = iris.connect(
                hostname='localhost',
                port=1973,
                namespace='USER',
                username='_SYSTEM',
                password='SYS'
            )
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label = 'test_entity'")
            entity_count = cursor.fetchone()[0]

            # Check edge attributes
            cursor.execute("""
                SELECT qualifiers FROM rdf_edges
                WHERE s = 'LOADER_DRUG_A' AND o_id = 'LOADER_TARGET_1'
            """)
            qualifiers_result = cursor.fetchone()

            cursor.close()
            conn.close()

            assert entity_count == 4  # 2 drugs + 2 targets
            assert qualifiers_result is not None

            # Verify qualifiers contain expected attributes
            qualifiers = json.loads(qualifiers_result[0])
            assert 'ic50' in qualifiers
            assert qualifiers['ic50'] == 0.05

        finally:
            os.unlink(jsonl_file)

    def test_graphml_loading(self):
        """Test loading GraphML format via CLI"""
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

            # Run loader CLI
            cmd = [
                sys.executable, str(self.loader_script),
                'load', graphml_file,
                '--format', 'graphml',
                '--node-type', 'test_node'
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            assert result.returncode == 0, f"CLI command failed: {result.stderr}"

            # Verify data was loaded
            conn = iris.connect(
                hostname='localhost',
                port=1973,
                namespace='USER',
                username='_SYSTEM',
                password='SYS'
            )
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label = 'test_node'")
            entity_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM rdf_edges WHERE s LIKE 'LOADER_NODE_%'")
            edge_count = cursor.fetchone()[0]

            # Check node properties
            cursor.execute("""
                SELECT COUNT(*) FROM rdf_props
                WHERE s LIKE 'LOADER_NODE_%' AND key = 'category'
            """)
            prop_count = cursor.fetchone()[0]

            cursor.close()
            conn.close()

            assert entity_count == 3  # 3 nodes
            assert edge_count == 3    # 3 edges
            assert prop_count == 3    # 3 category properties

        finally:
            os.unlink(graphml_file)

    def test_export_functionality(self):
        """Test exporting IRIS graph to file"""
        # First ensure we have some test data
        conn = iris.connect(
            hostname='localhost',
            port=1973,
            namespace='USER',
            username='_SYSTEM',
            password='SYS'
        )
        cursor = conn.cursor()

        # Insert test entities
        test_entities = [
            ('LOADER_EXPORT_A', 'export_test'),
            ('LOADER_EXPORT_B', 'export_test'),
            ('LOADER_EXPORT_C', 'export_test')
        ]
        cursor.executemany(
            "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
            test_entities
        )

        # Insert test edges
        test_edges = [
            ('LOADER_EXPORT_A', 'connects', 'LOADER_EXPORT_B', '{"weight": 0.8}'),
            ('LOADER_EXPORT_B', 'connects', 'LOADER_EXPORT_C', '{"weight": 0.9}')
        ]
        cursor.executemany(
            "INSERT INTO rdf_edges (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)",
            test_edges
        )

        cursor.close()
        conn.close()

        with tempfile.NamedTemporaryFile(suffix='.graphml', delete=False) as f:
            export_file = f.name

        try:
            # Run export CLI
            cmd = [
                sys.executable, str(self.loader_script),
                'export', export_file,
                '--format', 'graphml',
                '--node-filter', 'export_test',
                '--limit', '10'
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            assert result.returncode == 0, f"Export command failed: {result.stderr}"

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
        conn = iris.connect(
            hostname='localhost',
            port=1973,
            namespace='USER',
            username='_SYSTEM',
            password='SYS'
        )
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
            ['LOADER_EXISTING', 'existing_data']
        )

        cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label = 'existing_data'")
        initial_count = cursor.fetchone()[0]
        assert initial_count == 1

        cursor.close()
        conn.close()

        # Create new test data file
        tsv_data = "source\ttarget\nLOADER_NEW_A\tLOADER_NEW_B"

        with tempfile.NamedTemporaryFile(mode='w', suffix='.tsv', delete=False) as f:
            f.write(tsv_data)
            tsv_file = f.name

        try:
            # Run loader with --clear-existing
            cmd = [
                sys.executable, str(self.loader_script),
                'load', tsv_file,
                '--format', 'tsv',
                '--node-type', 'new_data',
                '--clear-existing'
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            assert result.returncode == 0, f"CLI command failed: {result.stderr}"

            # Verify existing data was cleared
            conn = iris.connect(
                hostname='localhost',
                port=1973,
                namespace='USER',
                username='_SYSTEM',
                password='SYS'
            )
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label = 'existing_data'")
            existing_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label = 'new_data'")
            new_count = cursor.fetchone()[0]

            cursor.close()
            conn.close()

            assert existing_count == 0  # Existing data should be cleared
            assert new_count == 2       # New data should be loaded

        finally:
            os.unlink(tsv_file)

    def test_auto_format_detection(self):
        """Test automatic format detection"""
        # Create TSV file without specifying format
        tsv_data = "source\ttarget\nLOADER_AUTO_A\tLOADER_AUTO_B"

        with tempfile.NamedTemporaryFile(mode='w', suffix='.tsv', delete=False) as f:
            f.write(tsv_data)
            tsv_file = f.name

        try:
            # Run loader without --format flag (should auto-detect)
            cmd = [
                sys.executable, str(self.loader_script),
                'load', tsv_file,
                '--node-type', 'auto_detected'
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            assert result.returncode == 0, f"Auto-detection failed: {result.stderr}"

            # Verify data was loaded correctly
            conn = iris.connect(
                hostname='localhost',
                port=1973,
                namespace='USER',
                username='_SYSTEM',
                password='SYS'
            )
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label = 'auto_detected'")
            count = cursor.fetchone()[0]

            cursor.close()
            conn.close()

            assert count == 2  # 2 entities should be loaded

        finally:
            os.unlink(tsv_file)

    def test_error_handling(self):
        """Test error handling for invalid inputs"""
        # Test with non-existent file
        cmd = [
            sys.executable, str(self.loader_script),
            'load', '/nonexistent/file.tsv'
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert result.returncode != 0, "Should fail with non-existent file"

        # Test with invalid format
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("invalid data")
            invalid_file = f.name

        try:
            cmd = [
                sys.executable, str(self.loader_script),
                'load', invalid_file,
                '--format', 'unsupported_format'
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            assert result.returncode != 0, "Should fail with unsupported format"

        finally:
            os.unlink(invalid_file)

    def test_help_output(self):
        """Test CLI help output"""
        cmd = [sys.executable, str(self.loader_script), '--help']

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, "Help command should succeed"
        assert 'networkx' in result.stdout.lower(), "Help should mention NetworkX"
        assert 'load' in result.stdout.lower(), "Help should mention load command"
        assert 'export' in result.stdout.lower(), "Help should mention export command"


if __name__ == "__main__":
    # Run specific tests for quick validation
    print("Running NetworkX Loader CLI Tests...")

    try:
        test_loader = TestNetworkXLoader()
        test_loader.setup_class()

        print("Testing TSV loading...")
        test_loader.test_tsv_loading()
        print("✅ TSV loading test passed")

        print("Testing CSV loading...")
        test_loader.test_csv_loading()
        print("✅ CSV loading test passed")

        print("Testing auto-detection...")
        test_loader.test_auto_format_detection()
        print("✅ Auto-detection test passed")

        test_loader.teardown_class()

        print("✅ All NetworkX loader tests passed")

    except Exception as e:
        print(f"❌ NetworkX loader tests failed: {e}")
        import traceback
        traceback.print_exc()

    print("Test suite completed")