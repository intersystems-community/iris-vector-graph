"""4.1.1 — ArnoAccel answers PageRank, PPR, WCC, CDLP and subgraph from ObjectScript.

The Rust functions behind these five (`kg_pagerank_global`, `kg_ppr_global`,
`kg_wcc_global`, `kg_cdlp_global`, `kg_subgraph_global`) read the live default graph
through `read_kg_adjacency_auto` -> `read_kg_native`, which walks `^KG("out",0,...)`
with the callout's key iterator. On the enterprise test image (libarno_callout.so of
2026-07-04) that walk is unreliable:

- the first call in a process sees only the first source and its successors (8 of 66
  nodes; a PPR seed that sorts later is not in the graph, so it scores nothing);
- later calls over unchanged data disagree: WCC answered 4, 13, 27, 34, 24 and 22
  components in one process, PPR 66 then 63 nodes.

Spec 233 read the `test_231` failure as a stale `graph_json` snapshot and stamped it,
but no Rust reader opens `graph_json`. Each ObjectScript owner walks the same `^KG`
directly and answers the same way every time, so ArnoAccel returns the owner's answer
until the reader is fixed in Arno. The live proof is
tests/e2e/test_411_arno_kg_reader_e2e.py.
"""

from __future__ import annotations

import re
from pathlib import Path

CLS = Path(__file__).resolve().parents[2] / "iris_src/src/Graph/KG/ArnoAccel.cls"

OWNERS = {
    "PageRankGlobalJson": "##class(Graph.KG.PageRank).PageRankGlobalJson(",
    "PPRJson": "##class(Graph.KG.PageRank).RunJson(",
    "WCCJson": "##class(Graph.KG.Algorithms).WCCJson(",
    "CDLPJson": "##class(Graph.KG.Algorithms).CDLPJson(",
    "SubgraphJson": "##class(Graph.KG.Subgraph).SubgraphJson(",
}


def _method(name: str) -> str:
    m = re.search(rf"^ClassMethod {name}\(.*?^\}}", CLS.read_text(), re.S | re.M)
    assert m, f"{name} not found in {CLS.name}"
    return m.group(0)


def test_each_default_graph_reader_returns_its_owner_without_calling_rust():
    for name, owner in OWNERS.items():
        body = _method(name)
        assert "CallFn(" not in body, f"{name} still asks the Rust ^KG reader"
        assert owner in body, f"{name} should return {owner}...)"


def test_the_other_callouts_are_left_alone():
    """No ObjectScript owner exists for these, and nothing in IVG calls them."""
    for name in ("KhopSampleJson", "RandomWalkJson", "NeighborAggJson"):
        assert "CallFn(" in _method(name)
