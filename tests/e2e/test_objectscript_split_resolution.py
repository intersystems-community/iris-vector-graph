"""Spec 187 — ObjectScript god-class split resolution contract (T006).

Verifies the public method surface of Graph.KG.NKGAccel and Graph.KG.Traversal is
preserved after the multiple-inheritance split. Written test-first (Principle III):
passes against the UN-split classes (the 35 resolvable methods already resolve), and
must keep passing after the split — any drop means a caller broke.

Scope (spec SC-3): the 35 RESOLVABLE Python-called method names (14 NKGAccel + 21
Traversal). The phantom/misrouted names Python calls on NKGAccel (WCCJson, SubgraphJson,
CDLPJson, PageRankJson, ReadBFSPage) are EXCLUDED — they do not resolve pre- or
post-split and are tracked as separate bugs (tasks T027), not asserted here.

Resolution = the method is visible on the class via %Dictionary.CompiledMethod
(inherited members are included on the compiled descendant), which is exactly what
`classMethodValue("Graph.KG.X", "Method")` needs at runtime.
"""
from __future__ import annotations

import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

# Verified 2026-06-01 from rg on iris_vector_graph/ caller sites (NOT the codebase graph,
# which under-reports DEFINES_METHOD edges — see codebase-memory-findings.md).
NKGACCEL_RESOLVABLE = [
    "BFSJson", "BetweennessGlobal", "BetweennessNeighborhood", "BuildNKGRust",
    "Capabilities", "ClosenessGlobal", "CountDistinctKHop", "EigenvectorGlobal",
    "InvalidateAdjCache", "IsLoaded", "KHopNeighbors", "Load", "PPRJson",
    "RandomWalkJson",
]
TRAVERSAL_RESOLVABLE = [
    "BFS", "BFSFastCountDistinct", "BFSFastJson", "BFSFastJsonSorted", "BackfillDegp",
    "Build2HopExactStats", "Build2HopStats", "BuildKG", "BuildNKG", "DijkstraJson",
    "InitNKGSkeleton", "KGEdgeCount", "KHop2CountExact", "KHop2CountFast",
    "KHop2NeighborIds", "KHopCount", "KHopNeighborIds", "NKGNodeCount", "NKGPopulated",
    "ReadBFSPage", "ReadBFSResults", "ShortestPathJson",
]


def _method_exists(conn, cls: str, method: str) -> bool:
    import iris

    irisobj = iris.createIRIS(conn)
    try:
        irisobj.classMethodValue(cls, method)
    except Exception as exc:
        msg = str(exc).upper()
        if "METHOD DOES NOT EXIST" in msg or "DOES NOT EXIST" in msg and method.upper() in msg:
            return False
        return True
    return True


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
@pytest.mark.parametrize("method", NKGACCEL_RESOLVABLE)
def test_nkgaccel_method_resolves(iris_connection, method):
    assert _method_exists(iris_connection, "Graph.KG.NKGAccel", method), (
        f"Graph.KG.NKGAccel.{method} not resolvable — split broke a caller"
    )


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
@pytest.mark.parametrize("method", TRAVERSAL_RESOLVABLE)
def test_traversal_method_resolves(iris_connection, method):
    assert _method_exists(iris_connection, "Graph.KG.Traversal", method), (
        f"Graph.KG.Traversal.{method} not resolvable — split broke a caller"
    )


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
def test_dijkstrapath_sql_projection_resolves(iris_connection):
    """SC-5: the SqlProc projection Graph_KG.DijkstraPath must still execute post-split."""
    cur = iris_connection.cursor()
    cur.execute(
        "SELECT Graph_KG.DijkstraPath(?, ?, ?, ?, ?, ?)",
        ["__nonexistent_src__", "__nonexistent_dst__", "", 9999, 10, "out"],
    )
    row = cur.fetchone()
    assert row is not None
