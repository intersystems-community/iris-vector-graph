"""The Arno library's BFS answers each hit's real relationship type (4.6.0, staletests).

On Arno 1.2.1 (x86_64), ``Graph.KG.NKGAccel.BFSJson`` (the native path, the library's ``kg_bfs_global``) answered
``"p": "R"`` for every hit, while ``Graph.KG.Traversal.BFSFastJsonSorted`` answers the edge's own type ("CHUNK") for
the same seed. ``ExportAdjacencyWithPreds`` hands the library the right types, so the defect is the library's; IVG
now resolves each hit's type from ``^KG("out")`` as it stages the hits (``NKGAccelTraversal.BFSJson``), so every
reader of the staged hits (``ReadBFSPage``, ``ReadBFSResults``, ``IRISGraphStore.execute_bfs``'s ``pred`` column, the
variable-length executor's relationship-property filter) gets a type the edge has. Cypher's ``[x IN r | type(x)]``
was not affected (it reads ``rdf_edges``).

The expected types come from the graph as written here, never from IVG's answer. Runs in the scratch namespace
``IVG_NAMESPACE`` names (an IVG460* one: the R309 gate makes one), which it erases; on a container with the Arno
library loaded, and checks the native path answered (``LastBfsPath() = "native"``), not the ObjectScript fallback.

    IVG_NAMESPACE=IVG460FXBP pytest tests/e2e/test_460_bfs_native_predicate_e2e.py
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns262

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    ns262.requires_container,
    pytest.mark.skipif(not NS.startswith("IVG460"), reason="IVG_NAMESPACE must name an IVG460* scratch namespace "
                                                           "(the module erases its graph)"),
]

#: s -CHUNK-> a (and s -ALSO-> a), s -KNOWS-> b, a -CHUNK-> c, b -LIKES-> d, s -R-> d: "R" is a real type here too,
#: so an answer of "R" for s -> a is caught, and s -> d's "R" must stay.
EDGES = [("s", "CHUNK", "a"), ("s", "ALSO", "a"), ("s", "KNOWS", "b"), ("a", "CHUNK", "c"), ("b", "LIKES", "d"),
         ("s", "R", "d")]
TYPES = {}
for _s, _p, _o in EDGES:
    TYPES.setdefault((_s, _o), set()).add(_p)


@pytest.fixture(scope="module")
def engine():
    from iris_vector_graph import IRISGraphEngine

    conn = ns262.connect(NS)
    eng = IRISGraphEngine(conn)
    native = ns262.native(conn)
    native.classMethodValue("Graph.KG.Eraser", "EraseAll")
    for v in "sabcd":
        eng.create_node(v)
    for s, p, o in EDGES:
        eng.create_edge(s, p, o)
    eng.sync()
    if not eng._store._detect_arno():
        pytest.skip("the Arno library is not loaded on this container: BFSJson has no native path to check")
    yield eng
    native.classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.close()


def _native_hits(engine, preds: str):
    from iris_vector_graph.engine import _bfs_stream_pages

    native = ns262.native(engine.conn)
    raw = str(native.classMethodValue("Graph.KG.NKGAccel", "BFSJson", "s", preds, 2, 0))
    path = str(native.classMethodValue("Graph.KG.NKGAccelTraversal", "LastBfsPath"))
    assert path == "native", f"BFSJson answered from {path!r}, not the library: {raw[:200]}"
    assert raw.startswith("SORTED:") and raw != "SORTED:0", raw
    return list(_bfs_stream_pages(engine.conn, raw.split(":")[1]))


def _wrong(hits):
    return [h for h in hits if h["p"] not in TYPES.get((h["s"], h["o"]), set())]


def test_every_native_hit_has_a_type_its_edge_has(engine):
    hits = _native_hits(engine, "")
    assert {(h["s"], h["o"]) for h in hits} >= {("s", "a"), ("s", "b"), ("s", "d"), ("a", "c")}
    assert _wrong(hits) == [], f"hits whose type their edge does not have: {_wrong(hits)}"
    assert [h["p"] for h in hits if (h["s"], h["o"]) == ("s", "d")] == ["R"]


def test_a_type_filter_answers_that_type(engine):
    hits = _native_hits(engine, '["CHUNK"]')
    assert sorted((h["s"], h["p"], h["o"]) for h in hits) == [("a", "CHUNK", "c"), ("s", "CHUNK", "a")]


def test_the_store_s_pred_column_is_the_edge_s_type(engine):
    store = engine._store
    assert type(store._select_bfs_strategy(None, "out")).__name__ == "_ArnoBfsAdapter"
    rows = store.execute_bfs("s", [], 2, "out", 0).rows
    # a row names the node, not its parent, so its pred is checked against every edge into the node
    types_into = {node: {p for _s, p, o in EDGES if o == node} for node in "abcd"}
    bad = [r for r in rows if r[2] not in types_into[r[0]]]
    assert rows and bad == [], f"rows whose pred no edge into the node has: {bad}"
    assert store.execute_bfs("s", ["KNOWS"], 2, "out", 0).rows == [["b", 1, "KNOWS"]]
