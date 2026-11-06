"""4.6.0 R050: neighbourhood betweenness is answered by the ObjectScript tier alone, directed, as networkx's.

BetweennessNeighborhoodOS set both bits of each edge in ^bcBitAdj, so it scored the neighbourhood as undirected and
counted every pair from both ends: from a on a->z, z->y, y->x, x->z, x->y it answered [z 4], where directed Brandes
(networkx on the DiGraph, and betweenness_centrality(direction="out")) answers z 2, y 2, x 1. It now sets the
out-edge's bit only. The Arno call (kg_betweenness_neighborhood_v, reached only in USER since R234) is not made:
nothing tested its answer against the directed one, and Arno's global kg_betweenness_global_v scores undirected,
which is why global betweenness does not ask it either. The live answers are in tests/e2e/test_460_r050_centrality_ranking_e2e.py.
"""

from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG" / "NKGAccelCentrality.cls"


def _body(name: str) -> str:
    return SRC.read_text().split(f"ClassMethod {name}(", 1)[1].split("\nClassMethod ", 1)[0]


def test_the_neighbourhood_does_not_call_arno():
    body = _body("BetweennessNeighborhood")
    assert "$ZF(" not in body and "IsLoaded" not in body  # no callout, whatever the namespace
    assert "..BetweennessNeighborhoodOS(" in body


def test_the_objectscript_tier_sets_only_the_out_edges_bit():
    body = _body("BetweennessNeighborhoodOS")
    assert "Set $BIT(^bcBitAdj(parentPid,idx),dstIdx+1) = 1" in body
    assert "^bcBitAdj(parentPid,dstIdx),idx+1" not in body
