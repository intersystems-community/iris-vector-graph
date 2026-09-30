"""Spec 233 FR-010: Arno's default-graph PPR answers from the live default graph.

Spec 233 blamed a stale `^ArnoKG("KG","graph_json")` snapshot for
`test_231_ppr_graph_e2e::test_default_graph_walk_stays_in_the_default_graph` and fixed
`BuildGraphJson`'s layout and the `^KG("__version")` stamp; those fixes stay tested
here. The real cause was the Rust `kg_ppr_global` reader, which never opens graph_json
and walks the live `^KG` unreliably. Since 4.1.1 `ArnoAccel.PPRJson` answers from
`Graph.KG.PageRank.RunJson`; see tests/unit/test_411_arno_kg_readers.py.
"""

import uuid

import pytest

PRED = "IVG233_STAMP"


def _stamp(eng):
    return int(eng._iris_obj().get("^KG", "__version") or 0)


@pytest.fixture
def eng(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(iris_connection)


def _arno_ppr(eng, seed):
    """`ArnoAccel.PPRJson` after the store has loaded the library, as it does."""
    import json

    eng._store._detect_arno()
    raw = str(eng._iris_obj().classMethodValue("Graph.KG.ArnoAccel", "PPRJson", json.dumps([seed]), "0.85", "20"))
    return {r.get("id", r.get("node")): r["score"] for r in json.loads(raw)}


@pytest.mark.parametrize("graph", [None, "ivg233stamp"])
def test_write_and_delete_bump_the_stamp(eng, graph):
    tag = uuid.uuid4().hex[:8]
    s, o = f"stamp-{tag}-s", f"stamp-{tag}-o"
    eng.create_node(s)
    eng.create_node(o)
    try:
        before = _stamp(eng)
        eng.create_edge(s, PRED, o, graph=graph)
        written = _stamp(eng)
        assert written > before
        eng.delete_edge(s, PRED, o, graph=graph)
        assert _stamp(eng) > written
    finally:
        eng.delete_edge(s, PRED, o, graph=graph)
        eng.delete_node(s)
        eng.delete_node(o)


def test_default_ppr_sees_an_edge_written_after_it_cached(eng):
    tag = uuid.uuid4().hex[:8]
    a, b, s, d = (f"stamp-{tag}-{n}" for n in ("a", "b", "s", "d"))
    for n in (a, b, s, d):
        eng.create_node(n)
    try:
        eng.create_edge(a, PRED, b)
        assert eng.kg_PERSONALIZED_PAGERANK([a]).get(b, 0) > 0
        assert _arno_ppr(eng, a).get(b, 0) > 0
        eng.create_edge(s, PRED, d)
        assert eng.kg_PERSONALIZED_PAGERANK([s]).get(d, 0) > 0
        assert _arno_ppr(eng, s).get(d, 0) > 0
    finally:
        eng.delete_edge(a, PRED, b)
        eng.delete_edge(s, PRED, d)
        for n in (a, b, s, d):
            eng.delete_node(n)


def test_graph_json_is_the_default_graph_only(eng):
    """`BuildGraphJson` read the pre-spec-214 layout `^KG("out",s,p,o)`. Under
    `^KG("out",graph,s,p,o)` it listed graph keys as nodes and nodes as edge types."""
    import json

    tag = uuid.uuid4().hex[:8]
    s, d, n = (f"stamp-{tag}-{x}" for x in ("s", "d", "n"))
    for x in (s, d, n):
        eng.create_node(x)
    try:
        eng.create_edge(s, PRED, d)
        eng.create_edge(s, PRED, n, graph="ivg233stamp")
        graph = json.loads(str(eng._iris_obj().classMethodValue("Graph.KG.ArnoAccel", "BuildGraphJson")))
        assert {"src": s, "dst": d, "type": PRED} in graph["edges"]
        assert not [e for e in graph["edges"] if n in (e["src"], e["dst"])]
        assert {s, d} <= set(graph["nodes"])
        assert "0" not in graph["nodes"] and "ivg233stamp" not in graph["nodes"]
        scores = _arno_ppr(eng, s)
        assert scores.get(d, 0) > 0
        assert n not in scores
    finally:
        eng.delete_edge(s, PRED, d)
        eng.delete_edge(s, PRED, n, graph="ivg233stamp")
        for x in (s, d, n):
            eng.delete_node(x)


def test_rebuild_bumps_the_stamp(eng):
    before = _stamp(eng)
    eng._iris_obj().classMethodValue("Graph.KG.Traversal", "BuildKG")
    assert _stamp(eng) > before
