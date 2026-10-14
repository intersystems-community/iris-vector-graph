import sys, os, time, iris
sys.path.insert(0, "/Users/tdyar/ws/iris-vector-graph/scripts/bench/alt")
import snb_queries as q
from iris_vector_graph.engine import IRISGraphEngine
c = iris.connect("localhost", 31972, "SNBBENCHB", "_SYSTEM", "SYS")
e = IRISGraphEngine(c)
ps = q.sample_params("/tmp/snb01/out", n=5, seed=42)
sp = q.QUERIES["IC13_shortest_path"]
for form in ("cypher", "ivg"):
    out = []
    for p in ps:
        t = time.perf_counter()
        r = e.execute_cypher(sp[form], {k: p[k] for k in sp["params"]}, raise_on_error=True)
        out.append((len(r.rows), r.rows[:1], round((time.perf_counter()-t)*1000, 1)))
    print(form, out)
