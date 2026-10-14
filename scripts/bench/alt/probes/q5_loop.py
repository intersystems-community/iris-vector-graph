import os, time, statistics, iris
from iris_vector_graph.engine import IRISGraphEngine
c = iris.connect("localhost", 31972, os.environ["NS"], "_SYSTEM", "SYS")
e = IRISGraphEngine(c)
cur = c.cursor()
cur.execute("SELECT TOP 40 s, o_id FROM Graph_KG.rdf_edges")
pairs = [(r[0], r[1]) for r in cur.fetchall() if r[0] != r[1]]
a, b = pairs[3]
q = "MATCH p = shortestPath((a {node_id: $a})-[*..8]-(b {node_id: $b})) RETURN length(p) AS hops"
for _ in range(100): e.execute_cypher(q, {"a": a, "b": b})
runs = []
for _ in range(5):
    t = time.perf_counter()
    for _ in range(500): r = e.execute_cypher(q, {"a": a, "b": b})
    runs.append((time.perf_counter() - t) / 500 * 1000)
print(os.environ["NS"], "Q5 ms/call", [round(x, 3) for x in runs], "median", round(statistics.median(runs), 3), "rows", len(r.rows), r.rows[:1])
