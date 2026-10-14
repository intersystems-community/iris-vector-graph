"""Audit the baseline's stored out-degrees across the bidirectional-PPR test's two fixtures (explains its 10 ms rows).

    NS=IVGBENCHA PYTHONPATH=/tmp/ivg-src-A python scripts/bench/alt/probes/degree_drift.py
"""
import os, random, iris
from iris_vector_graph.engine import IRISGraphEngine
ns = os.environ["NS"]
conn = iris.connect("localhost", 31972, ns, "_SYSTEM", "SYS")
eng = IRISGraphEngine(conn, embedding_dimension=768); eng.initialize_schema(auto_deploy_objectscript=True)
nat = iris.createIRIS(conn)
cur = conn.cursor()
def clean():
    for sql in ("DELETE FROM Graph_KG.rdf_edges WHERE s LIKE 'TEST_PERF:%' OR o_id LIKE 'TEST_PERF:%'",
                "DELETE FROM Graph_KG.rdf_labels WHERE s LIKE 'TEST_PERF:%'",
                "DELETE FROM Graph_KG.rdf_props WHERE s LIKE 'TEST_PERF:%'",
                "DELETE FROM Graph_KG.nodes WHERE node_id LIKE 'TEST_PERF:%'"):
        cur.execute(sql)
    conn.commit()
def build():
    for i in range(1000): eng.create_node(f"TEST_PERF:N{i}")
    random.seed(42)
    for i in range(1000):
        for _ in range(5):
            t = random.randint(0, 999)
            if t != i:
                try: eng.create_edge(f"TEST_PERF:N{i}", "connects_to", f"TEST_PERF:N{t}")
                except Exception: pass
def audit(tag):
    cur.execute("SELECT s, COUNT(*) FROM Graph_KG.rdf_edges WHERE s LIKE 'TEST_PERF:%' GROUP BY s")
    rows = {r[0]: r[1] for r in cur.fetchall()}
    bad = 0; tot_stored = 0; tot_rows = 0
    for n in (f"TEST_PERF:N{i}" for i in range(1000)):
        d = nat.get("^KG", "deg", 0, n)
        d = int(d) if d not in (None, "") else 0
        tot_stored += d; tot_rows += rows.get(n, 0)
        if d != rows.get(n, 0): bad += 1
    print(f"{tag}: nodes with stored deg != rdf_edges rows: {bad}; sum stored={tot_stored} sum rows={tot_rows}")
clean(); build(); audit("after fixture 1")
clean(); audit("after teardown 1 (SQL delete)")
build(); audit("after fixture 2")
clean()
