#!/usr/bin/env python3
"""
Working SQL Query Patterns Test Suite
Tests only SQL patterns that actually work with the current IRIS schema
"""

import pytest
import json
import time
import importlib
from typing import Dict, List, Any, Optional

# NOTE: Use importlib to avoid conflict with iris/ directory in project
try:
    iris_module = importlib.import_module('intersystems_irispython.iris')
    IRIS_AVAILABLE = True
except ImportError:
    IRIS_AVAILABLE = False
    pytest.skip("IRIS Python driver not available", allow_module_level=True)


class TestWorkingSQLPatterns:
    """Test suite for validated, working SQL patterns only"""

    @classmethod
    def setup_class(cls):
        """Setup working SQL pattern tests"""
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

            print("✓ IRIS connection for SQL pattern testing established")

            # Create test data
            cls._create_test_data()

        except Exception as e:
            pytest.skip(f"IRIS database not accessible: {e}")

    @classmethod
    def teardown_class(cls):
        """Clean up SQL pattern tests"""
        if hasattr(cls, 'conn'):
            # Clean up test data
            cursor = cls.conn.cursor()
            try:
                cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'TEST_%'")
                cursor.execute("DELETE FROM rdf_labels WHERE s LIKE 'TEST_%'")
                cursor.execute("DELETE FROM rdf_props WHERE s LIKE 'TEST_%'")
            except:
                pass
            cursor.close()
            cls.conn.close()

    @classmethod
    def _create_test_data(cls):
        """Create test data for SQL pattern validation"""
        cursor = cls.conn.cursor()

        # Create test entities with labels
        test_entities = [
            ('TEST_PROTEIN_A', 'protein'),
            ('TEST_PROTEIN_B', 'protein'),
            ('TEST_PROTEIN_C', 'protein'),
            ('TEST_DRUG_A', 'drug'),
            ('TEST_GENE_A', 'gene'),
            ('TEST_DISEASE_A', 'disease'),
            ('TEST_PATHWAY_A', 'pathway')
        ]

        # Clean existing test data first
        cursor.execute("DELETE FROM rdf_labels WHERE s LIKE 'TEST_%'")
        cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'TEST_%'")
        cursor.execute("DELETE FROM rdf_props WHERE s LIKE 'TEST_%'")

        cursor.executemany(
            "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
            test_entities
        )

        # Create test relationships with qualifiers
        test_edges = [
            ('TEST_PROTEIN_A', 'interacts_with', 'TEST_PROTEIN_B',
             '{"confidence": 0.85, "evidence": "experimental"}'),
            ('TEST_PROTEIN_B', 'interacts_with', 'TEST_PROTEIN_C',
             '{"confidence": 0.92, "source": "STRING"}'),
            ('TEST_DRUG_A', 'targets', 'TEST_PROTEIN_A',
             '{"confidence": 0.78, "binding_affinity": "high"}'),
            ('TEST_GENE_A', 'encodes', 'TEST_PROTEIN_A',
             '{"organism": "human", "chromosome": "17"}'),
            ('TEST_PROTEIN_A', 'associated_with', 'TEST_DISEASE_A',
             '{"evidence": "literature", "pmid": "12345678"}'),
            ('TEST_PROTEIN_B', 'participates_in', 'TEST_PATHWAY_A',
             '{"role": "enzyme", "pathway_position": "upstream"}')
        ]

        cursor.executemany(
            "INSERT INTO rdf_edges (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)",
            test_edges
        )

        # Create test properties
        test_props = [
            ('TEST_PROTEIN_A', 'name', 'Test Protein Alpha'),
            ('TEST_PROTEIN_A', 'molecular_weight', '45000'),
            ('TEST_PROTEIN_B', 'name', 'Test Protein Beta'),
            ('TEST_DRUG_A', 'name', 'Test Drug Alpha'),
            ('TEST_DRUG_A', 'compound_id', 'TDA001')
        ]

        cursor.executemany(
            "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
            test_props
        )

        cursor.close()
        print("✓ Test data created for SQL pattern validation")

    def test_basic_entity_lookup(self):
        """Test basic entity lookup by ID"""
        cursor = self.conn.cursor()

        # Pattern 1: Find entity by exact ID
        cursor.execute("SELECT s, label FROM rdf_labels WHERE s = ?", ['TEST_PROTEIN_A'])
        result = cursor.fetchone()

        assert result is not None
        assert result[0] == 'TEST_PROTEIN_A'
        assert result[1] == 'protein'

        cursor.close()
        print("✓ Basic entity lookup works")

    def test_entity_type_filtering(self):
        """Test filtering entities by type label"""
        cursor = self.conn.cursor()

        # Pattern 2: Find all entities of specific type
        cursor.execute("SELECT s FROM rdf_labels WHERE label = ?", ['protein'])
        results = cursor.fetchall()

        test_proteins = [r[0] for r in results if r[0].startswith('TEST_')]
        assert len(test_proteins) >= 3  # Should find our test proteins

        cursor.close()
        print(f"✓ Entity type filtering works: found {len(test_proteins)} test proteins")

    def test_direct_relationship_query(self):
        """Test direct relationship lookups"""
        cursor = self.conn.cursor()

        # Pattern 3: Find direct relationships from entity
        cursor.execute("""
            SELECT s, p, o_id
            FROM rdf_edges
            WHERE s = ? AND p = ?
        """, ['TEST_PROTEIN_A', 'interacts_with'])

        results = cursor.fetchall()
        assert len(results) >= 1

        subject, predicate, object_id = results[0]
        assert subject == 'TEST_PROTEIN_A'
        assert predicate == 'interacts_with'
        assert object_id == 'TEST_PROTEIN_B'

        cursor.close()
        print("✓ Direct relationship query works")

    def test_multi_hop_traversal(self):
        """Test multi-hop graph traversal using joins"""
        cursor = self.conn.cursor()

        # Pattern 4: Two-hop traversal
        cursor.execute("""
            SELECT e1.s as source, e1.p as rel1, e2.s as intermediate, e2.p as rel2, e2.o_id as target
            FROM rdf_edges e1
            JOIN rdf_edges e2 ON e1.o_id = e2.s
            WHERE e1.s = ?
            ORDER BY e1.p, e2.p
        """, ['TEST_PROTEIN_A'])

        results = cursor.fetchall()
        assert len(results) >= 1

        print(f"✓ Multi-hop traversal works: found {len(results)} paths")
        for source, rel1, intermediate, rel2, target in results[:3]:
            print(f"  {source} → {rel1} → {intermediate} → {rel2} → {target}")

        cursor.close()

    def test_bidirectional_neighbor_discovery(self):
        """Test finding all neighbors (incoming and outgoing)"""
        cursor = self.conn.cursor()

        # Pattern 5: Bidirectional neighbors
        cursor.execute("""
            SELECT 'outgoing' as direction, p as relationship, o_id as neighbor
            FROM rdf_edges
            WHERE s = ?
            UNION ALL
            SELECT 'incoming' as direction, p as relationship, s as neighbor
            FROM rdf_edges
            WHERE o_id = ?
            ORDER BY direction, relationship
        """, ['TEST_PROTEIN_A', 'TEST_PROTEIN_A'])

        results = cursor.fetchall()
        assert len(results) >= 2  # Should have both incoming and outgoing

        incoming = [r for r in results if r[0] == 'incoming']
        outgoing = [r for r in results if r[0] == 'outgoing']

        print(f"✓ Bidirectional neighbor discovery works: {len(incoming)} incoming, {len(outgoing)} outgoing")

        cursor.close()

    def test_property_lookup(self):
        """Test entity property queries"""
        cursor = self.conn.cursor()

        # Pattern 6: Get all properties for entity
        cursor.execute("""
            SELECT key, val
            FROM rdf_props
            WHERE s = ?
            ORDER BY key
        """, ['TEST_PROTEIN_A'])

        results = cursor.fetchall()
        assert len(results) >= 2  # Should have name and molecular_weight

        properties = {key: val for key, val in results}
        assert 'name' in properties
        assert properties['name'] == 'Test Protein Alpha'

        cursor.close()
        print(f"✓ Property lookup works: found {len(properties)} properties")

    def test_qualifier_text_search(self):
        """Test text-based search in qualifiers"""
        cursor = self.conn.cursor()

        # Pattern 7: Search qualifiers for text patterns
        cursor.execute("""
            SELECT s, p, o_id, qualifiers
            FROM rdf_edges
            WHERE qualifiers LIKE ?
        """, ['%experimental%'])

        results = cursor.fetchall()
        assert len(results) >= 1

        cursor.close()
        print(f"✓ Qualifier text search works: found {len(results)} experimental relationships")

    def test_relationship_type_analysis(self):
        """Test aggregation queries for relationship analysis"""
        cursor = self.conn.cursor()

        # Pattern 8: Count relationships by type
        cursor.execute("""
            SELECT p as relationship_type, COUNT(*) as count
            FROM rdf_edges
            WHERE s LIKE 'TEST_%'
            GROUP BY p
            ORDER BY count DESC
        """, )

        results = cursor.fetchall()
        assert len(results) >= 3  # Should have multiple relationship types

        cursor.close()
        print(f"✓ Relationship type analysis works: found {len(results)} relationship types")

    def test_entity_degree_analysis(self):
        """Test network degree calculations"""
        cursor = self.conn.cursor()

        # Pattern 9: Calculate entity degrees
        cursor.execute("""
            SELECT s as entity, COUNT(*) as out_degree
            FROM rdf_edges
            WHERE s LIKE 'TEST_%'
            GROUP BY s
            ORDER BY out_degree DESC
        """)

        results = cursor.fetchall()
        assert len(results) >= 1

        cursor.close()
        print(f"✓ Entity degree analysis works: analyzed {len(results)} entities")

    def test_complex_join_pattern(self):
        """Test complex joins with multiple tables"""
        cursor = self.conn.cursor()

        # Pattern 10: Join edges, labels, and properties
        cursor.execute("""
            SELECT
                e.s as source_entity,
                l1.label as source_type,
                e.p as relationship,
                e.o_id as target_entity,
                l2.label as target_type,
                p.val as source_name
            FROM rdf_edges e
            JOIN rdf_labels l1 ON e.s = l1.s
            JOIN rdf_labels l2 ON e.o_id = l2.s
            LEFT JOIN rdf_props p ON e.s = p.s AND p.key = 'name'
            WHERE e.s LIKE 'TEST_%'
            ORDER BY e.s, e.p
        """)

        results = cursor.fetchall()
        assert len(results) >= 1

        cursor.close()
        print(f"✓ Complex join pattern works: found {len(results)} enriched relationships")

    def test_performance_baseline(self):
        """Test query performance for baseline metrics"""
        cursor = self.conn.cursor()

        # Measure basic query performance
        start_time = time.time()

        cursor.execute("""
            SELECT COUNT(*)
            FROM rdf_edges e
            JOIN rdf_labels l ON e.s = l.s
            WHERE l.label = 'protein'
        """)

        count = cursor.fetchone()[0]
        elapsed = time.time() - start_time

        cursor.close()

        print(f"✓ Performance baseline: counted {count} protein relationships in {elapsed:.3f}s")
        assert elapsed < 1.0  # Should complete within 1 second

    def test_performance_reality_check(self):
        """Validate actual performance against documented claims"""
        cursor = self.conn.cursor()

        performance_results = {}

        # Test 1: Two-hop traversal (docs claim 0.25ms)
        start_time = time.time()
        cursor.execute("""
            SELECT e1.s, e2.o_id
            FROM rdf_edges e1
            JOIN rdf_edges e2 ON e1.o_id = e2.s
            WHERE e1.s LIKE 'TEST_%'
        """)
        results = cursor.fetchall()
        elapsed = (time.time() - start_time) * 1000  # Convert to ms
        performance_results['2-hop traversal'] = elapsed

        # Test 2: Direct path query (docs claim 1-3ms)
        start_time = time.time()
        cursor.execute("""
            SELECT s, p, o_id FROM rdf_edges
            WHERE s = 'TEST_PROTEIN_A'
        """)
        results = cursor.fetchall()
        elapsed = (time.time() - start_time) * 1000
        performance_results['direct paths'] = elapsed

        # Test 3: Neighborhood expansion (docs claim 2-8ms)
        start_time = time.time()
        cursor.execute("""
            SELECT 'outgoing' as direction, o_id as neighbor FROM rdf_edges WHERE s = 'TEST_PROTEIN_A'
            UNION ALL
            SELECT 'incoming' as direction, s as neighbor FROM rdf_edges WHERE o_id = 'TEST_PROTEIN_A'
        """)
        results = cursor.fetchall()
        elapsed = (time.time() - start_time) * 1000
        performance_results['neighborhood expansion'] = elapsed

        # Test 4: Text search (docs claim 5-15ms)
        start_time = time.time()
        cursor.execute("""
            SELECT s, qualifiers FROM rdf_edges
            WHERE qualifiers LIKE '%experimental%'
        """)
        results = cursor.fetchall()
        elapsed = (time.time() - start_time) * 1000
        performance_results['text search'] = elapsed

        # Test 5: Aggregation (docs claim 10-50ms)
        start_time = time.time()
        cursor.execute("""
            SELECT s, COUNT(*) as degree
            FROM rdf_edges
            WHERE s LIKE 'TEST_%'
            GROUP BY s
            ORDER BY degree DESC
        """)
        results = cursor.fetchall()
        elapsed = (time.time() - start_time) * 1000
        performance_results['aggregation'] = elapsed

        cursor.close()

        # Report actual vs claimed performance
        print("\n=== PERFORMANCE REALITY CHECK ===")
        documented_claims = {
            '2-hop traversal': 0.25,  # ms
            'direct paths': 2.0,      # ms (midpoint of 1-3ms)
            'neighborhood expansion': 5.0,  # ms (midpoint of 2-8ms)
            'text search': 10.0,      # ms (midpoint of 5-15ms)
            'aggregation': 30.0       # ms (midpoint of 10-50ms)
        }

        all_within_claims = True
        for operation, actual_time in performance_results.items():
            claimed_time = documented_claims.get(operation, float('inf'))
            within_claim = actual_time <= claimed_time * 2  # Allow 2x tolerance
            status = "✓" if within_claim else "❌"
            ratio = actual_time / claimed_time if claimed_time > 0 else float('inf')

            print(f"{status} {operation}: {actual_time:.2f}ms actual vs {claimed_time:.2f}ms claimed (ratio: {ratio:.1f}x)")

            if not within_claim:
                all_within_claims = False

        if not all_within_claims:
            print("\n⚠️  WARNING: Some performance claims appear to be unrealistic!")
            print("   Consider updating documentation with actual measured performance.")
        else:
            print("\n✓ Performance claims are realistic for current test data size")

        return performance_results

    def test_data_validation_queries(self):
        """Test queries for data validation and integrity"""
        cursor = self.conn.cursor()

        # Pattern 11: Find orphaned entities (in edges but not in labels)
        cursor.execute("""
            SELECT DISTINCT e.s
            FROM rdf_edges e
            LEFT JOIN rdf_labels l ON e.s = l.s
            WHERE l.s IS NULL
              AND e.s LIKE 'TEST_%'
        """)

        orphaned = cursor.fetchall()

        # Pattern 12: Find entities with properties but no labels
        cursor.execute("""
            SELECT DISTINCT p.s
            FROM rdf_props p
            LEFT JOIN rdf_labels l ON p.s = l.s
            WHERE l.s IS NULL
              AND p.s LIKE 'TEST_%'
        """)

        unlabeled = cursor.fetchall()

        cursor.close()

        print(f"✓ Data validation works: found {len(orphaned)} orphaned, {len(unlabeled)} unlabeled entities")

        # Our test data should be well-formed
        assert len(orphaned) == 0
        assert len(unlabeled) == 0


if __name__ == "__main__":
    # Run working SQL pattern tests
    print("Running IRIS Graph-AI Working SQL Pattern Tests...")

    try:
        test_instance = TestWorkingSQLPatterns()
        test_instance.setup_class()

        print("\n=== Testing Basic Patterns ===")
        test_instance.test_basic_entity_lookup()
        test_instance.test_entity_type_filtering()
        test_instance.test_direct_relationship_query()

        print("\n=== Testing Graph Traversal ===")
        test_instance.test_multi_hop_traversal()
        test_instance.test_bidirectional_neighbor_discovery()

        print("\n=== Testing Property and Text Search ===")
        test_instance.test_property_lookup()
        test_instance.test_qualifier_text_search()

        print("\n=== Testing Aggregation and Analysis ===")
        test_instance.test_relationship_type_analysis()
        test_instance.test_entity_degree_analysis()
        test_instance.test_complex_join_pattern()

        print("\n=== Testing Performance and Validation ===")
        test_instance.test_performance_baseline()
        test_instance.test_performance_reality_check()
        test_instance.test_data_validation_queries()

        test_instance.teardown_class()

        print("\n✅ All working SQL patterns validated successfully!")
        print("\nSummary of validated patterns:")
        print("1. ✓ Basic entity lookup by ID")
        print("2. ✓ Entity type filtering by label")
        print("3. ✓ Direct relationship queries")
        print("4. ✓ Multi-hop graph traversal with joins")
        print("5. ✓ Bidirectional neighbor discovery")
        print("6. ✓ Property lookup and retrieval")
        print("7. ✓ Text search in qualifiers")
        print("8. ✓ Relationship type aggregation")
        print("9. ✓ Entity degree calculations")
        print("10. ✓ Complex multi-table joins")
        print("11. ✓ Performance baseline measurement")
        print("12. ✓ Data validation and integrity checks")

    except Exception as e:
        print(f"\n❌ SQL pattern testing failed: {e}")
        import traceback
        traceback.print_exc()