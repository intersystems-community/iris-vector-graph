"""Check the variant harness (V0 = the unmodified generated source) and count iterations per loop level."""
import re
import sys

from scripts.bench.alt.prof259.var import *

conn, n = connect()
n.classMethodValue("Graph.KG.Cypher.Gen", "Mode", "sync")
eng = engine(conn, parallel_min=-1)
S = sets()
PICK = {"BI04": [4, 3], "BI14": [6, 1], "BI18": [0, 5]}
for short in ["BI04", "BI14", "BI18"]:
    q = QUERIES[NAME[short]]
    src = source(n, HASH[short])
    deploy(n, conn, f"V0{short}", src)
    # counters: after each neighbour loop's exit test, and at the emission
    lines = src.split("\n")
    out = []
    for ln in lines:
        out.append(ln)
        m = re.match(r"^(\t*)Quit:(m\d)=\"\"$", ln)
        if m:
            out.append(f"{m.group(1)}Do $Increment(^||ZC(\"{m.group(2)}\"))")
        if "Set g = $Get(pQ(\"cgc\"" in ln:
            ind = re.match(r"^(\t*)", ln).group(1)
            out.insert(len(out) - 1, ind + 'Do $Increment(^||ZC("emit"))')
        if re.match(r"^\t*If z\d \{$", ln):
            out.append(re.match(r"^(\t*)", ln).group(1) + "\tDo $Increment(^||ZC(\"zloop\"))")
    deploy(n, conn, f"C{short}", "\n".join(out))
    for i in PICK[short]:
        ps = S[i]
        params = {k: ps[k] for k in q["params"] if k in ps}
        plan, p = capture_plan(eng, NAME[short], params)
        res, heads = paired(n, plan, p, [None, f"ZIVGP.V0{short}"], rounds=5)
        print(short, params, "generated", spread(res[None]), "| V0 copy", spread(res[f"ZIVGP.V0{short}"]), "same rows:", heads[None] == heads[f"ZIVGP.V0{short}"])
        n.classMethodValue("ZIVGP.T", "X", "Kill ^||ZC")
        set_walk(n, f"ZIVGP.C{short}")
        run_once(n, plan, p)
        set_walk(n, None)
        print("   counts", n.classMethodValue("ZIVGP.T", "X", 'Set k="",o="" For { Set k=$Order(^||ZC(k),1,v) Quit:k=""  Set o=o_k_"="_v_" " } Set ^||zout=o'))
