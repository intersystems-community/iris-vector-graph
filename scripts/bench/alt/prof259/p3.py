"""Parallel split vs one process, paired and alternating (plans with parallel_min -1 vs the engine default 1500),
the phases of the parallel run (coordinator and workers) from Prof.Time.

    python -m scripts.bench.alt.prof259.p3 BI18 ROUNDS SET[,SET..]
"""
import json
import subprocess
import sys

from scripts.bench.alt.prof259.var import *

short, rounds = sys.argv[1], int(sys.argv[2])
picks = [int(x) for x in sys.argv[3].split(",")]
conn, n = connect()
n.classMethodValue("Graph.KG.Cypher.Gen", "Mode", "sync")
S = sets()
q = QUERIES[NAME.get(short, short)]
set_walk(n, None)
eng1 = engine(conn, parallel_min=-1)
engP = engine(conn, parallel_min=int(sys.argv[4]) if len(sys.argv) > 4 else None)


def cpu():
    return subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}", "ivg-iris-enterprise"], capture_output=True, text=True).stdout.strip()


for i in picks:
    params = {k: S[i][k] for k in q["params"] if k in S[i]}
    plan1, p1 = capture_plan(eng1, NAME.get(short, short), params)
    planP, pP = capture_plan(engP, NAME.get(short, short), params)
    print(short, params, "container cpu before:", cpu())
    one, par, phs, clients = [], [], [], []
    for _ in range(rounds):
        w1, ph1, h1 = run_once(n, plan1, p1)
        wP, phP, hP = run_once(n, planP, pP)
        assert rows_of(h1) == rows_of(hP), "answers differ"
        one.append(w1)
        par.append(wP)
        phs.append(phP)
    print("   one process  wall:", spread(one))
    print("   parallel     wall:", spread(par), " workers:", json.loads(hP).get("workers"))
    ratio = sorted(a / b for a, b in zip(one, par))
    print("   ratio one/parallel per round:", " ".join(f"{r:.2f}" for r in ratio))
    best = min(range(rounds), key=lambda k: par[k])
    ph = phs[best]
    print("   best parallel round phases (ms):", json.dumps({k: v for k, v in ph.items() if k != "workers"}))
    for w, d in sorted(ph.get("workers", {}).items()):
        print("      worker", w, json.dumps(d))
