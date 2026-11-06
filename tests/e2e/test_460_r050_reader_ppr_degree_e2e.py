"""4.6.0 R050 round 2 (expert walkthrough W1, W3), live: at database ``R`` personalized PageRank and degree centrality
answer whether or not the degree cache ``^KG("degc")`` is current, and ``ppr`` takes a list of seeds.

W1: ``Graph.KG.Adj.Degree`` stores a count in ``^KG("degc")`` whenever the entry is missing or older than the source's
generation: a node with no out-edge (never a cache entry after a sync), and a source the last write touched. At ``R``
IRIS refuses that store with ``<PROTECT>``. PPR then raised once the graph had been written to (the walkthrough's
"answers until the next write"), and ``degree_centrality`` answered only because the store fell back to a Python walk
over ``^KG`` when it saw ``%SYS.DBSRV`` in the error. Degree now answers its count when the store is refused.

The graph: reading_role460's a -> b -> c -> d (KNOWS), synced, then one more edge a -> d written by the owner, so
``a``'s cache entry is stale and ``d`` (no out-edge) has none. The expected scores are networkx's
``pagerank(G, alpha=0.85, personalization=...)`` on that graph, computed here from the edge list, not from IVG; the
out-degrees by hand: a 2, b 1, c 1, d 0, each over n - 1 = 3.

    IVG_NAMESPACE=IVG460FX61A pytest tests/e2e/test_460_r050_reader_ppr_degree_e2e.py
"""

from __future__ import annotations

import logging

import pytest

from tests.e2e import reading_role460 as rr

pytestmark = [pytest.mark.e2e, *rr.requires_namespace]

PREFIX = "IVG460FX61"
EDGES = [("a", "b"), ("b", "c"), ("c", "d"), ("a", "d")]


def networkx_ppr(seeds, iterations=1000):
    nx = pytest.importorskip("networkx")
    g = nx.MultiDiGraph()
    g.add_nodes_from("abcd")
    g.add_edges_from(EDGES)
    return nx.pagerank(g, alpha=0.85, personalization={s: 1 for s in seeds}, max_iter=iterations, tol=1e-10)


@pytest.fixture(scope="module")
def world():
    import iris

    from iris_vector_graph import IRISGraphEngine

    sysconn = rr.connect()
    owner = IRISGraphEngine(sysconn, embedding_dimension=rr.DIM)
    rr.seed(owner)
    owner.sync()
    assert owner.create_edge("a", "KNOWS", "d") is True  # after the sync: a's cache entry is stale
    nat = iris.createIRIS(sysconn)
    conns, readers = [], {}
    try:
        for db in ("R", "RW"):
            user = rr.make_reader(f"PPR{db}", rr.reading_resources(db), prefix=PREFIX)
            conn = rr.connect(user, rr.PASSWORD)
            conns.append(conn)
            readers[db] = IRISGraphEngine(conn, embedding_dimension=rr.DIM)
        yield owner, nat, readers
    finally:
        for conn in conns:
            conn.close()
        for db in ("R", "RW"):
            rr.drop_reader(f"PPR{db}", PREFIX)
        owner.erase_graph(None)
        sysconn.close()


def _degc(nat, node):
    """The degree cache's all-predicate entry for `node` in the default graph, as stored ("" when none)."""
    value = nat.get("^KG", "degc", 0, node, chr(1))
    return "" if value is None else value


def test_the_cache_is_stale_for_a_and_absent_for_d(world):
    _owner, nat, _readers = world
    gen = int(nat.get("^KG", "gen", 0, "a") or 0)
    assert _degc(nat, "d") == ""
    entry = _degc(nat, "a")
    assert entry == "" or not str(entry).startswith(str(gen)), "a's entry should predate the write"


@pytest.mark.parametrize("call", ["ppr", "kg_PERSONALIZED_PAGERANK"])
def test_ppr_at_r_answers_networkx_after_a_write(world, call):
    _owner, nat, readers = world
    expected = networkx_ppr(["a"])
    if call == "ppr":
        got = {row["id"]: row["score"] for row in readers["R"].ppr("a", max_iter=100)["scores"]}
    else:
        got = readers["R"].kg_PERSONALIZED_PAGERANK(["a"], max_iterations=100)
    assert set(got) == set(expected)
    for node, score in expected.items():
        assert got[node] == pytest.approx(score, abs=1e-5), (node, got, expected)
    assert _degc(nat, "d") == "", "the reader stored nothing"


def test_ppr_at_r_and_at_rw_agree(world):
    _owner, _nat, readers = world
    r = readers["R"].kg_PERSONALIZED_PAGERANK(["a"], max_iterations=100)
    rw = readers["RW"].kg_PERSONALIZED_PAGERANK(["a"], max_iterations=100)
    assert set(r) == set(rw)
    for node in r:
        assert r[node] == pytest.approx(rw[node], abs=1e-9)


def test_degree_centrality_at_r_answers_from_the_server_with_no_warning(world, caplog):
    _owner, _nat, readers = world
    with caplog.at_level(logging.INFO, logger="iris_vector_graph"):
        rows = readers["R"].degree_centrality()
    assert {r["id"]: r["degree"] for r in rows} == {"a": 2, "b": 1, "c": 1, "d": 0}
    assert {r["id"]: r["score"] for r in rows}["a"] == pytest.approx(2 / 3)
    said = [r.getMessage() for r in caplog.records]
    assert not [m for m in said if "gref" in m or "Bug S" in m], said
    assert not [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def test_graph_walk_at_r_still_names_its_work_area(world):
    _owner, _nat, readers = world
    with pytest.raises(RuntimeError, match=r'\^ArnoKG\("bfs_r"\)'):
        readers["R"].kg_GRAPH_WALK("a")


# ------------------------------------------------------------------------------------------------ W3: a list of seeds


@pytest.mark.parametrize("db", ["owner", "R"])
def test_ppr_takes_a_list_of_seeds(world, db, caplog):
    owner, _nat, readers = world
    engine = owner if db == "owner" else readers["R"]
    expected = networkx_ppr(["a", "c"])
    with caplog.at_level(logging.WARNING, logger="iris_vector_graph"):
        got = {row["id"]: row["score"] for row in engine.ppr(["a", "c"], max_iter=100)["scores"]}
    assert set(got) == set(expected)
    for node, score in expected.items():
        assert got[node] == pytest.approx(score, abs=1e-5), (node, got, expected)
    assert [r.getMessage() for r in caplog.records] == []


def test_ppr_of_a_one_seed_list_is_ppr_of_the_seed(world):
    owner, _nat, _readers = world
    assert owner.ppr(["a"]) == owner.ppr("a")
