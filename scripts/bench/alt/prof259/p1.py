"""Survey: per query and parameter set, the single-process phase times (Prof.Time, best of N by total)."""
import json
import sys
import time

from scripts.bench.alt.prof259.lib import *

REPEAT = int(sys.argv[1]) if len(sys.argv) > 1 else 3
NAMES = sys.argv[2].split(",") if len(sys.argv) > 2 else FOUR[:3]
conn, n = connect()
n.classMethodValue("Graph.KG.Cypher.Gen", "Mode", "sync")
eng = engine(conn, parallel_min=-1)
S = sets()
for name in NAMES:
    q = QUERIES[name]
    for i, ps in enumerate(S[:8]):
        params = {k: ps[k] for k in q["params"] if k in ps}
        plan, p = capture_plan(eng, name, params)
        best = None
        for _ in range(REPEAT):
            n.classMethodVoid("ZIVGP.Prof", "Reset")
            t = time.perf_counter()
            head = str(n.classMethodValue("ZIVGP.Prof", "Time", json.dumps(plan), json.dumps(p)))
            wall = (time.perf_counter() - t) * 1000
            ph = json.loads(str(n.classMethodValue("ZIVGP.Prof", "Phases")))
            if best is None or wall < best[0]:
                best = (wall, ph)
        wall, ph = best
        flat = {k: v for k, v in ph.items() if k != "workers"}
        print(f"{name[:5]} set{i} {params} wall {wall:.1f} ms  ", json.dumps(flat), flush=True)
