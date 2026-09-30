"""4.1.1 — NKGAccel.BFSJson answers from ObjectScript when the callout cannot take the graph.

BFSJson hands kg_bfs_global the whole ^NKG adjacency as one $ZF argument. At 300
nodes (30 KB) that works; at 600 nodes it raises <OUT OF $ZF HEAP SPACE>, at 1,000
<MAX $ZF STRING>. Nothing caught it, so a BFS on an ordinary graph raised, and the
store's Arno probe, which calls BFSJson, disabled Arno for the whole store. The live
proof is tests/e2e/test_411_bfs_zf_limit_e2e.py.
"""

from __future__ import annotations

import re
from pathlib import Path

CLS = Path(__file__).resolve().parents[2] / "iris_src/src/Graph/KG/NKGAccelTraversal.cls"


def _body():
    return re.search(r"^ClassMethod BFSJson\(.*?^\}", CLS.read_text(), re.S | re.M).group(0)


def test_export_and_callout_are_inside_a_try_that_falls_back():
    body = _body()
    t = body.index("Try {")
    catch = body.index("} Catch", t)
    assert t < body.index("Set adjStr = ") < catch
    assert t < body.index("Set raw = $ZF(-5") < catch
    assert "BFSFastJsonSorted(" in body[catch : body.index("}", body.index("{", catch)) + 1]
