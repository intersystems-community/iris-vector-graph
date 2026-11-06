"""4.6.0 R244, live: as a reader holding the database at ``R``, ``kg_GRAPH_WALK`` names the missing privilege, and
personalized PageRank and ``betweenness_centrality`` answer what they answer at ``RW``.

Both readers hold ADMIN_GUIDE §3's roles (``R``: "reading"; ``RW``: "reading, fully"). The graph is reading_role460's
seed, a -> b -> c -> d (KNOWS), synced so ``^NKG`` holds it. By hand:

* The walk stages its rows in ``^ArnoKG("bfs_r")``; at ``R`` IRIS refuses the write (<PROTECT>), which the Native API
  reported as a bare ``<THROW>``. At ``RW`` the walk from a answers (a, KNOWS, b, 1), (b, KNOWS, c, 2), (c, KNOWS, d, 3).
* PPR fills the degree cache ``^KG("degc")`` where it may; at ``R`` that store is refused and skipped (R050 round 2:
  it raised, here for d, the sink, which no sync caches), and PPR from a scores a first, at ``R`` as at ``RW``.
* Betweenness (directed, every source): b lies on the shortest paths a->c and a->d, c on a->d and b->d, so b and c
  score 2 and a and d 0. At ``RW`` the ObjectScript tier answered [b 2, c 2]; at ``R`` it cannot write its work area,
  the Python walk answered and listed a 0 and d 0 as well. Both answer [b 2, c 2].
* No probe node is left in ``^KG`` or ``^ArnoKG``.

    IVG_NAMESPACE=IVG460FXT pytest tests/e2e/test_460_r244_reader_algorithms_e2e.py
"""

from __future__ import annotations

import pytest

from tests.e2e import reading_role460 as rr

pytestmark = [pytest.mark.e2e, *rr.requires_namespace]

PREFIX = "FIXES29"
REFUSED = r"database resource at RW"


@pytest.fixture(scope="module")
def readers():
    from iris_vector_graph import IRISGraphEngine

    sysconn = rr.connect()
    owner = IRISGraphEngine(sysconn, embedding_dimension=rr.DIM)
    rr.seed(owner)
    owner.sync()
    out, conns = {}, []
    try:
        for db in ("R", "RW"):
            tag = f"R244{db}"
            user = rr.make_reader(tag, rr.reading_resources(db), prefix=PREFIX)
            conn = rr.connect(user, rr.PASSWORD)
            conns.append(conn)
            out[db] = IRISGraphEngine(conn, embedding_dimension=rr.DIM)
        yield owner, sysconn, out
    finally:
        for conn in conns:
            conn.close()
        for db in ("R", "RW"):
            rr.drop_reader(f"R244{db}", PREFIX)
        owner.erase_graph(None)
        sysconn.close()


def _no_probe_left(sysconn):
    import iris

    nat = iris.createIRIS(sysconn)
    return (int(nat.isDefined("^KG", "ivg_write_probe") or 0), int(nat.isDefined("^ArnoKG", "ivg_write_probe") or 0))


@pytest.mark.parametrize("call", ["ppr", "kg_PERSONALIZED_PAGERANK"])
def test_ppr_at_r_answers_what_rw_answers(readers, call):
    owner, sysconn, r = readers
    before = rr.state(sysconn)
    run = ((lambda e: {x["id"]: x["score"] for x in e.ppr("a")["scores"]}) if call == "ppr"
           else (lambda e: e.kg_PERSONALIZED_PAGERANK(["a"])))
    at_r = run(r["R"])
    assert max(at_r, key=at_r.get) == "a" and set(at_r) == {"a", "b", "c", "d"}
    assert at_r == pytest.approx(run(r["RW"]))
    assert rr.state(sysconn) == before and _no_probe_left(sysconn) == (0, 0)


def test_graph_walk_at_r_names_the_privilege(readers):
    _owner, sysconn, r = readers
    with pytest.raises(RuntimeError, match=REFUSED) as raised:
        r["R"].kg_GRAPH_WALK("a")
    assert '^ArnoKG("bfs_r")' in str(raised.value)
    assert _no_probe_left(sysconn) == (0, 0)


def test_at_rw_both_answer(readers):
    _owner, sysconn, r = readers
    scores = r["RW"].kg_PERSONALIZED_PAGERANK(["a"])
    assert max(scores, key=scores.get) == "a" and set(scores) == {"a", "b", "c", "d"}
    assert r["RW"].kg_GRAPH_WALK("a") == [("a", "KNOWS", "b", 1), ("b", "KNOWS", "c", 2), ("c", "KNOWS", "d", 3)]
    assert _no_probe_left(sysconn) == (0, 0)


@pytest.mark.parametrize("db", ["R", "RW"])
def test_betweenness_is_one_shape(readers, db):
    owner, _sysconn, r = readers
    expected = [{"id": "b", "score": 2.0}, {"id": "c", "score": 2.0}]
    assert r[db].betweenness_centrality() == expected
    assert owner.betweenness_centrality() == expected
