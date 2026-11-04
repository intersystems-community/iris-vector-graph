"""4.6.0 R257, live: betweenness's ObjectScript tier failing for a reader at database ``R`` says so before the Python
walk answers.

The readers hold ADMIN_GUIDE §3's roles (``R``: "reading"; ``RW``: "reading, fully"). The graph is reading_role460's
seed, a -> b -> c -> d (KNOWS), synced. By hand (as R244 worked it): b and c score 2, a and d 0, so both readers
answer [b 2, c 2]. At ``R`` the tier (Graph.KG.NKGAccel.BetweennessGlobal) cannot write its work area; before R257 the
Python walk answered with no word. Now a WARNING names the tier and its error. At ``RW`` the tier answers and nothing
is logged.

    IVG_NAMESPACE=IVG460FX32 pytest tests/e2e/test_460_r257_silent_fallbacks_e2e.py
"""

from __future__ import annotations

import logging

import pytest

from tests.e2e import reading_role460 as rr

pytestmark = [pytest.mark.e2e, *rr.requires_namespace]

PREFIX = "FIXES32"
EXPECTED = [{"id": "b", "score": 2.0}, {"id": "c", "score": 2.0}]
LOGGER = "iris_vector_graph.stores.iris_sql_store"


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
            user = rr.make_reader(f"R257{db}", rr.reading_resources(db), prefix=PREFIX)
            conn = rr.connect(user, rr.PASSWORD)
            conns.append(conn)
            out[db] = IRISGraphEngine(conn, embedding_dimension=rr.DIM)
        yield out
    finally:
        for conn in conns:
            conn.close()
        for db in ("R", "RW"):
            rr.drop_reader(f"R257{db}", PREFIX)
        owner.erase_graph(None)
        sysconn.close()


def _tier_warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and "BetweennessGlobal" in r.getMessage()]


def test_at_r_the_failed_tier_is_named_before_the_python_walk(readers, caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert readers["R"].betweenness_centrality() == EXPECTED
    (warned,) = _tier_warnings(caplog)
    assert "ObjectScript tier" in warned and "Python walk" in warned, warned
    assert "<PROTECT>" in warned or "PROTECT" in warned.upper(), warned


def test_at_rw_the_tier_answers_without_a_word(readers, caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert readers["RW"].betweenness_centrality() == EXPECTED
    assert _tier_warnings(caplog) == []
