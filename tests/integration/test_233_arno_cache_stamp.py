"""Spec 233 FR-010: Arno's default-graph PPR answers from the live default graph.

In a process that has loaded the Arno library, `Graph.KG.ArnoAccel.PPRJson` answers
from `^ArnoKG("KG","graph_json")`, which `BuildGraphJson` fills and `CacheGraphJson`
reuses while its stamp equals `^KG("__version")`. Two defects: `BuildGraphJson` walked
the pre-spec-214 layout `^KG("out",s,p,o)`, so under `^KG("out",graph,s,p,o)` it listed
graph keys as nodes and nodes as edge types; and only `Eraser` bumped the stamp, so a
fixed snapshot would still miss every edge written after it. A process without the
library falls back to `Graph.KG.PageRank.RunJson` and was always right, which is why
`test_231_ppr_graph_e2e::test_default_graph_walk_stays_in_the_default_graph` failed
only after an earlier test in the same connection had loaded Arno.
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
    """`ArnoAccel.PPRJson` in a process that has loaded the library, so Rust answers
    and not the `RunJson` fallback (whose rows say `id`, not `node`)."""
    import json

    if not eng._store._detect_arno():
        pytest.skip("arno library not loadable")
    raw = str(eng._iris_obj().classMethodValue("Graph.KG.ArnoAccel", "PPRJson", json.dumps([seed]), "0.85", "20"))
    rows = json.loads(raw)
    if rows and "node" not in rows[0]:
        pytest.skip("PPRJson fell back to RunJson")
    return {r["node"]: r["score"] for r in rows}


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
