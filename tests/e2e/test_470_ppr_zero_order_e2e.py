"""4.7.0 row 9, live: the nodes personalized PageRank never reaches come after the scored ones, 0.0 each, in IRIS
collation, and a ``top_k`` cut reads only the first of them.

The graph is the default graph: s -> t, and eight nodes with no edge whose IDs mix numbers and text. The walk from s
scores s = 1 / 1.85 and t = 0.85 / 1.85 (t is dangling and hands its score back to s). The zeros' order is IRIS
collation as USER_GUIDE states it, written out here by hand: canonical numbers first by value (-1, 1.5, 9, 10), then
text by character code ('07', 'B', 'a', 'x'). The engine reads the zeros from the node table's (graph_id, node_id)
index in that order and, with a limit, stops once they fill it.

    IVG_NAMESPACE=IVG470ALG IVG_PORT=31972 IVG_TEST_CONTAINER=ivg-iris-enterprise \\
        pytest tests/e2e/test_470_ppr_zero_order_e2e.py
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.e2e import ns463
from tests.e2e.ns470alg import call_scores, conn, load  # noqa: F401 - conn is a fixture

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

LONERS = ["10", "9", "07", "a", "B", "-1", "1.5", "x"]
#: by hand, IRIS collation: numbers by value, then text by character code
ZEROS = ["-1", "1.5", "9", "10", "07", "B", "a", "x"]
SCORED = {"s": 1 / 1.85, "t": 0.85 / 1.85}


@pytest.fixture(scope="module")
def engs(conn):  # noqa: F811
    return load(conn, ["s", "t"] + LONERS, [("s", "R", "t")])


def _routes(engs, monkeypatch):
    """Each Python route by name: the store, the engine's own RunJson call (fallback pinned), the Python fallback."""
    from iris_vector_graph import IRISGraphEngine

    eng = engs["default"]
    monkeypatch.setenv("IVG_DISABLE_ARNO", "1")
    fast = IRISGraphEngine(eng.conn, namespace=ns463.NS, schema_prefix=eng._schema_prefix)
    fast._store_capabilities = dict(fast._store_capabilities, ppr=False)
    fast.capabilities.objectscript_deployed = True
    fast.capabilities.kg_built = True

    def fast_path(**kw):
        with patch.object(fast, "_kg_PERSONALIZED_PAGERANK_python_fallback", side_effect=AssertionError("fallback")):
            return fast.kg_PERSONALIZED_PAGERANK(["s"], **kw)

    return {
        "store": lambda **kw: eng.kg_PERSONALIZED_PAGERANK(["s"], **kw),
        "RunJson": fast_path,
        "fallback": lambda **kw: eng._kg_PERSONALIZED_PAGERANK_python_fallback(
            ["s"], 0.85, 100, 1e-6, kw.get("return_top_k")),
    }


@pytest.mark.parametrize("route", ["store", "RunJson", "fallback"])
def test_every_node_in_order(engs, monkeypatch, route):
    got = _routes(engs, monkeypatch)[route]()
    assert list(got) == ["s", "t"] + ZEROS
    assert got == pytest.approx({**SCORED, **{z: 0.0 for z in ZEROS}}, abs=1e-4)


@pytest.mark.parametrize("route", ["store", "RunJson", "fallback"])
@pytest.mark.parametrize("top_k, want", [(1, ["s"]), (2, ["s", "t"]), (4, ["s", "t", "-1", "1.5"]),
                                          (7, ["s", "t", "-1", "1.5", "9", "10", "07"])])
def test_top_k_keeps_the_first_zeros_in_collation(engs, monkeypatch, route, top_k, want):
    got = _routes(engs, monkeypatch)[route](return_top_k=top_k)
    assert list(got) == want
    assert all(got[z] == 0.0 for z in want[2:])


def test_ppr_method_lists_the_zeros_in_collation(engs):
    scores = engs["default"].ppr("s", max_iter=100, top_k=6)["scores"]
    assert [d["id"] for d in scores] == ["s", "t", "-1", "1.5", "9", "10"]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_call_ivg_ppr_answers_the_loners_at_zero(engs, route):
    got, n = call_scores(engs[route], "CALL ivg.ppr(['s'], 0.85, 100) YIELD node, score "
                                      "RETURN node.node_id AS id, score")
    assert n == 10
    assert got == pytest.approx({**SCORED, **{z: 0.0 for z in ZEROS}}, abs=1e-4)


def test_kg_ppr_sql_function_orders_the_zeros_in_collation(engs, conn):  # noqa: F811
    """The JSON the SQL function answers, before any SQL reorders it: the scored rows, then the zeros in order."""
    import json

    schema = engs["default"]._schema_prefix or "Graph_KG"
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT {schema}.kg_PPR(?, 0.85, 100, 0, 1.0, '')", ['["s"]'])
        rows = json.loads(cur.fetchone()[0])
    finally:
        cur.close()
    assert [r["id"] for r in rows] == ["s", "t"] + ZEROS
