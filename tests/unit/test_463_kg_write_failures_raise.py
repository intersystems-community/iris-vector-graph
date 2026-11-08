"""4.6.3: an edge write whose ^KG part fails raises GraphWriteError naming that part; it never answers success.

Without the adjacency functional index (spec 252) the edge writers keep ^KG by hand through `Graph.KG.EdgeScan`. A
failed ^KG write or kill used to log `... ^KG write failed (BuildKG can recover)` at WARNING and answer True (or the
count), so on a namespace an interrupted setup left half built (`Graph.KG.EdgeScan` not compiled) `create_edge`
answered True for an edge the adjacency reads never see (KNOWN_ISSUES, 4.6.0 walkthrough). Each writer now makes the
^KG call before its COMMIT and rolls the rows back when it fails, so the error says nothing was written; where the rows
were committed first (retract_inference), the error says what stays.

No IRIS: the connection, cursor and Native API are fakes; the live case is
tests/e2e/test_463_half_setup_writes_e2e.py.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.errors import GraphWriteError

ABSENT = "<CLASS DOES NOT EXIST>XDCall+11^%SYS.DBSRV.1 *Graph.KG.EdgeScan"
PROTECT = "<PROTECT>WriteAdjacency+3^Graph.KG.EdgeScan.1 ^KG"


class _Cursor:
    """Records each statement; answers `rows` to fetchall and `one` to fetchone."""

    def __init__(self, rows=(), one=(0,)):
        self.rows, self.one = list(rows), one
        self.sql, self.rowcount, self.description = [], 1, [("c", None)]

    def execute(self, sql, params=None):
        self.sql.append(sql.strip().split()[0].upper() if sql.strip() else sql)

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.one

    def close(self):
        pass


def _engine(cursor, native_error=ABSENT):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    eng._adjacency_indexed_cached = False  # the writers keep ^KG by hand
    eng._ekey_guard_cached = ""
    native = MagicMock()
    native.classMethodVoid.side_effect = RuntimeError(native_error)
    native.classMethodValue.side_effect = RuntimeError(native_error)
    patch.object(eng, "_iris_obj", return_value=native).start()
    patch.object(eng, "_ensure_edge_endpoints").start()
    return eng, conn, native


@pytest.fixture(autouse=True)
def _stop_patches():
    yield
    patch.stopall()


def _no_commit(cursor, conn):
    """The writer opened its own transaction and rolled it back: neither the cursor's COMMIT statement nor the
    connection's commit() ran. A ROLLBACK statement, not conn.rollback(): the driver's connection autocommits, so
    conn.rollback() takes nothing back (the E2E saw delete_edge's row gone after it)."""
    assert "START" in cursor.sql and "ROLLBACK" in cursor.sql, cursor.sql
    assert "COMMIT" not in cursor.sql, cursor.sql
    conn.commit.assert_not_called()


@pytest.mark.parametrize("error", [ABSENT, PROTECT], ids=["class-absent", "protect"])
def test_create_edge_raises_and_commits_nothing(error, caplog):
    cur = _Cursor()
    eng, conn, _ = _engine(cur, error)
    with caplog.at_level(logging.WARNING), pytest.raises(GraphWriteError) as raised:
        eng.create_edge("a", "R", "b")
    message = str(raised.value)
    assert raised.value.operation == "create_edge"
    assert "^KG adjacency write (Graph.KG.EdgeScan.WriteAdjacency)" in message, message
    assert "nothing written" in message and error in message, message
    assert isinstance(raised.value.__cause__, RuntimeError)
    _no_commit(cur, conn)
    # before: WARNING "create_edge ^KG write failed (BuildKG can recover)" and True
    assert not [r for r in caplog.records if "BuildKG can recover" in r.getMessage()]


def test_a_class_absent_failure_says_to_finish_setup():
    eng, _, _ = _engine(_Cursor(), ABSENT)
    with pytest.raises(GraphWriteError, match=r"initialize_schema\(\) again"):
        eng.create_edge("a", "R", "b")


def test_a_protect_failure_does_not_blame_setup():
    eng, _, _ = _engine(_Cursor(), PROTECT)
    with pytest.raises(GraphWriteError) as raised:
        eng.create_edge("a", "R", "b")
    assert "initialize_schema" not in str(raised.value)


def test_create_edge_writes_kg_before_its_commit():
    """The order that lets a failure roll the row back: INSERT, then ^KG, then COMMIT."""
    cur = _Cursor()
    eng, conn, native = _engine(cur)
    order = []
    native.classMethodVoid.side_effect = lambda *a: order.append(("kg", list(cur.sql)))
    assert eng.create_edge("a", "R", "b") is True
    [(_, seen)] = order
    assert "INSERT" in seen and "COMMIT" not in seen, seen
    assert cur.sql[-1] == "COMMIT"


def test_set_edge_weight_raises_and_commits_nothing():
    cur = _Cursor(rows=[(1, '{"weight": "1.0"}')])
    eng, conn, _ = _engine(cur)
    with pytest.raises(GraphWriteError, match=r"set_edge_weight failed, its \^KG adjacency write"):
        eng.set_edge_weight("a", "R", "b", 2.0)
    _no_commit(cur, conn)


def test_delete_edge_raises_and_keeps_the_row():
    cur = _Cursor()
    eng, conn, _ = _engine(cur)
    with pytest.raises(GraphWriteError, match=r"delete_edge failed, its \^KG adjacency delete "
                                              r"\(Graph.KG.EdgeScan.DeleteAdjacency\)"):
        eng.delete_edge("a", "R", "b")
    _no_commit(cur, conn)


def test_delete_edge_all_graphs_raises_and_keeps_the_rows():
    cur = _Cursor()
    eng, conn, _ = _engine(cur)
    with pytest.raises(GraphWriteError, match=r"DeleteAdjacencyAllGraphs"):
        eng.delete_edge("a", "R", "b", all_graphs=True)
    _no_commit(cur, conn)


@pytest.mark.parametrize("error", [ABSENT, PROTECT], ids=["class-absent", "protect"])
def test_bulk_ingest_edges_raises_and_commits_nothing(error):
    """4.6.0 R276 passed a class-absent failure over ("no Graph.KG classes, no ^KG to keep"): that is the
    half-finished setup, whose reads use ^KG once the classes compile."""
    cur = _Cursor()
    eng, conn, _ = _engine(cur, error)
    eng.capabilities = MagicMock(objectscript_deployed=False)
    with pytest.raises(GraphWriteError, match=r"bulk_ingest_edges failed.*\^KG adjacency write"):
        eng.bulk_ingest_edges([("a", "R", "b")], auto_sync=False)
    _no_commit(cur, conn)


def test_materialize_inference_raises_and_commits_nothing():
    """subClassOf a -> b -> c infers a -> c; its ^KG write fails, and the transaction is rolled back."""
    cur = _Cursor(one=(0,))
    # _fetch_edges(subClassOf), (subPropertyOf), (rdf:type), (domain), (range), then the other predicates' edges
    answers = iter([[("a", "b"), ("b", "c")], [], [], [], [], []])
    cur.fetchall = lambda: next(answers, [])
    eng, conn, _ = _engine(cur)
    with pytest.raises(GraphWriteError, match=r"materialize_inference failed, its \^KG adjacency write.*nothing written"):
        eng.materialize_inference()
    assert "INSERT" in cur.sql and "COMMIT" not in cur.sql and "ROLLBACK" in cur.sql, cur.sql


def test_retract_inference_raises_naming_what_stays():
    """Its rows are committed before the ^KG deletes (each checks for an asserted row of the same triple), so the
    error says the rows are gone and which ^KG entries remain."""
    cur = _Cursor(rows=[("a", "R", "c")], one=(0,))
    eng, conn, _ = _engine(cur)
    with pytest.raises(GraphWriteError, match=r"retract_inference failed.*the 1 inferred rows are deleted; "
                                              r"the \^KG entries of up to 1 .*rebuild_kg\(\)"):
        eng.retract_inference()
