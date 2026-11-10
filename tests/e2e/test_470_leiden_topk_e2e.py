"""4.7.0 row 28, live: Leiden's ``topK`` counts rows on every route.

The first route (``Graph.KG.Communities.LeidenJsonAuto``: leidenalg in IRIS's embedded Python, else Louvain in
ObjectScript) kept whole communities: it added communities from the largest down and stopped after the one that
brought the rows to ``topK``, so ``top_k=1`` answered a community's every row. On three cliques of 4, 3 and 2 nodes
(docs/COMMUNITIES.md's example) the answer for ``top_k=1``, ``2`` and ``5`` was 4, 4 and 7 rows; it is now 1, 2 and 5,
the first rows in the same order (community 0, the 4-clique, first), and ``top_k=0`` still answers all 9.

    IVG_NAMESPACE=IVG470ALG IVG_PORT=31972 IVG_TEST_CONTAINER=ivg-iris-enterprise \\
        pytest tests/e2e/test_470_leiden_topk_e2e.py
"""

from __future__ import annotations

import json

import pytest

from tests.e2e import ns463
from tests.e2e.ns470alg import conn, load, native  # noqa: F401 - conn is a fixture

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

CLIQUE0 = {f"k0_{i}" for i in range(4)}


def _cliques(*sizes):
    nodes, edges = [], []
    for ci, size in enumerate(sizes):
        members = [f"k{ci}_{i}" for i in range(size)]
        nodes += members
        edges += [(members[i], "R", members[j]) for i in range(size) for j in range(i + 1, size)]
    return nodes, edges


@pytest.fixture(scope="module")
def engs(conn):  # noqa: F811
    return load(conn, *_cliques(4, 3, 2))


def _check(rows, n):
    """``n`` rows, the 4-clique's first."""
    assert len(rows) == n, rows
    assert {r[0] for r in rows[: min(n, 4)]} <= CLIQUE0, rows


@pytest.mark.parametrize("top_k, rows", [(1, 1), (2, 2), (5, 5), (0, 9)])
def test_leiden_communities_top_k_counts_rows(engs, top_k, rows):
    out = [r for r in engs["default"].leiden_communities(top_k=top_k, random_seed=1) if "id" in r]
    _check([[r["id"], r["community"]] for r in out], rows)


@pytest.mark.parametrize("method", ["LeidenJson", "LeidenJsonAuto"])
@pytest.mark.parametrize("top_k, rows", [(1, 1), (5, 5)])
def test_server_routes_cut_at_top_k_rows(engs, conn, method, top_k, rows):  # noqa: F811
    raw = str(native(conn).classMethodValue("Graph.KG.Communities", method, 10, 1.0, 0.0001, top_k, 256, 1))
    assert raw.startswith("OK:"), raw[:200]
    _check([[r["id"], r["community"]] for r in json.loads(raw[3:])], rows)


@pytest.mark.parametrize("top_k, rows", [(1, 1), (5, 5)])
def test_embedded_leidenalg_route_cuts_at_top_k_rows(engs, conn, top_k, rows):  # noqa: F811
    raw = str(native(conn).classMethodValue("Graph.KG.Communities", "LeidenJsonPy", 1.0, top_k, 1))
    if raw == "PYUNAVAIL":
        pytest.skip("igraph and leidenalg are not installed in this IRIS's embedded Python")
    assert raw.startswith("OK:"), raw[:200]
    _check([[r["id"], r["community"]] for r in json.loads(raw[3:])], rows)


@pytest.mark.parametrize("top_k, rows", [(1, 1), (5, 5)])
def test_python_route_cuts_at_top_k_rows(engs, top_k, rows):
    _check(engs["default"]._store._leiden_lazykg(10, 1.0, 1e-4, top_k, 256, 1, None).rows, rows)


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("top_k, rows", [(1, 1), (5, 5)])
def test_call_ivg_leiden_top_k_counts_rows(engs, route, top_k, rows):
    r = engs[route].execute_cypher(f"CALL ivg.leiden({{topK: {top_k}, randomSeed: 1}}) YIELD node, community "
                                   f"RETURN node, community", {}, raise_on_error=True)
    _check(r.rows, rows)
