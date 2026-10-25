"""Lever A prototype (group state in locals) against the generated walk: paired, alternating, one process; the whole
run's time ("wall", Python side, which includes EmitGroups and the flush) and the walk's alone.

    python -m scripts.bench.alt.prof259.p8 BI04 ROUNDS SET[,SET..]
"""
import sys

from scripts.bench.alt.prof259.lg import lg_transform
from scripts.bench.alt.prof259.var import *

short, rounds = sys.argv[1], int(sys.argv[2])
picks = [int(x) for x in sys.argv[3].split(",")]
conn, n = connect()
n.classMethodValue("Graph.KG.Cypher.Gen", "Mode", "sync")
eng = engine(conn, parallel_min=-1)
S = sets()
q = QUERIES[NAME[short]]
lines = source(n, HASH[short]).rstrip("\n").split("\n")
deploy(n, conn, f"LG{short}", "\n".join(lg_transform(lines)), base="ZIVGP.Prof")
for i in picks:
    params = {k: S[i][k] for k in q["params"] if k in S[i]}
    plan, p = capture_plan(eng, NAME[short], params)
    classes = [None, f"ZIVGP.LG{short}"]
    res, heads = paired_full(n, plan, p, classes, rounds=rounds)
    assert heads[None] == heads[classes[1]], "ANSWERS DIFFER"
    w = {c: [r[0] for r in res[c]] for c in classes}
    wk = {c: [r[1].get("walk", 0) for r in res[c]] for c in classes}
    fl = [r[1].get("lgflush", 0) for r in res[classes[1]]]
    print(f"{short} {params}  rows same: True")
    for c in classes:
        print(f"   {str(c):16s} wall min {min(w[c]):7.2f}  {spread(w[c])} | walk min {min(wk[c]):7.2f}")
    print(f"   flush (ms): {spread(fl)}")
    print(f"   speedup wall (min/min) {min(w[None]) / min(w[classes[1]]):.2f}x  walk {min(wk[None]) / min(wk[classes[1]]):.2f}x  pairwise wall ratios: {' '.join(f'{a / b:.2f}' for a, b in zip(w[None], w[classes[1]]))}")
