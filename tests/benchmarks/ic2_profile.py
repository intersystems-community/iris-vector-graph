"""
IC2 1-hop profiling script.
Decomposes the 1-hop neighbor query into isolated components to find
where the 15x gap vs GES comes from.
"""
import iris, time, json, statistics, sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

PORT = int(os.environ.get("IRIS_PORT", "4972"))
conn = iris.connect(hostname="localhost", port=PORT, namespace="USER",
                    username="_SYSTEM", password="SYS")
iris_obj = iris.createIRIS(conn)
cur = conn.cursor()


def timeit(fn, warmup=5, runs=20):
    for _ in range(warmup):
        fn()
    ts = []
    result = None
    for _ in range(runs):
        t0 = time.perf_counter()
        result = fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts), min(ts), max(ts), result


# --- Diagnose data state ---
cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes")
n_nodes = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE p = 'KNOWS'")
n_knows = cur.fetchone()[0]
print(f"IRIS port {PORT}: {n_nodes} nodes, {n_knows} KNOWS edges")

# Check ^KG state via ObjectScript
raw_kg = iris_obj.classMethodString("Graph.KG.Traversal", "BFSFastJson",
                                    "P_28587302384882", "KNOWS", 1)
bfs_results = json.loads(str(raw_kg)) if raw_kg else []
print(f"BFSFastJson(P_28587302384882, KNOWS, 1) -> {len(bfs_results)} results")

# if BFS is empty but SQL has data, ^KG is stale
if len(bfs_results) == 0 and n_knows > 0:
    print("WARNING: ^KG appears stale vs rdf_edges. Checking ^KG directly...")
    raw_kg_all = iris_obj.classMethodString("Graph.KG.Traversal", "BFSFastJson",
                                            "P_28587302384882", "", 1)
    bfs_all = json.loads(str(raw_kg_all)) if raw_kg_all else []
    print(f"BFSFastJson(P_28587302384882, '', 1) -> {len(bfs_all)} results (all predicates)")

# Pick seed with confirmed ^KG data
# Find a seed that actually has ^KG entries
result_code = iris_obj.classMethodString("Graph.KG.Traversal", "BFSFastJson",
                                         "P_28587302384882", "", 1)
bfs_check = json.loads(str(result_code)) if result_code else []
if bfs_check:
    SEED = "P_28587302384882"
    SEED_DEGREE = len(bfs_check)
    print(f"\nUsing seed {SEED} (^KG degree={SEED_DEGREE})")
else:
    # Try to find any node that has ^KG entries
    print("\nSearching for node with ^KG data...")
    cur.execute("SELECT TOP 5 s, COUNT(*) AS cnt FROM Graph_KG.rdf_edges GROUP BY s ORDER BY cnt DESC")
    for row in cur.fetchall():
        test_raw = iris_obj.classMethodString("Graph.KG.Traversal", "BFSFastJson", row[0], "", 1)
        test_r = json.loads(str(test_raw)) if test_raw else []
        if test_r:
            SEED = row[0]
            SEED_DEGREE = len(test_r)
            print(f"Found: {SEED} (^KG degree={SEED_DEGREE})")
            break
    else:
        print("ERROR: No nodes found with ^KG data. Run BuildKG first.")
        sys.exit(1)

print(f"\n{'='*60}")
print(f"IC2 1-hop profiling  seed={SEED}  degree={SEED_DEGREE}")
print(f"{'='*60}\n")

from iris_vector_graph.engine import IRISGraphEngine
engine = IRISGraphEngine(conn)


# 1. Raw ObjectScript BFSFastJson — pure IRIS, 1 round-trip
def bfs_raw_all():
    raw = iris_obj.classMethodString("Graph.KG.Traversal", "BFSFastJson", SEED, "", 1)
    return json.loads(str(raw))

p50, mn, mx, r = timeit(bfs_raw_all)
print(f"1. BFSFastJson(seed, '', 1)  [1 round-trip, ObjectScript $Order]")
print(f"   p50={p50:.3f}ms  min={mn:.3f}ms  max={mx:.3f}ms  results={len(r)}")

# 2. BFSFastJson with predicate filter
def bfs_raw_pred():
    raw = iris_obj.classMethodString("Graph.KG.Traversal", "BFSFastJson", SEED, "KNOWS", 1)
    return json.loads(str(raw))

p50, mn, mx, r = timeit(bfs_raw_pred)
print(f"\n2. BFSFastJson(seed, 'KNOWS', 1)  [filtered by predicate]")
print(f"   p50={p50:.3f}ms  min={mn:.3f}ms  max={mx:.3f}ms  results={len(r)}")

# 3. SQL on rdf_edges — traditional SQL path
def sql_rdf():
    c = conn.cursor()
    c.execute("SELECT o_id FROM Graph_KG.rdf_edges WHERE s = ? AND p = ?", [SEED, "KNOWS"])
    return c.fetchall()

p50, mn, mx, r = timeit(sql_rdf)
print(f"\n3. SQL rdf_edges WHERE s=? AND p=?  [index scan]")
print(f"   p50={p50:.3f}ms  min={mn:.3f}ms  max={mx:.3f}ms  results={len(r)}")

# 4. SQL MatchEdges stored proc (^KG scan via SQL)
try:
    def sql_match():
        c = conn.cursor()
        c.execute("SELECT * FROM Graph_KG.MatchEdges(?,?,?)", [SEED, "KNOWS", 0])
        return c.fetchall()
    p50, mn, mx, r = timeit(sql_match)
    print(f"\n4. Graph_KG.MatchEdges(seed, 'KNOWS', 0)  [^KG via SqlProc]")
    print(f"   p50={p50:.3f}ms  min={mn:.3f}ms  max={mx:.3f}ms  results={len(r)}")
except Exception as e:
    print(f"\n4. MatchEdges: {e}")

# 5. execute_cypher — full Python stack, returns node properties
def cypher_full():
    return engine.execute_cypher(
        "MATCH (s {node_id: $id})-[:KNOWS]->(n) RETURN n.node_id",
        {"id": SEED}
    )

p50, mn, mx, r = timeit(cypher_full, warmup=3, runs=10)
print(f"\n5. execute_cypher RETURN n.node_id  [Cypher→SQL, full stack]")
print(f"   p50={p50:.3f}ms  min={mn:.3f}ms  max={mx:.3f}ms  results={len(r.get('rows',[]))}")

# 6. execute_cypher — count only (no property join)
def cypher_count():
    return engine.execute_cypher(
        "MATCH (s {node_id: $id})-[:KNOWS]->(n) RETURN count(n) AS cnt",
        {"id": SEED}
    )

p50, mn, mx, r = timeit(cypher_count, warmup=3, runs=10)
print(f"\n6. execute_cypher COUNT(n)  [avoids rdf_props JOIN]")
print(f"   p50={p50:.3f}ms  min={mn:.3f}ms  max={mx:.3f}ms  result={r.get('rows')}")

# 7. Measure Python parse+translate overhead alone (no IRIS)
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

def parse_translate_only():
    q = parse_query("MATCH (s {node_id: $id})-[:KNOWS]->(n) RETURN n.node_id")
    return translate_to_sql(q, {"id": SEED})

p50, mn, mx, r = timeit(parse_translate_only, warmup=10, runs=100)
print(f"\n7. Python parse+translate only  [no IRIS round-trip]")
print(f"   p50={p50:.4f}ms  min={mn:.4f}ms  max={mx:.4f}ms")

# 8. SQL execution only (strip Python overhead from #5)
generated_sql, params = parse_translate_only()
def sql_direct():
    c = conn.cursor()
    c.execute(generated_sql, list(params.values()))
    return c.fetchall()

print(f"\n8. Generated SQL execution only")
print(f"   SQL: {generated_sql[:120]}...")
p50, mn, mx, r = timeit(sql_direct, warmup=5, runs=20)
print(f"   p50={p50:.3f}ms  min={mn:.3f}ms  max={mx:.3f}ms  results={len(r)}")

# Summary
print(f"\n{'='*60}")
print("SUMMARY")
print(f"{'='*60}")
print(f"GES published 1-hop KNOWS p50:   0.14ms")
print(f"IVG BFSFastJson (^KG $Order):    measured above as #1/#2")
print(f"IVG execute_cypher (full stack): measured above as #5")
print(f"Python parse+translate:          measured above as #7")
