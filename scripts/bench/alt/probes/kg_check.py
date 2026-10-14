import os, iris
from iris_vector_graph.engine import IRISGraphEngine
c = iris.connect("localhost", 31972, os.environ["NS"], "_SYSTEM", "SYS")
e = IRISGraphEngine(c)
g = e.get_bucket_groups("CALLS_AT", 0, 9_999_999_999)
top = sorted(g, key=lambda x: x["count"], reverse=True)[0]
print(os.environ["NS"], len(g), top["source"], top["count"], "total CALLS_AT", sum(x["count"] for x in g))
