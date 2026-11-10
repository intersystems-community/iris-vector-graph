"""4.6.0 R276: the ``except Exception:`` handlers that swallowed a failure on a path that writes or answers.

Each was ``except Exception: pass`` (or ``continue`` / ``return 0`` / ``return {}``) around a commit, a write, or a
read whose answer the caller takes as the truth. Each case below makes the one call fail on purpose (the fake
cursor or connection raises for the statement under test, everything else succeeds) and checks the call raises with
IRIS's error, or, where a failure has one benign meaning (a table or class not there), that only that meaning is
still answered quietly.

No IRIS: connections, cursors and the Native API are fakes. The live checks are
``tests/e2e/test_460_r276_except_audit_e2e.py``.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.errors import GraphWriteError
from iris_vector_graph.result import IVGResult

LOCK = "<SQL ERROR>; Details: [SQLCODE: <-110>:<Locking conflict in filing>]"
REFUSED = "<SQL ERROR>; Details: [SQLCODE: <-99>:<Privilege violation>]"
MISSING = "<SQL ERROR>; Details: [SQLCODE: <-30>:<Table or view not found>]"


class _Cursor:
    """Records each statement; raises ``error`` for the first statement matching ``fail``."""

    def __init__(self, fail=None, error=LOCK, rows=(), one=None):
        self.fail, self.error, self.rows, self.one = fail and re.compile(fail, re.I), error, list(rows), one
        self.sql, self.rowcount, self.description = [], 1, [("c", None)]
        #: what a transaction would leave: the writes committed, and those of the open transaction
        self.committed, self.pending, self.open = [], [], False

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if self.fail and self.fail.search(sql):
            raise RuntimeError(self.error)
        verb = sql.split()[0].upper()
        if verb == "START":
            self.open = True
        elif verb in ("COMMIT", "ROLLBACK"):
            if verb == "COMMIT":
                self.committed.extend(self.pending)
            self.pending, self.open = [], False
        elif verb in ("INSERT", "UPDATE", "DELETE"):
            (self.pending if self.open else self.committed).append((verb, tuple(params or ())))

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.one

    def close(self):
        pass


def _engine(cursor=None):
    conn = MagicMock()
    conn.cursor.return_value = MagicMock(fetchall=MagicMock(return_value=[]), fetchone=MagicMock(return_value=(0,)))
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    if cursor is not None:
        conn.cursor.return_value = cursor
    return eng


# ------------------------------------------------------------------ store: the commit before Graph.KG.Eraser


@pytest.mark.parametrize(
    "call",
    [
        lambda st: st.delete_nodes(["a"]),
        lambda st: st.delete_nodes_by_prefix("a"),
        lambda st: st.delete_edges([("a", "R", "b")]),
        lambda st: st.erase_bare_nodes(),
    ],
    ids=["delete_nodes", "delete_nodes_by_prefix", "delete_edges", "erase_bare_nodes"],
)
def test_a_refused_transaction_before_the_eraser_raises_and_erases_nothing(call):
    """The commit before the Eraser was ``except: pass`` (4.6.0 R276). Since 4.6.4 nothing is committed first: the
    Eraser runs inside IVG's own transaction (_write_txn), and a refused start of it raises before anything is
    erased."""
    st = _engine()._store

    def execute(sql, *a, **k):
        if sql == "START TRANSACTION":
            raise RuntimeError(LOCK)

    st.conn.cursor.return_value.execute.side_effect = execute
    with patch.object(st, "_has_eraser_method", return_value=True), patch.object(
        st, "_call_classmethod", side_effect=AssertionError("the Eraser ran")
    ), pytest.raises(GraphWriteError, match=r"SQLCODE -110"):
        call(st)


# ------------------------------------------------------------------ embeddings: the DELETE before the INSERT


def _embedding_engine(cursor):
    eng = _engine(cursor)
    for name, value in (
        ("_assert_node_exists", None),
        ("enforce_embedding_identity", None),
        ("_diagnose_vector_write", None),
    ):
        patch.object(eng, name, return_value=value).start()
    patch.object(eng, "_route_for_write", side_effect=lambda *a, **k: ("kg_NodeEmbeddings", None)).start()
    patch.object(eng, "_route_width", side_effect=lambda route, offered: offered).start()
    return eng


@pytest.fixture
def stop_patches():
    yield
    patch.stopall()


@pytest.mark.parametrize(
    "call",
    [
        lambda e: e.store_embedding("a", [1.0, 0, 0, 0]),
        lambda e: e.store_embeddings([{"node_id": "a", "embedding": [1.0, 0, 0, 0]}]),
        lambda e: e.store_edge_embedding("a", "R", "b", [1.0, 0, 0, 0]),
        lambda e: e.store_edge_embeddings([{"s": "a", "p": "R", "o_id": "b", "embedding": [1.0, 0, 0, 0]}]),
    ],
    ids=["store_embedding", "store_embeddings", "store_edge_embedding", "store_edge_embeddings"],
)
def test_a_refused_delete_of_the_old_vector_raises_before_the_insert(stop_patches, call):
    """Swallowed, the INSERT then ran: a duplicate row where the table has no unique key (legacy edge vectors), or a
    -119 that named the wrong statement."""
    cur = _Cursor(fail=r"^\s*DELETE")
    with pytest.raises(RuntimeError, match="-110"):
        call(_embedding_engine(cur))
    assert not any(s.lstrip().upper().startswith("INSERT") for s in cur.sql)


# ------------------------------------------------------------------ nodes and edges


def test_the_node_check_before_a_vector_write_raises_when_it_cannot_read():
    """It passed, so the write went ahead on a node it never found (the legacy vector tables have no FK)."""
    eng = _engine(_Cursor(fail=r"SELECT COUNT", error=REFUSED))
    with pytest.raises(RuntimeError, match=r"'a'.*-99"):
        eng._assert_node_exists("a")


def test_the_node_check_still_refuses_a_missing_node():
    eng = _engine(_Cursor(one=(0,)))
    with pytest.raises(ValueError, match="Node does not exist"):
        eng._assert_node_exists("a")


def test_nodes_exist_raises_when_a_lookup_fails():
    """A failed lookup left the ID out, so it read as a node that does not exist."""
    eng = _engine(_Cursor(fail=r"SELECT", error=REFUSED))
    with pytest.raises(RuntimeError, match="-99"):
        eng.nodes_exist(["a", "b"])


def _sql_path_engine(cursor, adjacency_indexed=False):
    eng = _engine(cursor)
    eng.capabilities = MagicMock(objectscript_deployed=False)
    eng._adjacency_indexed_cached = adjacency_indexed
    patch.object(eng, "_ensure_edge_endpoints").start()
    return eng


def test_bulk_ingest_edges_raises_on_an_insert_that_is_not_a_duplicate(stop_patches):
    """It fell through, wrote ^KG for the row that was not there, and counted it."""
    eng = _sql_path_engine(_Cursor(fail=r"INSERT INTO", error=REFUSED), adjacency_indexed=True)
    with pytest.raises(GraphWriteError, match=r"bulk_ingest_edges.*SQLCODE -99"):
        eng.bulk_ingest_edges([("a", "R", "b")], auto_sync=False)


def test_bulk_ingest_edges_still_skips_a_duplicate(stop_patches):
    dup = "<SQL ERROR>; Details: [SQLCODE: <-119>:<UNIQUE or PRIMARY KEY Constraint failed uniqueness check>]"
    eng = _sql_path_engine(_Cursor(fail=r"INSERT INTO", error=dup), adjacency_indexed=True)
    assert eng.bulk_ingest_edges([("a", "R", "b")], auto_sync=False) == 0


def test_bulk_ingest_edges_raises_when_its_adjacency_write_fails(stop_patches):
    """Without the adjacency index the edge's ^KG entry is written by hand; a failure left ^KG without it."""
    eng = _sql_path_engine(_Cursor())
    native = MagicMock()
    native.classMethodVoid.side_effect = RuntimeError("<PROTECT>WriteAdjacency+3^Graph.KG.EdgeScan.1 ^KG")
    with patch.object(eng, "_iris_obj", return_value=native), pytest.raises(GraphWriteError, match="PROTECT"):
        eng.bulk_ingest_edges([("a", "R", "b")], auto_sync=False)


def test_bulk_ingest_edges_without_the_classes_raises(stop_patches):
    """4.6.0 passed this one over ("no Graph.KG classes, no ^KG to keep"); 4.6.3: it is a half-finished setup, whose
    ^KG reads missed the edges once the classes compiled, so it raises too and says to finish setup."""
    eng = _sql_path_engine(_Cursor())
    native = MagicMock()
    native.classMethodVoid.side_effect = RuntimeError("<CLASS DOES NOT EXIST>XDCall+11^%SYS.DBSRV.1 *Graph.KG.EdgeScan")
    with patch.object(eng, "_iris_obj", return_value=native), \
            pytest.raises(GraphWriteError, match=r"initialize_schema\(\) again"):
        eng.bulk_ingest_edges([("a", "R", "b")], auto_sync=False)


def test_retract_inference_raises_on_a_refused_commit():
    eng = _engine(_Cursor())
    eng.conn.commit.side_effect = RuntimeError(LOCK)
    with pytest.raises(GraphWriteError, match=r"retract_inference.*SQLCODE -110"):
        eng.retract_inference()


# ------------------------------------------------------------------ import_rdf, temporal mirror


def test_import_rdf_raises_on_a_refused_batch_commit(tmp_path):
    pytest.importorskip("rdflib")
    nt = tmp_path / "x.nt"
    nt.write_text("<http://x/a> <http://x/knows> <http://x/b> .\n")
    # 4.7.0: each batch is IVG's own transaction (_write_txn; here, as tests/unit/conftest.py makes it, START
    # TRANSACTION ... COMMIT on the cursor), which nests in a caller's (conn.commit() committed the caller's work); a
    # refused commit of it still raises (R276), and leaves nothing of the batch
    cur = _Cursor(fail=r"^COMMIT$", error=LOCK)
    eng = _engine(cur)
    with pytest.raises(GraphWriteError, match=r"import_rdf.*SQLCODE -110"):
        eng.import_rdf(str(nt))
    assert cur.committed == [] and cur.pending == []


def test_the_temporal_mirror_commits_nothing_of_a_callers():
    """R276 raised on a refused commit here. 4.6.4: there is no commit: each mirror INSERT is atomic on its own, and
    `conn.commit()` ended a caller's open transaction."""
    eng = _engine(_Cursor())
    eng._adjacency_indexed_cached = False
    with patch.object(
        eng._store, "bulk_write_temporal_edges", return_value=IVGResult(columns=["n"], rows=[[1, 0, 0]])
    ), patch("iris_vector_graph._engine.temporal._mirror_node"):
        eng.bulk_create_edges_temporal([{"s": "a", "p": "R", "o": "b", "ts": 1}], graph="g1")
    eng.conn.commit.assert_not_called()


# ------------------------------------------------------------------ embedded connection


def test_an_embedded_commit_that_fails_raises():
    """``COMMIT`` with no transaction open succeeds in embedded IRIS (probed), so a failure is a failure."""
    from iris_vector_graph.embedded import EmbeddedConnection

    sql = MagicMock()
    sql.exec.side_effect = RuntimeError(LOCK)
    with pytest.raises(RuntimeError, match="-110"):
        EmbeddedConnection(iris_sql=sql).commit()


def test_an_embedded_rollback_that_fails_is_still_cleanup():
    from iris_vector_graph.embedded import EmbeddedConnection

    sql = MagicMock()
    sql.exec.side_effect = RuntimeError(LOCK)
    EmbeddedConnection(iris_sql=sql).rollback()


class _StatementResult:
    """A %SQL.StatementResult whose second ``%Next`` raises."""

    def __init__(self):
        self.calls = 0

    def _Next(self):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("<LIST ERROR>%Next+5^%SQL.StatementResult.1")
        return 1

    def _GetProperty(self, name):
        return 1

    def _GetData(self, i):
        return "row"


def test_an_embedded_result_that_fails_mid_read_raises():
    """The rows read so far were the answer: a truncated result read as a complete one."""
    from iris_vector_graph.embedded import _SqlStatementResultSet

    with pytest.raises(RuntimeError, match="LIST ERROR"):
        list(_SqlStatementResultSet(_StatementResult()))


def test_an_embedded_cursor_that_fails_mid_fetch_raises():
    from iris_vector_graph.embedded import EmbeddedCursor

    def rows():
        yield ("a",)
        raise RuntimeError("<LIST ERROR>")

    cur = EmbeddedCursor(iris_sql=MagicMock())
    cur._rs, cur._rows = rows(), None
    with pytest.raises(RuntimeError, match="LIST ERROR"):
        cur.fetchall()


# ------------------------------------------------------------------ ledger post-commit properties


def test_a_refused_post_commit_property_delete_is_the_reported_error():
    """It was passed over and the INSERT ran after it (a second value, or a -119 naming the INSERT)."""
    from iris_vector_graph.ledger.client import GraphLedger

    cur = _Cursor(fail=r"^\s*DELETE", error=REFUSED)
    ledger = MagicMock()
    ledger._engine.conn.cursor.return_value = cur
    ledger._schema.return_value = "Graph_KG"
    applied, error = GraphLedger._apply_post_commit_properties(ledger, {"a": {"k": "v"}}, "r1")
    assert applied is False and "-99" in error
    assert not any(s.lstrip().upper().startswith("INSERT") for s in cur.sql)
    # nothing of it is left, committed or pending (4.7.0: IVG's own transaction is taken back, not the connection's;
    # tests/e2e/test_470_ledger_caller_txn_e2e.py shows a caller's work surviving it)
    assert cur.committed == [] and cur.pending == [] and not cur.open


def test_post_commit_properties_are_written_and_committed():
    from iris_vector_graph.ledger.client import GraphLedger

    cur = _Cursor()
    ledger = MagicMock()
    ledger._engine.conn.cursor.return_value = cur
    ledger._schema.return_value = "Graph_KG"
    assert GraphLedger._apply_post_commit_properties(ledger, {"a": {"k": "v"}}, "r1") == (True, None)
    # the DELETE and the INSERT are committed together, nothing left pending (4.7.0: as IVG's own transaction, which
    # nests in a caller's: tests/e2e/test_470_ledger_caller_txn_e2e.py shows the caller's rollback taking them back)
    assert [verb for verb, _ in cur.committed] == ["DELETE", "INSERT"]
    assert cur.committed[1][1] == ("", "a", "k", "v")
    assert cur.pending == [] and not cur.open


# ------------------------------------------------------------------ reads that answer


def test_embedding_count_raises_when_it_cannot_count():
    """0 said "no vectors" for a count IRIS refused."""
    eng = _engine(_Cursor(fail=r"COUNT", error=REFUSED))
    with pytest.raises(RuntimeError, match="-99"):
        eng.embedding_count()


def test_embedding_count_of_a_table_that_is_not_there_is_zero():
    eng = _engine(_Cursor(fail=r"COUNT", error=MISSING))
    assert eng.embedding_count() == 0


def test_embed_queue_pending_raises_when_it_cannot_count():
    eng = _engine()
    with patch("iris_vector_graph.schema._call_classmethod", side_effect=RuntimeError("<PROTECT> ^EmbedQueue")):
        with pytest.raises(RuntimeError, match="PROTECT"):
            eng.embed_queue_pending()


def test_embed_queue_pending_without_the_queue_class_is_zero():
    eng = _engine()
    missing = RuntimeError("<CLASS DOES NOT EXIST>XDCall+11^%SYS.DBSRV.1 *Graph.KG.EmbedQueue")
    with patch("iris_vector_graph.schema._call_classmethod", side_effect=missing):
        assert eng.embed_queue_pending() == 0


def test_list_namespaces_raises_when_it_cannot_read():
    eng = _engine(_Cursor(fail=r"rdf_namespaces", error=REFUSED))
    with pytest.raises(RuntimeError, match="-99"):
        eng.list_namespaces()


def test_list_namespaces_without_the_table_is_empty():
    eng = _engine(_Cursor(fail=r"rdf_namespaces", error=MISSING))
    assert eng.list_namespaces() == {}


def test_subgraph_embeddings_raises_on_a_vector_it_cannot_read():
    eng = _engine(_Cursor(rows=[("a", "1,0"), ("b", None), ("c", "not a vector")]))
    with pytest.raises(ValueError, match="'c'"):
        eng._subgraph_embeddings(["a", "b", "c"])


def test_subgraph_embeddings_leaves_out_a_node_without_a_vector():
    eng = _engine(_Cursor(rows=[("a", "1,0"), ("b", None)]))
    assert eng._subgraph_embeddings(["a", "b"]) == {"a": [1.0, 0.0]}
