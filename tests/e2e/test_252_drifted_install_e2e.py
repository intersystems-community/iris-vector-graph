"""Spec 252, T050: declaring the index on a drifted install repairs it, once, and says so.

An install from before the index can hold a row with no adjacency entry (a SQL or Cypher write) and an entry with
no row (a bypass). ensure_indexes declares the index, CREATE INDEX builds it from the rows, and both kinds of
drift are gone; a second call finds the index present and does nothing; sync() still works afterwards.

Drops idx_edges_adj in the test namespace (which empties ^KG("out"/"in")) and declares it again before it ends.
Run against a scratch namespace: `IVG_NAMESPACE=TCKJ`.
"""

from __future__ import annotations

import contextlib
import logging
import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema

from .adjacency_recount import differences

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


def _declare(conn):
    cur = conn.cursor()
    try:
        return GraphSchema.ensure_adjacency_index(cur)
    finally:
        cur.close()
        with contextlib.suppress(Exception):
            conn.commit()


@pytest.fixture
def unindexed(iris_connection, iris_master_cleanup):
    assert "skipped" not in _declare(iris_connection)
    cur = iris_connection.cursor()
    cur.execute("DROP INDEX Graph_KG.rdf_edges.idx_edges_adj")
    iris_connection.commit()
    yield iris_connection
    _declare(iris_connection)


def test_declaring_the_index_repairs_both_kinds_of_drift_once(unindexed, caplog):
    conn = unindexed
    engine = IRISGraphEngine(conn)
    assert not engine._adjacency_indexed
    t = uuid.uuid4().hex[:8]
    a, b, c, d = (f"dr_{t}_{i}" for i in range(4))
    for n in (a, b, c, d):
        engine.create_node(n, labels=["DR"])
    cur = conn.cursor()
    # a row with no entry: written by SQL, which nothing kept the adjacency current for
    cur.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, 'R', ?)", [a, b])
    conn.commit()
    # an entry with no row: a bypass
    native = engine._iris_obj()
    native.set("1", "^KG", "out", 0, c, "R", d)
    native.set("1", "^KG", "in", 0, d, "R", c)
    assert differences(conn, [a, b, c, d]) != []

    with caplog.at_level(logging.WARNING, logger="iris_vector_graph.schema"):
        status = _declare(conn)
    assert status["idx_edges_adj"] == "created"
    assert any("rebuild" in r.getMessage().lower() for r in caplog.records)
    assert differences(conn, [a, b, c, d]) == []

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="iris_vector_graph.schema"):
        assert _declare(conn)["idx_edges_adj"] == "present"
    assert not [r for r in caplog.records if r.levelname == "WARNING"]

    engine = IRISGraphEngine(conn)
    assert engine._adjacency_indexed
    engine.sync()
    assert differences(conn, [a, b, c, d]) == []
