"""The parallel gate's work estimate (Exec.ParallelWantedH1 / ParallelWanted) per query and set: the largest
parallel_min at which the run still splits, against the single-process time (what the estimate should predict)."""
import json
import sys

from scripts.bench.alt.prof259.var import *

conn, n = connect()
n.classMethodValue("Graph.KG.Cypher.Gen", "Mode", "sync")
S = sets()
for short in sys.argv[1].split(","):
    full = NAME.get(short, short)
    q = QUERIES[full]
    for i in [int(x) for x in sys.argv[2].split(",")]:
        params = {k: S[i][k] for k in q["params"] if k in S[i]}
        plan, p = plan_only(full, params)

        def splits(pm):
            pl = dict(plan, parallel_min=pm)
            w, ph, head = run_once(n, pl, p)
            return bool(json.loads(head).get("workers"))

        lo, hi = 1, 200000
        if not splits(1):
            print(short, params, "never splits (estimate < 1)")
            continue
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if splits(mid):
                lo = mid
            else:
                hi = mid - 1
        t = min(run_once(n, dict(plan, parallel_min=-1), p)[0] for _ in range(3))
        print(f"{short} set{i} {params}: estimate ~{lo} (default gate 1500)   single-process {t:.1f} ms")
