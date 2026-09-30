"""4.1.1 — the ObjectScript BFS walks each hop from that hop's frontier only.

Found by the upgrade stage of scripts/quickstart_e2e.py on a stock container
(2026-09-30): `MATCH (a {node_id:$id})-[:NEXT*1..3]->(b) RETURN b.node_id AS id
ORDER BY id` on the chain n0->n1->n2->n3 answered n1, n1, n2, n1, n2, n3.
`Graph.KG.TraversalBFS.BFS` and `BFSFast` merged each hop's new nodes into
`frontier` without clearing it, so hop 3 re-expanded n0 and n1 and recorded
their edges again. Enterprise runs took the Arno path and never reached it.

The live proof is tests/e2e/test_411_bfs_frontier_e2e.py; this pins the source.
"""

from __future__ import annotations

import re
from pathlib import Path

CLS = Path(__file__).resolve().parents[2] / "iris_src/src/Graph/KG/TraversalBFS.cls"


def _method(name: str) -> str:
    text = CLS.read_text()
    m = re.search(rf"^ClassMethod {name}\(.*?^\}}", text, re.S | re.M)
    assert m, f"{name} not found in {CLS.name}"
    return m.group(0)


def _merges_follow_a_kill(body: str) -> list[str]:
    lines = [ln.strip() for ln in body.splitlines()]
    bad = []
    for i, ln in enumerate(lines):
        if ln.startswith("Merge frontier = nextFrontier"):
            if i == 0 or lines[i - 1] != "Kill frontier":
                bad.append(ln)
    return bad


def test_bfs_resets_the_frontier_each_hop():
    body = _method("BFS")
    assert "Merge frontier = nextFrontier" in body
    assert _merges_follow_a_kill(body) == []


def test_bfsfast_resets_the_frontier_each_hop():
    body = _method("BFSFast")
    assert "Merge frontier = nextFrontier" in body
    assert _merges_follow_a_kill(body) == []
