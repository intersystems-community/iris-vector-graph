"""4.6.0 R270, live: every personalized-PageRank route gives networkx's ``pagerank(G, alpha, personalization, tol)``.

``Graph.KG.PageRank.Run`` kept ``1 - damping`` of a dangling node's score and lost the rest, so the scores summed to
less than 1, and it stopped once no score moved by 1e-4 whatever ``tolerance`` said. It now hands a dangling node's
score to the seeds as networkx does, and stops on networkx's rule (summed change below N * tolerance, N the nodes
holding a score). The graph has two dangling nodes (e, f), a pair of edges a -> c under two predicates (weight 2 in
networkx's multigraph), and a node (g) the seeds never reach. ``test_keyed_parallel_edges`` adds a multigraph-mode
triple carrying two keyed edges (``"#"`` = 2).

Each route is asked with the graph and seeds, and checked against networkx's answer (computed here, not read from
IVG): the scores sum to 1, and each is within 1e-6. Routes with a tolerance argument are run at 1e-10; the SQL
function and ``CALL ivg.ppr`` have none, so they are checked to be RunJson's answer at its default tolerance, and
within that tolerance's reach of networkx's.

    IVG_NAMESPACE=IVG460FY4 IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \\
        pytest tests/e2e/test_460_r270_ppr_networkx_e2e.py
"""

from __future__ import annotations

import json
import os
import uuid
from unittest.mock import patch

import networkx as nx
import pytest
from networkx.algorithms.link_analysis.pagerank_alg import _pagerank_python

SKIP_IRIS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS, reason="SKIP_IRIS_TESTS=true")]

TOL = 1e-10
NODES = ["a", "b", "c", "d", "e", "f", "g"]
EDGES = [
    ("a", "KNOWS", "b"),
    ("a", "KNOWS", "c"),
    ("a", "LIKES", "c"),
    ("b", "KNOWS", "c"),
    ("b", "KNOWS", "e"),
    ("c", "KNOWS", "d"),
    ("c", "KNOWS", "f"),
    ("d", "KNOWS", "a"),
    ("g", "KNOWS", "a"),
]
CASES = {
    "one seed": dict(seeds=["a"]),
    "two seeds": dict(seeds=["a", "d"]),
    "a dangling seed": dict(seeds=["e"]),
    "a seed named twice": dict(seeds=["a", "a", "d"]),
}


def _networkx(seeds, edges=EDGES, damping=0.85, tol=TOL, reverse=False):
    g = nx.MultiDiGraph()
    g.add_nodes_from(NODES)
    for s, _p, o in edges:
        g.add_edge(s, o)
        if reverse:
            g.add_edge(o, s)
    return _pagerank_python(g, alpha=damping, personalization={s: 1 for s in seeds}, tol=tol, max_iter=10000)


def _check(got, want, prefix, abs_tol=1e-6):
    """``got`` is keyed by IVG node ID; compare on the unprefixed name."""
    plain = {k[len(prefix):]: float(v) for k, v in got.items() if str(k).startswith(prefix)}
    # the namespace's other nodes are in the answer at 0.0, as networkx answers every node (4.7.0 row 9)
    outside = {k: v for k, v in got.items() if not str(k).startswith(prefix)}
    assert all(float(v) == 0.0 for v in outside.values()), f"scores outside the test graph: {outside}"
    assert sum(plain.values()) == pytest.approx(1.0, abs=abs_tol), plain
    for node, score in want.items():
        assert plain.get(node, 0.0) == pytest.approx(score, abs=abs_tol), (node, plain, want)
    return plain


@pytest.fixture(scope="module")
def world(iris_connection):
    import iris

    from iris_vector_graph import IRISGraphEngine

    prefix = f"FIXES39_{uuid.uuid4().hex[:6]}_"
    eng = IRISGraphEngine(iris_connection, embedding_dimension=768)
    for n in NODES:
        eng.create_node(prefix + n)
    for s, p, o in EDGES:
        assert eng.create_edge(prefix + s, p, prefix + o)
    iris_connection.commit()
    try:
        yield eng, iris.createIRIS(iris_connection), prefix
    finally:
        eng.bulk_delete_nodes([prefix + n for n in NODES])
        iris_connection.commit()


@pytest.fixture
def no_arno(monkeypatch, iris_connection):
    """An engine that does not load the Arno library, so IVG's ObjectScript walk answers."""
    from iris_vector_graph import IRISGraphEngine

    monkeypatch.setenv("IVG_DISABLE_ARNO", "1")
    eng = IRISGraphEngine(iris_connection, embedding_dimension=768)
    assert eng._detect_arno() is False
    return eng


def _seeds(prefix, seeds):
    return [prefix + s for s in seeds]


def _runjson(o, prefix, seeds, *, tol=None, bidir=0, exclude="", graph=""):
    args = [json.dumps(_seeds(prefix, seeds)), 0.85, 1000, bidir, 1.0, graph, exclude, 0]
    if tol is not None:
        args.append(tol)
    return {r["id"]: r["score"] for r in json.loads(str(o.classMethodValue("Graph.KG.PageRank", "RunJson", *args)))}


# ------------------------------------------------------------------ the ObjectScript walk, Arno off


@pytest.mark.parametrize("case", CASES)
def test_objectscript_route(world, no_arno, case):
    _eng, _o, prefix = world
    seeds = CASES[case]["seeds"]
    got = no_arno.kg_PERSONALIZED_PAGERANK(_seeds(prefix, seeds), max_iterations=1000, tolerance=TOL)
    _check(got, _networkx(seeds), prefix)


def test_objectscript_route_bidirectional(world, no_arno):
    """At reverse weight 1 a reverse hop is an edge like any other: networkx on the graph plus every edge reversed."""
    _eng, _o, prefix = world
    got = no_arno.kg_PERSONALIZED_PAGERANK(
        _seeds(prefix, ["a"]), max_iterations=1000, tolerance=TOL, bidirectional=True
    )
    _check(got, _networkx(["a"], reverse=True), prefix)


def test_objectscript_route_exclusion_makes_c_dangling(world, no_arno):
    """Without KNOWS, a -LIKES-> c is the one edge left from a, and c, with none, hands its score back to a."""
    _eng, _o, prefix = world
    got = no_arno.kg_PERSONALIZED_PAGERANK(
        _seeds(prefix, ["a"]), max_iterations=1000, tolerance=TOL, exclude_predicates=["KNOWS"]
    )
    plain = _check(got, _networkx(["a"], edges=[e for e in EDGES if e[1] != "KNOWS"]), prefix)
    assert {k for k, v in plain.items() if v > 0} == {"a", "c"}
    assert set(plain) == set(NODES)  # the others at 0.0 (4.7.0 row 9)


def test_runjson_reads_tolerance(world):
    """A tolerance of 10 stops after the first iteration; 1e-10 runs to networkx's answer."""
    _eng, o, prefix = world
    loose = _runjson(o, prefix, ["a"], tol=10)
    one = {r["id"]: r["score"] for r in json.loads(str(o.classMethodValue(
        "Graph.KG.PageRank", "RunJson", json.dumps(_seeds(prefix, ["a"])), 0.85, 1, 0, 1.0, "", "", 0, TOL)))}
    assert loose == pytest.approx(one)
    assert loose != pytest.approx(_runjson(o, prefix, ["a"], tol=TOL), abs=1e-3)
    _check(_runjson(o, prefix, ["a"], tol=TOL), _networkx(["a"]), prefix)


def test_keyed_parallel_edges(world, no_arno):
    """A multigraph-mode triple with two keyed edges weighs 2, as two networkx edges do (Graph.KG.Adj.Count reads
    its ``"#"``, and the walk passes the share once per edge)."""
    eng, o, _prefix = world
    graph = f"fixes39_r270_mg_{uuid.uuid4().hex[:6]}"
    eng.set_multigraph(graph, True)
    try:
        for n in ("x", "y", "z"):
            eng.create_node(n, graph=graph)
        for s, dst in (("x", "y"), ("x", "y"), ("x", "z"), ("y", "x")):
            r = eng.execute_cypher(
                f"USE GRAPH '{graph}' MATCH (a {{node_id: '{s}'}}), (b {{node_id: '{dst}'}}) CREATE (a)-[:R]->(b)"
            )
            assert not r.error, r.error
        key = str(o.classMethodValue("Graph.KG.GraphKey", "ForIndex", graph))
        assert int(o.get("^KG", "out", key, "x", "R", "y", "#")) == 2
        g = nx.MultiDiGraph([("x", "y"), ("x", "y"), ("x", "z"), ("y", "x")])
        want = _pagerank_python(g, alpha=0.85, personalization={"x": 1}, tol=TOL, max_iter=10000)
        for got in (
            no_arno.kg_PERSONALIZED_PAGERANK(["x"], max_iterations=1000, tolerance=TOL, graph=graph),
            no_arno._kg_PERSONALIZED_PAGERANK_python_fallback(["x"], 0.85, 1000, TOL, graph=graph),
        ):
            _check(got, want, "")
    finally:
        eng.erase_graph(graph)


# ------------------------------------------------------------------ every other route


@pytest.mark.parametrize("case", CASES)
def test_store_with_arno_loaded(world, case):
    """The engine as it comes, Arno detected or not: the store answers (no Native Reader is proven for PPR)."""
    eng, _o, prefix = world
    seeds = CASES[case]["seeds"]
    _check(eng.kg_PERSONALIZED_PAGERANK(_seeds(prefix, seeds), max_iterations=1000, tolerance=TOL),
           _networkx(seeds), prefix)


@pytest.mark.parametrize("case", CASES)
def test_engine_fast_path(world, no_arno, case):
    """The engine's own RunJson call, taken when the store does not offer PPR."""
    _eng, _o, prefix = world
    seeds = CASES[case]["seeds"]
    no_arno._store_capabilities = dict(no_arno._store_capabilities, ppr=False)
    # what initialize_schema records on this namespace, whose classes are deployed and whose ^KG the writes keep
    no_arno.capabilities.objectscript_deployed = True
    no_arno.capabilities.kg_built = True
    # pinned: a failing RunJson call is logged and the Python fallback answers, which would pass this test too
    with patch.object(no_arno, "_kg_PERSONALIZED_PAGERANK_python_fallback", side_effect=AssertionError("fallback")):
        got = no_arno.kg_PERSONALIZED_PAGERANK(_seeds(prefix, seeds), max_iterations=1000, tolerance=TOL)
    _check(got, _networkx(seeds), prefix)


@pytest.mark.parametrize("case", CASES)
def test_python_fallback(world, case):
    eng, _o, prefix = world
    seeds = CASES[case]["seeds"]
    _check(eng._kg_PERSONALIZED_PAGERANK_python_fallback(_seeds(prefix, seeds), 0.85, 1000, TOL),
           _networkx(seeds), prefix)


def test_python_fallback_bidirectional(world):
    eng, _o, prefix = world
    got = eng._kg_PERSONALIZED_PAGERANK_python_fallback(_seeds(prefix, ["a"]), 0.85, 1000, TOL, None, True, 1.0)
    _check(got, _networkx(["a"], reverse=True), prefix)


def test_kg_pagerank_with_seeds(world, no_arno):
    _eng, _o, prefix = world
    got = no_arno.kg_PAGERANK(seed_entities=_seeds(prefix, ["a"]), max_iterations=1000)
    _check(got, _networkx(["a"], tol=1e-6), prefix, abs_tol=1e-4)


def test_sql_function_and_call(world):
    """No tolerance argument: RunJson's default (1e-6). The answer is RunJson's own at that default, and within that
    tolerance's reach of networkx's."""
    eng, o, prefix = world
    seeds = _seeds(prefix, ["a"])
    runjson = _runjson(o, prefix, ["a"])
    schema = eng._schema_prefix or "Graph_KG"
    cur = eng.conn.cursor()
    cur.execute(f"SELECT {schema}.kg_PPR(?, 0.85, 1000, 0, 1.0, '')", [json.dumps(seeds)])
    sql = {r["id"]: r["score"] for r in json.loads(cur.fetchone()[0])}
    cur.close()
    r = eng.execute_cypher(
        f"USE GRAPH '' CALL ivg.ppr({json.dumps(seeds)}, 0.85, 1000) YIELD node, score RETURN node, score"
    )
    assert not r.error, r.error
    node = r.columns.index("node_id") if "node_id" in r.columns else r.columns.index("node")
    call = {row[node]: row[r.columns.index("score")] for row in r.rows}
    # RunJson answers the nodes the walk scored; kg_PPR (so the CALL) adds the graph's others at 0.0 (4.7.0 row 9)
    zeros = {k: 0.0 for k in sql if k not in runjson}
    assert set(zeros) >= {prefix + "g"}, "g, which the seeds never reach, has a row"
    assert sql == pytest.approx({**runjson, **zeros}, abs=1e-12)
    assert call == pytest.approx({**runjson, **zeros}, abs=1e-12)
    _check(sql, _networkx(["a"]), prefix, abs_tol=1e-4)


@pytest.mark.parametrize("arno", [False, True], ids=["arno off", "arno as found"])
def test_engine_ppr(world, no_arno, arno):
    """``ppr(seed)``: one seed, top_k rows."""
    eng, _o, prefix = world
    engine = eng if arno else no_arno
    got = {d["id"]: d["score"] for d in engine.ppr(prefix + "a", max_iter=1000, top_k=100)["scores"]}
    _check(got, _networkx(["a"], tol=1e-6), prefix, abs_tol=1e-4)


def test_engine_ppr_with_nkg_built(world):
    """With the library loaded and ``^NKG`` built, ``ppr`` answered from ``NKGAccelTraversal.PPRNative``: a walk of
    the seed's 2-hop neighbourhood that passed a dangling node's score nowhere, ran every iteration and rounded to 6
    places (a = 0.216 where networkx gives 0.389 on a -> b -> {c, e}, c -> a). It is ``kg_PERSONALIZED_PAGERANK``'s
    answer now, as khop is BFSFastJson's whether or not the library is loaded (R095)."""
    from iris_vector_graph import IRISGraphEngine

    eng, o, prefix = world
    assert int(o.classMethodValue("Graph.KG.Traversal", "BuildNKG")) == 1
    try:
        engine = IRISGraphEngine(eng.conn, embedding_dimension=768)
        if not engine._detect_arno():
            pytest.skip("the Arno library is not loaded on this container")
        got = {d["id"]: d["score"] for d in engine.ppr(prefix + "a", max_iter=1000, top_k=100)["scores"]}
        _check(got, _networkx(["a"], tol=1e-6), prefix, abs_tol=1e-4)
    finally:
        o.classMethodVoid("Graph.KG.GraphIndex", "DropNKG")
