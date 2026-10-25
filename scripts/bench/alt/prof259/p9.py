"""The client path: execute_cypher end to end against the executor call alone and the in-IRIS time, per query (paired,
alternating, best of N); the Python-side pieces (parse, plan_query, translation cache) timed apart."""
import json
import sys
import time

from scripts.bench.alt.prof259.var import *

rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 15
conn, n = connect()
n.classMethodValue("Graph.KG.Cypher.Gen", "Mode", "sync")
eng = engine(conn, parallel_min=-1)
S = sets()
PICK = {"BI04": 4, "BI14": 6, "BI18": 0}
if len(sys.argv) > 2:
    PICK = {k: int(sys.argv[2]) for k in PICK}
from iris_vector_graph.cypher import native_plan
from iris_vector_graph.cypher.parser import parse_query

for short in ["BI04", "BI14", "BI18"]:
    full = NAME[short]
    q = QUERIES[full]
    params = {k: S[PICK[short]][k] for k in q["params"] if k in S[PICK[short]]}
    cypher = q.get("ivg", q["cypher"])
    plan, p = capture_plan(eng, full, params)
    t_client, t_call, t_iris, t_parse, t_plan, t_json = [], [], [], [], [], []
    for _ in range(rounds):
        t = time.perf_counter()
        eng.execute_cypher(cypher, params)
        t_client.append((time.perf_counter() - t) * 1000)
        t = time.perf_counter()
        parsed = parse_query(cypher)
        t_parse.append((time.perf_counter() - t) * 1000)
        t = time.perf_counter()
        native_plan.plan_query(parsed, params)
        t_plan.append((time.perf_counter() - t) * 1000)
        t = time.perf_counter()
        js = json.dumps(plan), json.dumps(params, default=str)
        t_json.append((time.perf_counter() - t) * 1000)
        t = time.perf_counter()
        head = str(n.classMethodValue("Graph.KG.Cypher.Exec", "Run", *js))
        t_call.append((time.perf_counter() - t) * 1000)
        json.loads(head)
        # in-IRIS only: Time's own phase sum
        n.classMethodVoid("ZIVGP.Prof", "Reset")
        t = time.perf_counter()
        n.classMethodValue("ZIVGP.Prof", "Time", *js)
        w = (time.perf_counter() - t) * 1000
        ph = json.loads(str(n.classMethodValue("ZIVGP.Prof", "Phases")))
        t_iris.append(sum(v for k, v in ph.items() if k in ("begin", "setup", "loop", "emitgroups", "end", "head", "kill", "lgflush")))
    print(f"{short} {params}")
    for label, v in [("execute_cypher end to end", t_client), ("  parse_query", t_parse), ("  plan_query", t_plan), ("  json.dumps plan+params", t_json), ("Exec.Run call (Native API, includes the in-IRIS run)", t_call), ("in-IRIS phase sum (Prof.Time)", t_iris)]:
        print(f"   {label:55s} min {min(v):7.2f}  med {statistics.median(v):7.2f}")
