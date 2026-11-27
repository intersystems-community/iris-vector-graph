"""4.7.2: `bulk_create_edges` when the foreign key refuses every row of the batch.

`rdf_edges` has composite foreign keys onto `nodes (graph_id, node_id)`: an edge whose endpoint has no node row in
its own graph is refused (SQLCODE -121). `bulk_create_edges` writes the endpoint rows first, so an ordinary batch
never meets that; this makes it meet it, by leaving the endpoint step out (`_ensure_edge_endpoints` does nothing),
and asks what the call answers and what is left in the database.

An answer of the batch's size, or of any count, with no edge written is a success that did not happen.

    IVG_R472_NS=IVG470W3 pytest tests/e2e/test_472_bulk_edges_fk_e2e.py
"""

from __future__ import annotations

import os
import uuid

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R472_NS", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
    scratch_vars.requires("IVG_R472_NS"),
]


def _connect():
    import iris

    return iris.connect("localhost", rr.PORT, NS, "_SYSTEM", "SYS")


@pytest.fixture
def conn():
    c = _connect()
    yield c
    try:
        c.rollback()
    finally:
        c.close()


@pytest.fixture
def engine(conn):
    from iris_vector_graph import IRISGraphEngine

    return IRISGraphEngine(conn, embedding_dimension=rr.DIM)


def _edges_of(conn, u: str) -> int:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s %STARTSWITH ?", [u])
    return int(cur.fetchone()[0])


def _kg_out(conn, u: str, graph) -> bool:
    import iris

    n = iris.createIRIS(conn)
    return bool(n.isDefined("^KG", "out", 0 if graph is None else graph, f"{u}a0", "R472"))


def _batch(u: str, n: int, graph=None) -> list:
    return [dict(source_id=f"{u}a{i}", predicate="R472", target_id=f"{u}b{i}", **({"graph": graph} if graph else {}))
            for i in range(n)]


@pytest.mark.parametrize("graph", [None, "g472fk"])
@pytest.mark.parametrize("size", [1, 5])
def test_a_batch_every_row_of_which_the_foreign_key_refuses_does_not_answer_a_count(conn, engine, monkeypatch, graph,
                                                                                 size):
    u = f"fk472{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(type(engine), "_ensure_edge_endpoints", lambda self, cursor, pairs: None)
    answered = None
    raised = None
    try:
        answered = engine.bulk_create_edges(_batch(u, size, graph), graph=graph, auto_sync=False)
    except Exception as exc:  # noqa: BLE001 - the type is asserted below
        raised = exc

    assert _edges_of(conn, u) == 0, "the foreign key refused the rows, so none can be there"
    assert raised is not None, f"the call answered {answered!r} for a batch whose every edge was refused"
    # a single row or a named graph runs row by row and re-raises the driver's own refusal (SQLCODE -121); several
    # rows of the default graph go through executemany, which skips a refused row, and the read-back raises
    # GraphWriteError ("IRIS refused 5 edge row(s) of the batch")
    assert "121" in str(raised) or "refused" in str(raised), repr(raised)
    assert not _kg_out(conn, u, graph)


def test_a_batch_that_only_some_of_its_rows_the_foreign_key_refuses_writes_none_of_them(conn, engine, monkeypatch):
    u = f"fk472{uuid.uuid4().hex[:8]}"
    # two of the five edges have their endpoints; the other three do not
    assert engine.bulk_create_nodes([{"id": f"{u}a{i}"} for i in (0, 1)] + [{"id": f"{u}b{i}"} for i in (0, 1)])
    monkeypatch.setattr(type(engine), "_ensure_edge_endpoints", lambda self, cursor, pairs: None)
    with pytest.raises(Exception) as caught:
        engine.bulk_create_edges(_batch(u, 5), auto_sync=False)
    assert "121" in str(caught.value) or "refused" in str(caught.value), repr(caught.value)
    assert _edges_of(conn, u) == 0


def test_the_same_batch_with_its_endpoints_written_answers_its_size(conn, engine):
    u = f"fk472{uuid.uuid4().hex[:8]}"
    assert engine.bulk_create_edges(_batch(u, 5), auto_sync=False) == 5
    assert _edges_of(conn, u) == 5
