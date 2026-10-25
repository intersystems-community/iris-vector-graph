"""BI11 prototype: the cross filters inline (sort keys cached per node in locals, the other node's key looked up in the
same cache) against the generated walk's NodeOK calls, paired and alternating, on a stride sample of the anchors."""
import json
import sys

from scripts.bench.alt.prof259.var import *

stride = int(sys.argv[1]) if len(sys.argv) > 1 else 200
rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 5
conn, n = connect()
n.classMethodValue("Graph.KG.Cypher.Gen", "Mode", "sync")
plan, p = plan_only(FOUR[3], {})
src = source(n, "3300079668")
lines = src.rstrip("\n").split("\n")

IDX, DATA, SLOT = "^t52S.DMCe.3", "^t52S.DMCe.1", 4


def xcheck(node, other, prop="sid", op=">", label="Person", ind="\t"):
    # labels first, as NodeOK does; then the later node's sort key, the other node's, and the comparison
    return [
        f"{ind}Continue:'$Data(^KG(\"label\",pKey,\"{label}\",m{node}))",
        f"{ind}Set kl = $Get(pQ(\"xs\",\"{prop}\",m{node}))",
        f"{ind}If kl = \"\" {{ Set r = $Order({IDX}(gk, m{node}, \"{prop}\", \"\")) Set kl = $Select(r=\"\": \"z\", 1: ##class(Graph.KG.Cypher.SortKey).Key($ListGet($Get({DATA}(r)), {SLOT}), \"x\")) Set pQ(\"xs\",\"{prop}\",m{node}) = kl }}",
        f"{ind}Set ko = $Get(pQ(\"xs\",\"{prop}\",{other}))",
        f"{ind}If ko = \"\" {{ Set r = $Order({IDX}(gk, {other}, \"{prop}\", \"\")) Set ko = $Select(r=\"\": \"z\", 1: ##class(Graph.KG.Cypher.SortKey).Key($ListGet($Get({DATA}(r)), {SLOT}), \"x\")) Set pQ(\"xs\",\"{prop}\",{other}) = ko }}",
        f"{ind}Continue:'..XOK(\"{op}\", kl, ko)",
    ]


out = []
for ln in lines:
    if "Continue:'..NodeOK(.pQ,1,pKey,pGName,m1,.pB)" in ln:
        out += xcheck(1, "s1")
    elif "Continue:'..NodeOK(.pQ,2,pKey,pGName,m2,.pB)" in ln:
        out += xcheck(2, "s2")
    else:
        out.append(ln)
extra = '''ClassMethod XOK(pOp As %String, pKa As %String, pKb As %String) As %Boolean
{
	Set tR = ..CmpKeys(pKa, pKb)
	Quit $Case(pOp, "<": tR = -1, "<=": (tR = -1) || (tR = 0), ">": tR = 1, : (tR = 1) || (tR = 0))
}'''
deploy(n, conn, "BI11x", "\n".join(out), extra)
deploy(n, conn, "BI11g", "\n".join(lines))
n.classMethodValue("ZIVGP.T", "X", f"Set ^||ZStride = {stride}")
res, heads = paired(n, plan, p, [None, "ZIVGP.BI11g", "ZIVGP.BI11x"], rounds=rounds)
print(f"stride {stride} (anchors ~{9892 // stride})")
for c in res:
    print(f"   {str(c):16s} walk min {min(res[c]):8.1f} ms  {spread(res[c])}  rows {heads[c]}")
print("   inline / generated (min):", f"{min(res['ZIVGP.BI11x']) / min(res[None]):.3f}", " speedup", f"{min(res[None]) / min(res['ZIVGP.BI11x']):.2f}x")
