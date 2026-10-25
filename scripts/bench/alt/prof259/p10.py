"""Lever D prototype: the parallel split on a hash of the count(DISTINCT) variable's hop, so its members are disjoint
across workers (no member set exchanged, no spill). Arms, alternating in one process, the same plan with parallel_min 0:
  single   one process (parallel_min -1)
  pos      the shipped split: every n-th entry of the first hop, members exchanged
  hash     the hash split of hop K (K = 1 for BI18, 2 for BI04)

    python -m scripts.bench.alt.prof259.p10 BI18 1 ROUNDS SET[,SET..]
"""
import sys

from scripts.bench.alt.prof259.var import *

short, hop, rounds = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
picks = [int(x) for x in sys.argv[4].split(",")]
conn, n = connect()
n.classMethodValue("Graph.KG.Cypher.Gen", "Mode", "sync")
S = sets()
full = NAME[short]
q = QUERIES[full]
lines = source(n, HASH[short]).rstrip("\n").split("\n")
out = []
for ln in lines:
    if "If tH1 { Continue:(($Increment(pQ(\"h1pos\"))-1)#tParts)'=tPart }" in ln:
        if hop == 1:
            out.append(ln.replace("(($Increment(pQ(\"h1pos\"))-1)#tParts)", "($ZCRC(m1,7)#tParts)"))
        continue
    out.append(ln)
    if hop > 1 and re.match(r"^\t*Quit:tMF&&\(pQ\(\"count\"\)>=tLim\)$", ln):
        pass
if hop > 1:
    # the filter after hop `hop`'s own exit and limit checks
    seen = 0
    res = []
    for ln in out:
        res.append(ln)
        if re.match(r"^\t*Quit:tMF&&\(pQ\(\"count\"\)>=tLim\)$", ln):
            seen += 1
            if seen == hop:
                ind = re.match(r"^(\t*)", ln).group(1)
                res.append(f"{ind}If tH1 {{ Continue:($ZCRC(m{hop},7)#tParts)'=tPart }}")
    out = res
deploy(n, conn, f"H{short}", "\n".join(out), base="ZIVGP.Prof")
eng1 = engine(conn, parallel_min=-1)
engP = engine(conn, parallel_min=0)
for i in picks:
    params = {k: S[i][k] for k in q["params"] if k in S[i]}
    plan1, p1 = capture_plan(eng1, full, params)
    planP, pP = capture_plan(engP, full, params)
    arms = {"single": (plan1, p1, None, ""), "pos": (planP, pP, None, ""), "hash": (planP, pP, f"ZIVGP.H{short}", str(hop))}
    res = {a: [] for a in arms}
    rows = {}
    for _ in range(rounds):
        for a, (pl, pa, cls, hh) in arms.items():
            set_walk(n, cls)
            n.classMethodValue("ZIVGP.T", "X", f'Set ^||ZHash = "{hh}"')
            w, ph, head = run_once(n, pl, pa)
            res[a].append((w, ph))
            rows[a] = rows_of(head)
    set_walk(n, None)
    n.classMethodValue("ZIVGP.T", "X", 'Set ^||ZHash = ""')
    print("single==pos", rows["single"] == rows["pos"], "single==hash", rows["single"] == rows["hash"])
    if rows["single"] != rows["hash"]:
        print("single", rows["single"]); print("hash  ", rows["hash"])
    print(f"{short} {params}  answers equal across the three arms")
    for a in arms:
        w = [r[0] for r in res[a]]
        print(f"   {a:7s} wall {spread(w)}")
    print("   ratios per round single/pos:", " ".join(f"{x[0] / y[0]:.2f}" for x, y in zip(res["single"], res["pos"])))
    print("   ratios per round single/hash:", " ".join(f"{x[0] / y[0]:.2f}" for x, y in zip(res["single"], res["hash"])))
    print("   ratios per round pos/hash:", " ".join(f"{x[0] / y[0]:.2f}" for x, y in zip(res["pos"], res["hash"])))
    best = min(range(rounds), key=lambda k: res["hash"][k][0])
    ph = res["hash"][best][1]
    print("   hash best round coordinator:", json.dumps({k: v for k, v in ph.items() if k != "workers"}))
    for wk, d in sorted(ph.get("workers", {}).items()):
        print("      worker", wk, json.dumps(d))
