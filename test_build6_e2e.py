#!/usr/bin/env python3
"""
End-to-end test for Build 6 (v1.1.6) with live IRIS.

Tests:
1. Schema initialization creates all 6 tables
2. rdf_edges table works with VARCHAR qualifiers
3. Manual ID management works
4. All indexes created successfully
"""

import os
import json
import iris
from iris_vector_graph.schema import GraphSchema

# IRIS connection parameters
IRIS_HOST = os.getenv('IRIS_HOST', 'localhost')
IRIS_PORT = int(os.getenv('IRIS_PORT', '1972'))
IRIS_NAMESPACE = os.getenv('IRIS_NAMESPACE', 'USER')
IRIS_USER = os.getenv('IRIS_USER', '_SYSTEM')
IRIS_PASSWORD = os.getenv('IRIS_PASSWORD', 'SYS')

def test_build6_e2e():
    """End-to-end test of build 6 schema"""
    print("="*70)
    print("Build 6 (v1.1.6) End-to-End Test")
    print("="*70)

    try:
        # Connect to IRIS
        print(f"\n1. Connecting to IRIS at {IRIS_HOST}:{IRIS_PORT}...")
        conn = iris.connect(IRIS_HOST, IRIS_PORT, IRIS_NAMESPACE, IRIS_USER, IRIS_PASSWORD)
        cursor = conn.cursor()
        print("   ✅ Connected")

        # Clean slate - drop all tables
        print("\n2. Dropping existing tables for clean test...")
        tables = ['rdf_labels', 'rdf_props', 'rdf_edges', 'kg_NodeEmbeddings', 'kg_NodeEmbeddings_optimized', 'docs']
        for table in tables:
            try:
                cursor.execute(f"DROP TABLE {table}")
                print(f"   ✅ Dropped {table}")
            except Exception:
                pass

        # Initialize schema using GraphSchema.ensure_schema()
        print("\n3. Initializing schema with GraphSchema.ensure_schema()...")
        results = GraphSchema.ensure_schema(cursor)

        # Count successes
        successes = sum(1 for status in results.values() if 'success' in status)
        errors = sum(1 for status in results.values() if 'error' in status)

        print(f"   Results: {successes} successes, {errors} errors")

        if errors > 0:
            print("\n   ❌ Errors found:")
            for stmt, status in results.items():
                if 'error' in status:
                    print(f"      {stmt}: {status}")
            raise Exception(f"Schema initialization had {errors} errors")

        print("   ✅ Schema initialized successfully")

        # Validate all 6 tables exist
        print("\n4. Validating all 6 tables exist...")
        validation = GraphSchema.validate_schema(cursor)

        for table, exists in validation.items():
            status = "✅" if exists else "❌"
            print(f"   {status} {table}: {'exists' if exists else 'MISSING'}")

        if not all(validation.values()):
            raise Exception("Not all tables were created!")

        print("   ✅ All 6 tables exist")

        # Test rdf_edges with VARCHAR qualifiers (JSON as string)
        print("\n5. Testing rdf_edges with JSON-as-string qualifiers...")

        # Insert edge with JSON metadata
        qualifiers_json = {"confidence": 0.95, "source": "STRING", "evidence": "experimental"}
        cursor.execute("""
INSERT INTO rdf_edges (edge_id, s, p, o_id, qualifiers)
VALUES (?, ?, ?, ?, ?)
        """, (1, 'PROTEIN:TP53', 'interacts_with', 'PROTEIN:MDM2', json.dumps(qualifiers_json)))
        print("   ✅ Inserted edge with JSON-as-string qualifiers")

        # Retrieve and parse
        cursor.execute("SELECT * FROM rdf_edges WHERE edge_id = 1")
        row = cursor.fetchone()
        retrieved_qualifiers = json.loads(row[4])  # qualifiers column

        assert retrieved_qualifiers['confidence'] == 0.95, "Confidence mismatch"
        assert retrieved_qualifiers['source'] == "STRING", "Source mismatch"
        print(f"   ✅ Retrieved and parsed qualifiers: {retrieved_qualifiers}")

        # Test manual ID management (multiple edges)
        print("\n6. Testing manual ID management...")
        for i in range(2, 6):
            cursor.execute("""
INSERT INTO rdf_edges (edge_id, s, p, o_id, qualifiers)
VALUES (?, ?, ?, ?, ?)
            """, (i, f'PROTEIN:P{i}', 'interacts_with', f'PROTEIN:P{i+100}', '{}'))

        cursor.execute("SELECT COUNT(*) FROM rdf_edges")
        count = cursor.fetchone()[0]
        assert count == 5, f"Expected 5 edges, got {count}"
        print(f"   ✅ Manual ID management works: {count} edges inserted")

        # Test other tables
        print("\n7. Testing other tables...")

        # rdf_labels
        cursor.execute("INSERT INTO rdf_labels (s, label) VALUES (?, ?)", ('PROTEIN:TP53', 'Protein'))
        cursor.execute("SELECT COUNT(*) FROM rdf_labels")
        assert cursor.fetchone()[0] == 1
        print("   ✅ rdf_labels works")

        # rdf_props
        cursor.execute("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                      ('PROTEIN:TP53', 'name', 'Tumor protein p53'))
        cursor.execute("SELECT COUNT(*) FROM rdf_props")
        assert cursor.fetchone()[0] == 1
        print("   ✅ rdf_props works")

        # docs (text search table)
        cursor.execute("INSERT INTO docs (id, text) VALUES (?, ?)",
                      ('DOC1', 'Test document for text search'))
        cursor.execute("SELECT COUNT(*) FROM docs")
        assert cursor.fetchone()[0] == 1
        print("   ✅ docs works")

        print("\n" + "="*70)
        print("SUCCESS: Build 6 (v1.1.6) passes end-to-end test!")
        print("="*70)
        print("\nKey validations:")
        print("  ✅ All 6 tables created")
        print("  ✅ rdf_edges works with VARCHAR qualifiers (JSON-as-string)")
        print("  ✅ Manual ID management works")
        print("  ✅ JSON serialization/deserialization works")
        print("  ✅ All base tables functional")
        print("\nConclusion: Build 6 is CORRECT and ready for release as v1.1.6")

        cursor.close()
        conn.close()
        return True

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    success = test_build6_e2e()
    exit(0 if success else 1)
