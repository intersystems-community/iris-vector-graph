#!/usr/bin/env python3
"""
Test and benchmark ObjectScript + Globals PPR implementation.
"""

import time
import iris
import sys


def load_pagerank_class(conn):
    """Load PageRank.cls into IRIS."""
    cursor = conn.cursor()

    # Read the class file
    with open('iris/src/Graph/KG/PageRank.cls', 'r') as f:
        class_code = f.read()

    # Write to temp file in container
    # We'll use a different approach - direct ObjectScript execution
    try:
        # Try to call BuildPPRGraph - if it works, class is loaded
        cursor.execute("SELECT Graph.KG.PageRank_BuildPPRGraph()")
        print("✅ PageRank class already loaded")
        return True
    except Exception as e:
        print(f"⚠️ PageRank class not loaded: {e}")
        print("Loading class via embedded Python...")

        # Use embedded Python to load the class
        try:
            cursor.execute("""
                SELECT %SYSTEM.Python.Run('
import iris
result = iris.cls("%%SYSTEM.OBJ").Load("/tmp/PageRank.cls", "ck")
print(f"Load result: {result}")
')
            """)
            print("✅ Class loaded successfully")
            return True
        except Exception as load_error:
            print(f"❌ Failed to load class: {load_error}")
            return False


def build_ppr_graph(conn):
    """Build ^PPR Global structure."""
    cursor = conn.cursor()

    print("\n📊 Building ^PPR Global structure...")
    start = time.time()

    try:
        cursor.execute("SELECT Graph.KG.PageRank_BuildPPRGraph()")
        elapsed = time.time() - start
        print(f"✅ ^PPR Global built in {elapsed:.2f}s")
        return True
    except Exception as e:
        print(f"❌ Failed to build ^PPR Global: {e}")
        return False


def test_globals_ppr(conn, seed_entity):
    """Test PPR using Globals implementation."""
    cursor = conn.cursor()

    print(f"\n🔍 Computing PPR with seed: {seed_entity}")

    # Create DynamicArray for seeds
    try:
        # Call ComputePPR method
        start = time.time()

        # Build the SQL call
        sql = """
            SELECT Graph.KG.PageRank_ComputePPR(
                ?,
                0.85,
                50,
                0.000001
            )
        """

        # Create JSON array for seeds
        import json
        seeds_json = json.dumps([seed_entity])

        cursor.execute(sql, [seeds_json])
        result = cursor.fetchone()

        elapsed = time.time() - start

        if result:
            print(f"✅ PPR completed in {elapsed*1000:.2f}ms")

            # Parse result (should be DynamicObject with scores)
            result_obj = result[0]
            print(f"   Result type: {type(result_obj)}")
            print(f"   Result: {result_obj}")

            return {
                'time_ms': elapsed * 1000,
                'result': result_obj
            }
        else:
            print(f"❌ No result returned")
            return None

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return None


def create_test_graph(conn, num_nodes=1000):
    """Create small test graph."""
    cursor = conn.cursor()

    print(f"\n📝 Creating test graph ({num_nodes} nodes)...")

    # Clear existing
    cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'PERF:%'")
    cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'PERF:%'")

    # Create nodes
    node_ids = [f"PERF:N{i:06d}" for i in range(num_nodes)]
    for node_id in node_ids:
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", [node_id])

    # Create edges
    import random
    random.seed(42)

    edge_count = 0
    for i, source_id in enumerate(node_ids):
        num_edges = random.randint(3, 7)
        for _ in range(num_edges):
            target_idx = random.randint(0, num_nodes - 1)
            if target_idx != i:
                cursor.execute(
                    "INSERT INTO rdf_edges (s, p, o_id) VALUES (?, 'links_to', ?)",
                    [source_id, node_ids[target_idx]]
                )
                edge_count += 1

    conn.commit()
    print(f"✅ Created {num_nodes} nodes with {edge_count} edges")

    return node_ids


def main():
    """Main benchmark."""
    print("="*80)
    print("OBJECTSCRIPT + GLOBALS PPR BENCHMARK")
    print("="*80)

    # Connect to IRIS
    conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")

    # Create test graph
    node_ids = create_test_graph(conn, num_nodes=1000)
    seed = node_ids[0]

    # Load PageRank class
    if not load_pagerank_class(conn):
        print("\n❌ Cannot proceed without PageRank class loaded")
        print("\nTo manually load the class:")
        print("1. docker cp iris/src/Graph/KG/PageRank.cls iris-pgwire-db:/tmp/")
        print("2. docker exec -it iris-pgwire-db /usr/irissys/bin/irissession IRIS -U USER")
        print("3. In IRIS: do ##class(%SYSTEM.OBJ).Load(\"/tmp/PageRank.cls\", \"ck\")")
        sys.exit(1)

    # Build ^PPR Global
    if not build_ppr_graph(conn):
        print("\n❌ Failed to build ^PPR Global")
        sys.exit(1)

    # Test PPR
    result = test_globals_ppr(conn, seed)

    if result:
        print(f"\n✅ SUCCESS!")
        print(f"   Execution time: {result['time_ms']:.2f}ms")

    # Cleanup
    cursor = conn.cursor()
    cursor.execute("DELETE FROM nodes WHERE node_id LIKE 'PERF:%'")
    cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'PERF:%'")
    cursor.execute("KILL ^PPR")  # Clean up Global
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
