"""4.6.0 R258, live: what each personalized-PageRank form does for a reader holding the database at ``R`` (fixes29).

ADMIN_GUIDE §3 said ``kg_PPR`` raises IRIS's ``<PROTECT>`` at ``R``, while ``CALL ivg.ppr`` (which runs ``kg_PPR``)
answered. ``Graph.KG.PageRank.RunJson`` reads ``^KG("out"/"in")`` and sorts in the process-private ``^||PPR``: it writes
no database global, so by hand both the SQL function and the CALL answer at ``R``. The Python ``ppr`` fills the degree
cache ``^KG("degc")`` first and raises (R244).

The graph is reading_role460's seed, a -> b -> c -> d (KNOWS). By hand, a walk from a only moves forward and loses
mass at each step, so the scores fall along the chain: a, b, c, d. Each form is also compared with the owner's
(``_SYSTEM``) answer, which is the "reading, fully" answer.

    IVG_NAMESPACE=IVG460FX32 pytest tests/e2e/test_460_r258_reader_ppr_forms_e2e.py
"""

from __future__ import annotations

import json

import pytest

from tests.e2e import reading_role460 as rr

pytestmark = [pytest.mark.e2e, *rr.requires_namespace]

PREFIX = "FIXES32"
SQL = f"SELECT {rr.SCHEMA}.kg_PPR(?, 0.85, 20, 0, 1.0, '')"
CALL = "CALL ivg.ppr(['a'], 0.85, 20) YIELD node, score RETURN node, score ORDER BY score DESC"


@pytest.fixture(scope="module")
def setup():
    from iris_vector_graph import IRISGraphEngine

    sysconn = rr.connect()
    owner = IRISGraphEngine(sysconn, embedding_dimension=rr.DIM)
    rr.seed(owner)
    owner.sync()
    user = rr.make_reader("R258R", rr.reading_resources("R"), prefix=PREFIX)
    conn = rr.connect(user, rr.PASSWORD)
    try:
        yield owner, sysconn, IRISGraphEngine(conn, embedding_dimension=rr.DIM), conn
    finally:
        conn.close()
        rr.drop_reader("R258R", PREFIX)
        owner.erase_graph(None)
        sysconn.close()


def _sql_scores(conn):
    cur = conn.cursor()
    cur.execute(SQL, [json.dumps(["a"])])
    return {r["id"]: r["score"] for r in json.loads(cur.fetchone()[0])}


def _call_scores(engine):
    r = engine.execute_cypher(CALL)
    assert not r.error, r.error
    node, score = r.columns.index("node_id"), r.columns.index("score")  # a node comes back as its _id/_labels/_props
    return {row[node]: row[score] for row in r.rows}


def _by_hand(scores):
    """a, b and c as worked by hand: nothing flows back into them, so each holds the teleport share 0.15 times 0.85 per
    step from a. d, the sink, also keeps what its handling of a dangling node gives it; only its being there is
    checked."""
    assert set(scores) == {"a", "b", "c", "d"}, scores
    assert scores["a"] == pytest.approx(0.15) and scores["b"] == pytest.approx(0.1275), scores
    assert scores["c"] == pytest.approx(0.108375) and scores["d"] > 0, scores


def test_python_ppr_raises_at_r(setup):
    _owner, _sysconn, reader, _conn = setup
    with pytest.raises(RuntimeError, match=r'\^KG\("degc"\)'):
        reader.ppr("a")


def test_the_sql_function_answers_at_r(setup):
    _owner, sysconn, _reader, conn = setup
    before = rr.state(sysconn)
    scores = _sql_scores(conn)
    _by_hand(scores)
    assert scores == _sql_scores(sysconn)
    assert rr.state(sysconn) == before


def test_the_call_answers_at_r(setup):
    owner, sysconn, reader, _conn = setup
    scores = _call_scores(reader)
    _by_hand(scores)
    assert scores == _call_scores(owner)
