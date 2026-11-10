"""4.7.0 row 8b, live: wrong answers found while fixing rows 8, 9 and 12.

1. Global PageRank took any damping factor and iteration count (``kg_PAGERANK(damping=2.0)``, ``CALL
   gds.pageRank.stream('g', {dampingFactor: 2.0})``, ``Graph.KG.PageRank.PageRankGlobalJson(2.0, 20)``): a damping
   outside (0, 1) gives negative scores. Each now raises (the class methods throw) naming the argument, so no
   negative score comes out.
2. ``Graph.KG.PageRank.Run`` scored a seed that is not a node of the graph at 1.0 (the walk started there and, the seed
   having no out-edge, handed its score back to itself). A seed outside the graph now scores nothing, as
   tests/oracle/reference.py states: ``['zz']`` answers no rows, and ``['a', 'zz']`` the answer of ``['a']``.
3. A VecIndex with a metric ``Graph.KG.VecIndex.Distance`` does not know (``mahalanobis``, a typo) measured cosine
   distance and reported score = +distance. Creating one now raises, and so does searching one created before.
4. ``Graph.KG.MCPTools.PPRWalk`` was reported (by me, from a misread) to answer "" on success; it answers
   ``status OK`` and its results, which this checks.

    IVG_NAMESPACE=IVG470ALG2 IVG_PORT=31972 IVG_TEST_CONTAINER=ivg-iris-enterprise \\
        pytest tests/e2e/test_470_row8b_e2e.py
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest

from tests.e2e import ns463
from tests.e2e.ns470alg import conn, load, native  # noqa: F401 - conn is a fixture

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

#: a -> b plus a loner c; from a at 0.85, a = 1 / 1.85, b = 0.85 / 1.85, c = 0 (hand-worked, as in row 9's file)
FROM_A = {"a": 1 / 1.85, "b": 0.85 / 1.85, "c": 0.0}


@pytest.fixture(scope="module")
def engs(conn):  # noqa: F811
    engs = load(conn, ["a", "b", "c"], [("a", "R", "b")])
    # a node of another graph: not a node of the default graph's walk
    engs["default"].create_node("elsewhere", graph="g2")
    return engs


# ------------------------------------------------------------------------------------ 1. global PageRank


@pytest.mark.parametrize("damping", [2.0, 1.5, 1.0, 0.0, -0.5])
def test_kg_pagerank_refuses_a_damping_outside_the_interval(engs, damping):
    with pytest.raises(ValueError, match="damping"):
        engs["default"].kg_PAGERANK(damping=damping)


def test_kg_pagerank_refuses_no_iterations(engs):
    with pytest.raises(ValueError, match="max_iterations"):
        engs["default"].kg_PAGERANK(max_iterations=0)


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("config, named", [("{dampingFactor: 2.0}", "dampingFactor"),
                                           ("{dampingFactor: -1.0}", "dampingFactor"),
                                           ("{maxIterations: 0}", "maxIterations")])
def test_gds_pagerank_stream_refuses_out_of_range(engs, route, config, named):
    with pytest.raises(ValueError, match=rf"gds\.pageRank\.stream: {named} "):
        engs[route].execute_cypher(f"CALL gds.pageRank.stream('g', {config}) YIELD nodeId, score "
                                   f"RETURN nodeId, score", {}, raise_on_error=True)


@pytest.mark.parametrize("cls, args", [("Graph.KG.PageRank", (2.0, 20)), ("Graph.KG.PageRank", (0.85, 0)),
                                       ("Graph.KG.ArnoAccel", (2.0, 20))])
def test_global_class_methods_refuse_out_of_range(engs, conn, cls, args):  # noqa: F811
    method = "PageRankGlobalJson"
    with pytest.raises(RuntimeError, match="alpha|maxIter"):
        native(conn).classMethodValue(cls, method, *args)


def test_global_pagerank_in_range_has_no_negative_score(engs):
    """The scores of a range that is accepted: none below 0, and they sum to 1 (networkx's convention)."""
    ranks = dict(engs["default"].kg_PAGERANK(damping=0.85, max_iterations=200))
    assert set(ranks) == {"a", "b", "c"}
    assert min(ranks.values()) >= 0.0
    assert sum(ranks.values()) == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------------- 2. a seed that is no node


def _routes(engs, monkeypatch):
    from iris_vector_graph import IRISGraphEngine

    eng = engs["default"]
    monkeypatch.setenv("IVG_DISABLE_ARNO", "1")
    fast = IRISGraphEngine(eng.conn, namespace=ns463.NS, schema_prefix=eng._schema_prefix)
    fast._store_capabilities = dict(fast._store_capabilities, ppr=False)
    fast.capabilities.objectscript_deployed = True
    fast.capabilities.kg_built = True

    def fast_path(seeds):
        with patch.object(fast, "_kg_PERSONALIZED_PAGERANK_python_fallback", side_effect=AssertionError("fallback")):
            return fast.kg_PERSONALIZED_PAGERANK(seeds, max_iterations=200)

    return {
        "store": lambda seeds: eng.kg_PERSONALIZED_PAGERANK(seeds, max_iterations=200),
        "RunJson": fast_path,
        "fallback": lambda seeds: eng._kg_PERSONALIZED_PAGERANK_python_fallback(seeds, 0.85, 200, 1e-6),
    }


@pytest.mark.parametrize("route", ["store", "RunJson", "fallback"])
@pytest.mark.parametrize("seeds", [["zz"], ["elsewhere"], ["zz", "elsewhere"]])
def test_a_seed_outside_the_graph_scores_nothing(engs, monkeypatch, route, seeds):
    assert _routes(engs, monkeypatch)[route](seeds) == {}


@pytest.mark.parametrize("route", ["store", "RunJson", "fallback"])
def test_a_seed_outside_the_graph_beside_one_inside_is_ignored(engs, monkeypatch, route):
    got = _routes(engs, monkeypatch)[route](["a", "zz", "elsewhere"])
    assert got == pytest.approx(FROM_A, abs=1e-4)


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_call_ivg_ppr_from_a_seed_outside_the_graph(engs, route):
    r = engs[route].execute_cypher("CALL ivg.ppr(['zz'], 0.85, 100) YIELD node, score RETURN node.node_id, score",
                                   {}, raise_on_error=True)
    assert r.rows == []
    r = engs[route].execute_cypher("CALL ivg.ppr(['a', 'zz'], 0.85, 100) YIELD node, score "
                                   "RETURN node.node_id, score", {}, raise_on_error=True)
    assert {row[0]: row[1] for row in r.rows} == pytest.approx(FROM_A, abs=1e-4)


def test_ppr_method_from_a_seed_outside_the_graph(engs):
    assert engs["default"].ppr("zz")["scores"] == []


@pytest.mark.parametrize("method, seeds, want", [
    ("RunJson", '["zz"]', {}),
    ("RunJson", '["elsewhere"]', {}),
    ("RunJson", '["a", "zz"]', {"a": FROM_A["a"], "b": FROM_A["b"]}),
])
def test_runjson_from_a_seed_outside_the_graph(engs, conn, method, seeds, want):  # noqa: F811
    raw = native(conn).classMethodValue("Graph.KG.PageRank", method, seeds, 0.85, 200)
    assert {r["id"]: r["score"] for r in json.loads(str(raw))} == pytest.approx(want, abs=1e-4)


def test_pprnativejson_from_a_seed_outside_the_graph_does_not_walk(engs, conn):  # noqa: F811
    """No seed is a node: no walk to hand the library (which refuses a namespace but USER) and no rows."""
    assert str(native(conn).classMethodValue("Graph.KG.ArnoAccel", "PPRNativeJson", '["zz"]', 0.85, 20)) == "[]"


# ------------------------------------------------------------------------------------- 3. a VecIndex metric


@pytest.mark.parametrize("metric", ["bogus", "mahalanobis"])
def test_vec_create_index_refuses_an_unknown_metric(engs, conn, metric):  # noqa: F811
    name = f"alg470_{uuid.uuid4().hex[:6]}"
    with pytest.raises(ValueError, match="metric must be one of"):
        engs["default"].vec_create_index(name, dim=2, metric=metric)
    with pytest.raises(RuntimeError, match="metric"):
        native(conn).classMethodValue("Graph.KG.VecIndex", "Create", name, 2, metric)
    assert native(conn).isDefined("^VecIdx", name) == 0, "a refused index left settings behind"


@pytest.mark.parametrize("metric", ["COSINE", "l2", "L1", "dot", "chebyshev"])
def test_vec_create_index_takes_every_known_metric(engs, metric):
    name = f"alg470_{uuid.uuid4().hex[:6]}"
    try:
        assert engs["default"].vec_create_index(name, dim=2, metric=metric)["metric"] == metric.lower()
    finally:
        engs["default"].vec_drop(name)


def test_searching_an_index_with_an_unknown_metric_raises(engs, conn):  # noqa: F811
    """An index made before 4.7.0 with a metric Distance does not know: search raises, not cosine-as-distance."""
    eng, o = engs["default"], native(conn)
    name = f"alg470_{uuid.uuid4().hex[:6]}"
    eng.vec_create_index(name, dim=2, metric="cosine")
    try:
        eng.vec_insert(name, "same", [1.0, 0.0])
        o.set("mahalanobis", "^VecIdx", name, "cfg", "metric")
        with pytest.raises(RuntimeError, match="metric"):
            eng.vec_search(name, [1.0, 0.0], k=1)
        eng.vec_build(name)
        with pytest.raises(RuntimeError, match="metric"):
            eng.vec_search(name, [1.0, 0.0], k=1)
    finally:
        eng.vec_drop(name)


# ------------------------------------------------------------------------------------------ 4. MCP PPRWalk


def test_mcp_pprwalk_answers_its_results(engs, conn):  # noqa: F811
    o = native(conn)
    tool = o.classMethodObject("Graph.KG.MCPTools", "%New")
    seeds = o.classMethodObject("%DynamicArray", "%FromJSON", '["a"]')
    out = json.loads(str(tool.invokeObject("PPRWalk", seeds, 2).invoke("%ToJSON")))
    assert out["status"] == "OK", out
    assert [r["nodeId"] for r in out["results"]] == ["a", "b"]
    assert out["results"][0]["score"] == pytest.approx(FROM_A["a"], abs=1e-3)
