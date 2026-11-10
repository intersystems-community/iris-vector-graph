"""4.7.0 row 8, live: personalized PageRank refuses alpha outside (0, 1) and maxIter below 1.

``CALL ivg.ppr(['a'], 2.0, 5)`` answered b 22.0 and a -21.0: no route checked alpha or maxIter. Now each raises
``ValueError`` naming the argument (``alpha`` and ``maxIter`` in Cypher, as USER_GUIDE names them; the Python method's
own parameter names in Python), and the ObjectScript entries Python reaches (``Graph.KG.PageRank.RunJson`` and
``Run``, ``Graph.KG.ArnoAccel.PPRNativeJson``, the ``kg_PPR`` SQL function) throw.

Alpha 0.5 still answers a 0.6154 on 4.6.5's graph a -> b, a -> c, b -> c, worked by hand: c is dangling and hands its
score to the seed, so a = 0.5 + 0.5c, b = 0.25a, c = 0.25a + 0.5b = 0.375a, and a = 0.5 / 0.8125 = 0.61538.

    IVG_NAMESPACE=IVG470ALG IVG_PORT=31972 IVG_TEST_CONTAINER=ivg-iris-enterprise \\
        pytest tests/e2e/test_470_ppr_args_e2e.py
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463
from tests.e2e.ns470alg import call_scores, conn, load, native  # noqa: F401 - conn is a fixture

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

NODES = ["a", "b", "c"]
EDGES = [("a", "KNOWS", "b"), ("a", "KNOWS", "c"), ("b", "KNOWS", "c")]
A = 0.5 / 0.8125


@pytest.fixture(scope="module")
def engs(conn):  # noqa: F811
    return load(conn, NODES, EDGES)


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize(
    "args, named",
    [("2.0, 5", "alpha"), ("0.0, 20", "alpha"), ("1.0, 20", "alpha"), ("-0.5, 20", "alpha"),
     ("0.85, 0", "maxIter"), ("0.85, -3", "maxIter")],
)
def test_ivg_ppr_refuses_alpha_and_maxiter_out_of_range(engs, route, args, named):
    with pytest.raises(ValueError, match=rf"ivg\.ppr: {named} "):
        engs[route].execute_cypher(f"CALL ivg.ppr(['a'], {args}) YIELD node, score RETURN node, score", {},
                                   raise_on_error=True)


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_ivg_ppr_alpha_half_still_answers_the_hand_worked_score(engs, route):
    got, _n = call_scores(engs[route], "CALL ivg.ppr(['a'], 0.5, 100) YIELD node, score "
                                       "RETURN node.node_id AS id, score")
    assert got["a"] == pytest.approx(A, abs=1e-4)
    assert got["b"] == pytest.approx(0.25 * A, abs=1e-4)
    assert got["c"] == pytest.approx(0.375 * A, abs=1e-4)


@pytest.mark.parametrize("kw, named", [({"alpha": 2.0}, "alpha"), ({"alpha": 0.0}, "alpha"),
                                       ({"alpha": 1.0}, "alpha"), ({"max_iter": 0}, "max_iter")])
def test_ppr_method_refuses_out_of_range(engs, kw, named):
    with pytest.raises(ValueError, match=rf"\b{named}\b"):
        engs["default"].ppr("a", **kw)


@pytest.mark.parametrize("kw, named", [({"damping_factor": 2.0}, "damping_factor"),
                                       ({"damping_factor": -0.1}, "damping_factor"),
                                       ({"max_iterations": 0}, "max_iterations")])
def test_kg_personalized_pagerank_refuses_out_of_range(engs, kw, named):
    with pytest.raises(ValueError, match=rf"\b{named}\b"):
        engs["default"].kg_PERSONALIZED_PAGERANK(["a"], **kw)


def test_ppr_method_alpha_half_answers_the_hand_worked_score(engs):
    got = {d["id"]: d["score"] for d in engs["default"].ppr("a", alpha=0.5, max_iter=100)["scores"]}
    assert got["a"] == pytest.approx(A, abs=1e-4)


@pytest.mark.parametrize("cls, method, args, named", [
    ("Graph.KG.PageRank", "RunJson", ('["a"]', 2.0, 5), "alpha"),
    ("Graph.KG.PageRank", "RunJson", ('["a"]', 0, 5), "alpha"),
    ("Graph.KG.PageRank", "RunJson", ('["a"]', 0.85, 0), "maxIter"),
    ("Graph.KG.ArnoAccel", "PPRNativeJson", ('["a"]', 2.0, 5), "alpha"),
    ("Graph.KG.ArnoAccel", "PPRNativeJson", ('["a"]', 0.85, 0), "maxIter"),
])
def test_objectscript_entries_refuse_out_of_range(engs, conn, cls, method, args, named):  # noqa: F811
    """The class methods Python reaches throw, naming the argument, rather than walk with alpha 2.0."""
    with pytest.raises(RuntimeError, match=named):
        native(conn).classMethodValue(cls, method, *args)


def test_objectscript_run_alpha_half_answers_the_hand_worked_score(engs, conn):  # noqa: F811
    import json

    raw = native(conn).classMethodValue("Graph.KG.PageRank", "RunJson", '["a"]', 0.5, 100)
    got = {r["id"]: r["score"] for r in json.loads(str(raw))}
    assert got["a"] == pytest.approx(A, abs=1e-4)


def test_sql_function_refuses_alpha_two(engs, conn):  # noqa: F811
    schema = engs["default"]._schema_prefix or "Graph_KG"
    cur = conn.cursor()
    try:
        with pytest.raises(Exception, match="alpha"):
            cur.execute(f"SELECT {schema}.kg_PPR(?, 2.0, 5, 0, 1.0, '')", ['["a"]'])
            cur.fetchall()
    finally:
        cur.close()
