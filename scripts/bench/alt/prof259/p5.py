"""BI11 on a stride sample of its anchors (^||ZStride): phases, and the generated walk's source dumped."""
import json
import os
import sys

from scripts.bench.alt.prof259.var import *

stride = int(sys.argv[1]) if len(sys.argv) > 1 else 100
conn, n = connect()
n.classMethodValue("Graph.KG.Cypher.Gen", "Mode", "sync")
before = {e["hash"] for e in json.loads(str(n.classMethodValue("Graph.KG.Cypher.Gen", "Entries")))}
plan, p = plan_only(FOUR[3], {})
n.classMethodValue("ZIVGP.T", "X", f"Set ^||ZStride = {stride}")
set_walk(n, None)
for r in range(3):
    wall, ph, head = run_once(n, plan, p)
    print(f"stride {stride}: wall {wall:.1f} ms  rows {rows_of(head)}  ", json.dumps({k: v for k, v in ph.items() if k != "workers"}))
after = {e["hash"] for e in json.loads(str(n.classMethodValue("Graph.KG.Cypher.Gen", "Entries")))}
new = after - before
print("new hashes", new)
for h in new:
    src = source(n, h)
    open(os.path.expanduser(f"~/.cache/ivg-bench/p259/src_{h}.txt"), "w").write(src)
    print(src)
