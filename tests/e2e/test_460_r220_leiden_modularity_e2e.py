"""4.6.0 R220, live: every route Leiden takes returns a partition as good as networkx's Louvain.

The walkthrough (walkthrough 2, W9) measured `leiden_communities()` at modularity -0.012 on its 10-node graph where
networkx's Louvain got 0.204. Two things were behind it:

- The walkthrough computed modularity with networkx's default, `weight="weight"`, and its edges carry weights 1-5.
  IVG's Leiden ignores edge weights on every route (Arno's adjacency upload has none, like GDS Louvain without
  `relationshipWeightProperty`) and treats the graph as undirected and simple. Measured unweighted, the same
  partition scores 0.202, networkx Louvain's own unweighted answer. COMMUNITIES now says so.
- The in-IRIS ObjectScript fallback, the route a fresh install takes (IRIS's Python has no igraph and Arno is not
  loaded), ran Louvain's local-moving phase only and never aggregated communities into nodes for a second level. On
  Zachary's karate club it stopped at 0.361 where Louvain reaches 0.385-0.419.

The bar is networkx: Louvain (`weight=None`) on `nx.Graph(G.to_undirected())`, the graph IVG partitions, at seeds 0-9.
IVG's partition, scored by networkx the same way, must reach Louvain's lowest score less `TOL`. Two 5-cliques joined by
one edge must come back as the two cliques.

Routes: `Graph.KG.Communities.LeidenJson` (ObjectScript), `LeidenJsonPy` (leidenalg in IRIS's Python, skipped where
IRIS's Python lacks igraph), Arno's `kg_leiden_run` (skipped where Arno is not loaded), the Python process's leidenalg
over `^KG`, the engine method with and without `IVG_DISABLE_ARNO=1`, and `CALL ivg.leiden`.

Runs in an IVG460FX* scratch namespace (scripts/dev/make_scratch_namespace.py), emptied first.
"""

from __future__ import annotations

import json
import os

import networkx as nx
import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

#: how far below networkx Louvain's lowest score over ten seeds a partition may land
TOL = 0.01

#: the walkthrough's graph (~/.cache/ivg-bench/novice-460/python/work/mygraph.py): KNOWS with weights, WORKS_AT, HAS_SKILL
WALKTHROUGH = (
    [("ann", "KNOWS", "ben", 1.0), ("ben", "KNOWS", "cat", 2.0), ("cat", "KNOWS", "dan", 1.0),
     ("ann", "KNOWS", "cat", 5.0), ("dan", "KNOWS", "eve", 1.0), ("eve", "KNOWS", "fay", 1.0),
     ("fay", "KNOWS", "ann", 3.0), ("ben", "KNOWS", "dan", 4.0)]
    + [(s, "WORKS_AT", t, 1.0) for s, t in
       [("ann", "globex"), ("ben", "globex"), ("cat", "initech"), ("dan", "initech"), ("eve", "globex")]]
    + [(s, "HAS_SKILL", t, 1.0) for s, t in
       [("ann", "python"), ("ben", "python"), ("cat", "cypher"), ("fay", "python"), ("fay", "cypher")]]
)

CLIQUE_A = ["a1", "a2", "a3", "a4", "a5"]
CLIQUE_B = ["b1", "b2", "b3", "b4", "b5"]
CLIQUES = (
    [(s, "LINKS", t, 1.0) for i, s in enumerate(CLIQUE_A) for t in CLIQUE_A[i + 1:]]
    + [(s, "LINKS", t, 1.0) for i, s in enumerate(CLIQUE_B) for t in CLIQUE_B[i + 1:]]
    + [("a5", "LINKS", "b1", 1.0)]
)

KARATE = [(f"k{u}", "LINKS", f"k{v}", 1.0) for u, v in sorted(nx.karate_club_graph().edges())]

GRAPHS = {"walkthrough": WALKTHROUGH, "cliques": CLIQUES, "karate": KARATE}


def undirected(edges) -> nx.Graph:
    """The graph IVG partitions: undirected, simple, unweighted."""
    g = nx.Graph()
    g.add_edges_from((s, t) for s, _, t, _ in edges)
    return g


def louvain_floor(g: nx.Graph) -> float:
    return min(
        nx.community.modularity(g, nx.community.louvain_communities(g, seed=s, weight=None), weight=None)
        for s in range(10)
    )


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
    for s, p, t, w in edges:
        engine.create_edge(s, p, t, weight=w)
    engine.conn.commit()
    engine.sync()
    return request.param, edges


def _native(engine):
    import iris

    return iris.createIRIS(engine.conn)


def _rows_from_json(raw: str) -> list:
    assert str(raw).startswith("OK:"), raw
    return json.loads(str(raw)[3:])


def _objectscript(engine):
    return _rows_from_json(
        _native(engine).classMethodValue("Graph.KG.Communities", "LeidenJson", 10, 1.0, 0.0001, 0, 256, 42)
    )


def _iris_python(engine):
    raw = str(_native(engine).classMethodValue("Graph.KG.Communities", "LeidenJsonPy", 1.0, 0, 42))
    if raw == "PYUNAVAIL":
        pytest.skip("IRIS's embedded Python has no igraph/leidenalg in this container")
    return _rows_from_json(raw)


def _arno(engine):
    from iris_vector_graph.stores.arno_bridge import ArnoError, arno_available

    store = engine._store
    if os.environ.get("IVG_DISABLE_ARNO") == "1" or not arno_available(engine.conn, schema=store._ivg_schema):
        pytest.skip("Arno is not loaded in this container")
    try:
        result = store._leiden_arno(10, 1.0, 0.0001, 0, 256, 42)
    except ArnoError as e:
        pytest.skip(f"Arno is not loaded: {e}")
    return [dict(zip(result.columns, r)) for r in result.rows]


def _python_process(engine):
    pytest.importorskip("leidenalg")  # the [communities] extra; networkx's Louvain answers without it
    result = engine._store._leiden_lazykg(10, 1.0, 0.0001, 0, 256, 42, None)
    assert result.error is None, result.error
    return [dict(zip(result.columns, r)) for r in result.rows]


def _engine_method(engine):
    return engine.leiden_communities(random_seed=42, top_k=0)


def _cypher(engine):
    r = engine.execute_cypher(
        "CALL ivg.leiden({randomSeed: 42}) YIELD node, community RETURN node.node_id AS id, community"
    )
    assert r.error is None, r.error
    return [{"id": node_id, "community": c} for node_id, c in r.rows]


ROUTES = {
    "objectscript": _objectscript,
    "iris_python": _iris_python,
    "arno": _arno,
    "python_process": _python_process,
    "engine": _engine_method,
    "cypher": _cypher,
}


def partition(rows) -> list:
    by: dict = {}
    for r in rows:
        by.setdefault(r["community"], set()).add(str(r["id"]))
    return list(by.values())


@pytest.mark.parametrize("disable_arno", ["0", "1"])
@pytest.mark.parametrize("route", sorted(ROUTES))
def test_each_route_scores_at_least_louvain(loaded, engine, route, disable_arno, monkeypatch):
    name, edges = loaded
    monkeypatch.setenv("IVG_DISABLE_ARNO", disable_arno)
    g = undirected(edges)
    parts = partition(ROUTES[route](engine))
    assert set().union(*parts) == set(g), "every node is assigned once"
    assert sum(len(p) for p in parts) == g.number_of_nodes()
    q = nx.community.modularity(g, parts, weight=None)
    floor = louvain_floor(g)
    assert q >= floor - TOL, f"{name}/{route}: modularity {q:.4f} < Louvain's {floor:.4f} - {TOL}: {parts}"
    if name == "cliques":
        assert sorted(sorted(p) for p in parts) == [CLIQUE_A, CLIQUE_B]
