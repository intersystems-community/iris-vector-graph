"""The parallel plan run serially in one process (the 8 workers one after another, ^||ZSerial): the total work of each
phase, which contention among workers cannot blur, against the single-process run. Paired and alternating.

    python -m scripts.bench.alt.prof259.p4 BI18 ROUNDS SET[,SET..]
"""
import json
import sys

from scripts.bench.alt.prof259.var import *

short, rounds = sys.argv[1], int(sys.argv[2])
picks = [int(x) for x in sys.argv[3].split(",")]
conn, n = connect()
n.classMethodValue("Graph.KG.Cypher.Gen", "Mode", "sync")
S = sets()
full = NAME.get(short, short)
q = QUERIES[full]
set_walk(n, None)
eng1 = engine(conn, parallel_min=-1)
engP = engine(conn, parallel_min=int(sys.argv[4]) if len(sys.argv) > 4 else None)
for i in picks:
    params = {k: S[i][k] for k in q["params"] if k in S[i]}
    plan1, p1 = capture_plan(eng1, full, params)
    planP, pP = capture_plan(engP, full, params)
    one, ser, rows = [], [], []
    best = None
    for _ in range(rounds):
        n.classMethodValue("ZIVGP.T", "X", "Kill ^||ZSerial")
        w1, ph1, h1 = run_once(n, plan1, p1)
        n.classMethodValue("ZIVGP.T", "X", "Set ^||ZSerial = 1")
        wS, phS, hS = run_once(n, planP, pP)
        n.classMethodValue("ZIVGP.T", "X", "Kill ^||ZSerial")
        assert rows_of(h1) == rows_of(hS), "answers differ"
        one.append(w1)
        ser.append(wS)
        if best is None or wS < best[0]:
            best = (wS, phS, ph1)
    print(short, params)
    print("   single process:", spread(one))
    print("   8 parts serially:", spread(ser), " (= the work the parallel run does, minus the overlap)")
    wS, phS, ph1 = best
    print("   single phases:", json.dumps({k: v for k, v in ph1.items() if k != "workers"}))
    print("   serial coordinator phases:", json.dumps({k: v for k, v in phS.items() if k != "workers"}))
    tot = {}
    for w, d in phS.get("workers", {}).items():
        for k, v in d.items():
            tot[k] = tot.get(k, 0) + v
    print("   sum over the 8 parts:", json.dumps({k: round(v, 2) for k, v in tot.items()}))
