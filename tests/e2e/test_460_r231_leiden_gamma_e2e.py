"""4.6.0 R231, live: Leiden's `gamma` means one thing on every route, the one COMMUNITIES documents.

COMMUNITIES says: at `gamma=1.0` Leiden optimizes modularity; any other `gamma` uses the Constant Potts Model (CPM),
quality sum over communities of (edges inside - gamma * pairs of members), so a lower `gamma` gives fewer, larger
communities and any `gamma >= 1` on an unweighted graph gives singletons. Before, only the leidenalg routes did that.
The ObjectScript fallback (the route a fresh install takes) scaled modularity's expected-edges term by `gamma` (the
Reichardt-Bornholdt resolution), the networkx fallback passed `gamma` to Louvain's `resolution`, the same thing, and
Arno was never checked.

The bar is leidenalg's own CPM answer (`leidenalg.CPMVertexPartition`, resolution `gamma`), on the graph IVG
partitions (undirected, simple, unweighted): two 5-cliques joined by one edge must come back as the two cliques at 0.5
and as singletons at 2.0; on Zachary's karate club a route's CPM quality, scored by leidenalg, must reach leidenalg's
lowest over seeds 0-9 less `TOL`, and be singletons at 2.0. A route that cannot compute CPM refuses `gamma != 1.0`
(networkx's Louvain has no CPM; Arno's `kg_leiden_run` is asked only at 1.0) and the call is answered by one that can.

Routes: `Graph.KG.Communities.LeidenJson` (ObjectScript), `LeidenJsonPy` (leidenalg in IRIS's Python), Arno's
`kg_leiden_run`, this process's leidenalg over `^KG`, the networkx fallback (leidenalg hidden), the engine method with
and without `IVG_DISABLE_ARNO=1` (alone in the namespace, and with a named graph's edge, which sends it past `^NKG`),
and `CALL ivg.leiden`. Runs in an IVG460FX* scratch namespace, emptied first.
"""

from __future__ import annotations

import json
import os
import sys

import networkx as nx
import pytest

from tests.e2e import ns460alt as alt
from tests.e2e.test_460_r220_leiden_modularity_e2e import CLIQUE_A, CLIQUE_B, CLIQUES, KARATE, undirected

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

#: how far below leidenalg's lowest CPM quality over ten seeds a route may land (karate at 0.5 spans 26-28)
TOL = 2.0
GAMMAS = (0.5, 2.0)
GRAPHS = {"cliques": CLIQUES, "karate": KARATE}


def _igraph(g: nx.Graph):
    import igraph as ig

    nodes = sorted(g)
    idx = {n: i for i, n in enumerate(nodes)}
    return nodes, idx, ig.Graph(n=len(nodes), edges=[(idx[s], idx[t]) for s, t in g.edges()])


def cpm_quality(g: nx.Graph, parts: list, gamma: float) -> float:
    import leidenalg as la

    nodes, idx, G = _igraph(g)
    member = [0] * len(nodes)
    for c, p in enumerate(parts):
        for n in p:
            member[idx[n]] = c
    return la.CPMVertexPartition(G, initial_membership=member, resolution_parameter=gamma).quality()


def leidenalg_floor(g: nx.Graph, gamma: float) -> float:
    import leidenalg as la

    _, _, G = _igraph(g)
    return min(la.find_partition(G, la.CPMVertexPartition, resolution_parameter=gamma, seed=s).quality()
               for s in range(10))


def test_the_reference_by_hand():
    g = undirected(CLIQUES)
    # each clique: 10 edges, 10 pairs; leidenalg counts ordered pairs: 2 * (10 - 0.5 * 10) per clique
    assert cpm_quality(g, [set(CLIQUE_A), set(CLIQUE_B)], 0.5) == pytest.approx(20.0)
    assert leidenalg_floor(g, 0.5) == pytest.approx(20.0)
    assert leidenalg_floor(g, 2.0) == pytest.approx(0.0)


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    eng = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768)
    eng.initialize_schema()
    yield eng
    iris.createIRIS(conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    conn.commit()
    conn.close()


@pytest.fixture(scope="module", params=sorted(GRAPHS))
def loaded(request, engine):
    import iris

    edges = GRAPHS[request.param]
    iris.createIRIS(engine.conn).classMethodValue("Graph.KG.Eraser", "EraseAll")
    engine.conn.commit()
    engine.bulk_create_edges([{"source_id": s, "predicate": p, "target_id": t} for s, p, t, _ in edges])
    engine.conn.commit()
    engine.sync()
    return request.param, edges


def _native(engine):
    import iris

    return iris.createIRIS(engine.conn)


def _rows_from_json(raw: str) -> list:
    assert str(raw).startswith("OK:"), raw
    return json.loads(str(raw)[3:])


class Refused(Exception):
    pass


def _objectscript(engine, gamma):
    return _rows_from_json(
        _native(engine).classMethodValue("Graph.KG.Communities", "LeidenJson", 10, gamma, 0.0001, 0, 256, 42))


def _iris_python(engine, gamma):
    raw = str(_native(engine).classMethodValue("Graph.KG.Communities", "LeidenJsonPy", gamma, 0, 42))
    if raw == "PYUNAVAIL":
        pytest.skip("IRIS's embedded Python has no igraph/leidenalg in this container")
    return _rows_from_json(raw)


def _arno(engine, gamma):
    from iris_vector_graph.stores.arno_bridge import ArnoError, arno_available

    store = engine._store
    if not arno_available(engine.conn, schema=store._ivg_schema):
        pytest.skip("Arno is not loaded in this container")
    try:
        result = store._leiden_arno(10, gamma, 0.0001, 0, 256, 42)
    except ArnoError as e:
        if "gamma" in str(e):
            raise Refused(str(e))
        pytest.skip(f"Arno is not loaded: {e}")
    return [dict(zip(result.columns, r)) for r in result.rows]


def _python_process(engine, gamma):
    result = engine._store._leiden_lazykg(10, gamma, 0.0001, 0, 256, 42, None)
    assert result.error is None, result.error
    return [dict(zip(result.columns, r)) for r in result.rows]


def _networkx_fallback(engine, gamma):
    saved = sys.modules.get("leidenalg")
    sys.modules["leidenalg"] = None  # import leidenalg raises ImportError
    try:
        result = engine._store._leiden_lazykg(10, gamma, 0.0001, 0, 256, 42, None)
    finally:
        if saved is None:
            sys.modules.pop("leidenalg", None)
        else:
            sys.modules["leidenalg"] = saved
    if result.error:
        raise Refused(result.error)
    return [dict(zip(result.columns, r)) for r in result.rows]


def _engine_method(engine, gamma):
    return engine.leiden_communities(gamma=gamma, random_seed=42, top_k=0)


def _engine_past_nkg(engine, gamma):
    """A named graph's edge: the engine answers from Arno or this process, never ^NKG (R184)."""
    engine.create_edge("zz_a", "R", "zz_b", graph="r231_other")
    engine.conn.commit()
    try:
        assert engine._named_graph_with_edges() == "r231_other"
        return engine.leiden_communities(gamma=gamma, random_seed=42, top_k=0)
    finally:
        engine.erase_graph("r231_other")
        engine.conn.commit()
        engine.sync()  # ^NKG kept the erased graph's two nodes, which the ^NKG routes then partitioned


def _cypher(engine, gamma):
    r = engine.execute_cypher(
        f"CALL ivg.leiden({{gamma: {gamma}, randomSeed: 42}}) YIELD node, community RETURN node.node_id AS id, community"
    )
    assert r.error is None, r.error
    return [{"id": node_id, "community": c} for node_id, c in r.rows]


ROUTES = {
    "objectscript": _objectscript,
    "iris_python": _iris_python,
    "arno": _arno,
    "python_process": _python_process,
    "networkx_fallback": _networkx_fallback,
    "engine": _engine_method,
    "engine_past_nkg": _engine_past_nkg,
    "cypher": _cypher,
}
#: routes that cannot compute CPM and must refuse gamma != 1.0 rather than answer something else
MAY_REFUSE = {"arno", "networkx_fallback"}


def partition(rows) -> list:
    by: dict = {}
    for r in rows:
        by.setdefault(r["community"], set()).add(str(r["id"]))
    return list(by.values())


#: Every route with and without IVG_DISABLE_ARNO=1, but the Arno route itself only with Arno allowed: it calls the
#: library directly, so with Arno disabled there is no route to test (it skipped "Arno is not loaded in this container"
#: in the gate, where Arno is loaded).
ROUTE_CASES = [(r, d) for r in sorted(ROUTES) for d in ("0", "1") if not (r == "arno" and d == "1")]


@pytest.mark.parametrize("gamma", GAMMAS)
@pytest.mark.parametrize("route,disable_arno", ROUTE_CASES)
def test_each_route_is_leidenalgs_cpm(loaded, engine, route, gamma, disable_arno, monkeypatch):
    name, edges = loaded
    monkeypatch.setenv("IVG_DISABLE_ARNO", disable_arno)
    g = undirected(edges)
    try:
        parts = partition(ROUTES[route](engine, gamma))
    except Refused as e:
        assert route in MAY_REFUSE, f"{route} refused: {e}"
        assert "gamma" in str(e) and "Constant Potts" in str(e), str(e)
        return
    assert set().union(*parts) == set(g), "every node is assigned once"
    assert sum(len(p) for p in parts) == g.number_of_nodes()
    if gamma >= 1.0:
        assert all(len(p) == 1 for p in parts), f"{name}/{route}: CPM at {gamma} is singletons: {parts}"
        return
    if name == "cliques":
        assert sorted(sorted(p) for p in parts) == [CLIQUE_A, CLIQUE_B], parts
    q, floor = cpm_quality(g, parts, gamma), leidenalg_floor(g, gamma)
    assert q >= floor - TOL, f"{name}/{route}: CPM quality {q:.2f} < leidenalg's {floor:.2f} - {TOL}: {parts}"


def test_the_networkx_fallback_still_answers_modularity_at_one(loaded, engine):
    name, edges = loaded
    g = undirected(edges)
    parts = partition(_networkx_fallback(engine, 1.0))
    assert set().union(*parts) == set(g)
    if name == "cliques":
        assert sorted(sorted(p) for p in parts) == [CLIQUE_A, CLIQUE_B]
