"""4.7.2: temporal writes are a transaction of IVG's own, and a refused batch is taken back before its retry.

The live proof, with a caller's rollback and the globals read from a second connection, is
tests/e2e/test_472_temporal_rollback_e2e.py. These cases pin the Python half without a container: which levels the
write opens, and that an error undoes them. The cursor answers the `Txn_*` functions with the level they would.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from iris_vector_graph import GraphWriteError
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult
from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

TEMPORAL_CLS = (Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG" / "TemporalIndex.cls").read_text()


def _conn():
    """A connection whose cursor answers Txn_Begin with the next level and logs every Txn_ call and every other write."""
    conn = MagicMock()
    cur = conn.cursor.return_value
    log = []
    state = {"level": 0}

    def execute(sql, params=None):
        text = str(sql)
        if "Txn_Begin" in text:
            state["level"] += 1
            log.append(("begin", state["level"]))
            cur.fetchone.return_value = (state["level"],)
        elif "Txn_End" in text:
            level = params[0]
            state["level"] = level - 1
            log.append(("end", level))
            cur.fetchone.return_value = (state["level"],)
        elif "Txn_Undo" in text:
            level = params[0]
            state["level"] = level - 1
            log.append(("undo", level))
            cur.fetchone.return_value = (state["level"],)
        else:
            log.append(("sql", text))

    cur.execute.side_effect = execute
    return conn, log


def _engine(store):
    conn, log = _conn()
    e = IRISGraphEngine.__new__(IRISGraphEngine)
    e.conn = conn
    e._schema_prefix = "Graph_KG"
    e._in_bulk_load = False
    e._adjacency_indexed_cached = True
    e._store = store
    return e, log


def test_create_edge_temporal_is_one_level_that_commits():
    store = MagicMock()
    store.write_temporal_edge.return_value = IVGResult(columns=[], rows=[])
    e, log = _engine(store)
    assert e.create_edge_temporal("a", "P", "b", timestamp=1000) is True
    assert [x for x in log if x[0] != "sql"] == [("begin", 1), ("end", 1)]


def test_create_edge_temporal_refused_is_undone_and_says_nothing_was_written():
    store = MagicMock()
    store.write_temporal_edge.return_value = IVGResult(columns=[], rows=[], error="<THROW> refused")
    e, log = _engine(store)
    with pytest.raises(GraphWriteError) as raised:
        e.create_edge_temporal("a", "P", "b", timestamp=1000)
    assert [x for x in log if x[0] != "sql"] == [("begin", 1), ("undo", 1)]
    assert "nothing written" in str(raised.value)


def test_bulk_create_edges_temporal_refused_is_undone_whatever_part_of_it_was_written():
    store = MagicMock()
    store.bulk_write_temporal_edges.return_value = IVGResult(columns=["inserted"], rows=[[1]], error="<THROW> refused")
    e, log = _engine(store)
    with pytest.raises(GraphWriteError) as raised:
        e.bulk_create_edges_temporal([{"s": "a", "p": "P", "o": "b", "ts": 1}, {"s": "c", "p": "P", "o": "d", "ts": 2}])
    assert [x for x in log if x[0] != "sql"] == [("begin", 1), ("undo", 1)]
    assert "nothing written" in str(raised.value) and "1 of 2" not in str(raised.value)


def test_delete_edge_temporal_is_one_level():
    store = MagicMock()
    store.delete_temporal_edge.return_value = True
    e, log = _engine(store)
    e._adjacency_indexed_cached = True
    e._temporal_rows_cached = False
    assert e.delete_edge_temporal("a", "P", "b", 1000) is True
    assert [x for x in log if x[0] != "sql"] == [("begin", 1), ("end", 1)]


def _store(first_call_raises: bool):
    conn, log = _conn()
    s = IRISGraphStore.__new__(IRISGraphStore)
    s.conn = conn
    calls = []

    def call(cls, method, *args):
        calls.append((method, len(log)))
        if method == "BulkInsertCounts":
            if first_call_raises:
                raise RuntimeError("<THROW> AssertTemporalRow: refused")
            return "2:0:0"
        # the per-edge retry
        return "1"

    s._call_classmethod = call
    return s, log, calls


_EDGES = [{"source": "a", "predicate": "P", "target": "b", "timestamp": 1, "weight": 1.0},
          {"source": "c", "predicate": "P", "target": "d", "timestamp": 2, "weight": 1.0}]


def test_a_batch_the_server_refuses_is_undone_before_the_per_edge_retry_starts():
    store, log, calls = _store(first_call_raises=True)
    result = store.bulk_write_temporal_edges(_EDGES)
    kinds = [x[0] for x in log if x[0] != "sql"]
    # the batch's level is begun and undone; then each retried edge is a level of its own, committed
    assert kinds[:2] == ["begin", "undo"]
    assert kinds[2:] == ["begin", "end", "begin", "end"]
    # nothing of the first call is in the store when the retry's first write runs
    first_retry = [c for c in calls if c[0] == "InsertEdge"][0]
    undo_at = next(i for i, x in enumerate(log) if x[0] == "undo")
    assert first_retry[1] > undo_at
    assert result.error is None and result.rows == [[2]]


def test_a_batch_that_the_server_writes_is_one_level_that_commits():
    store, log, calls = _store(first_call_raises=False)
    result = store.bulk_write_temporal_edges(_EDGES)
    assert [x[0] for x in log if x[0] != "sql"] == ["begin", "end"]
    assert result.rows == [[2, 0, 0]]


def test_budget_a_batch_costs_one_transaction_pair_at_each_layer_whatever_its_size():
    """The transaction's calls per operation are a ceiling: one begin and one end for the engine's level and for the
    store's, per call and not per edge (each is a SQL round trip, 0.16 ms measured on IRIS 2026.3)."""
    edges = [{"source": f"s{i}", "predicate": "P", "target": f"t{i}", "timestamp": i, "weight": 1.0} for i in range(1000)]
    store, log, _ = _store(first_call_raises=False)
    store.bulk_write_temporal_edges(edges)
    assert [x[0] for x in log if x[0] != "sql"] == ["begin", "end"]

    inner = MagicMock()
    inner.bulk_write_temporal_edges.return_value = IVGResult(columns=["inserted", "collisions", "skipped"],
                                                             rows=[[1000, 0, 0]])
    e, elog = _engine(inner)
    e.bulk_create_edges_temporal([{"s": f"s{i}", "p": "P", "o": f"t{i}", "ts": i} for i in range(1000)])
    assert [x[0] for x in elog if x[0] != "sql"] == ["begin", "end"]


class TestTheObjectScriptShape:
    """What the globals do under a rollback is the live file's to prove; these keep the class from drifting back."""

    def _body(self, name: str) -> str:
        m = re.search(rf"^ClassMethod {name}\(.*?^\}}", TEMPORAL_CLS, re.M | re.S)
        assert m, name
        return m.group(0)

    def test_the_counters_are_set_inside_a_transaction_and_incremented_outside_one(self):
        body = self._body("CountEdge")
        inside, outside = body.split("} Else {")
        assert "If $TLevel" in inside and "$Increment" not in inside
        assert 'Set ^KG("tagg", pGKey, pBucket, pS, pP, "count")' in inside
        assert '$Increment(^KG("bucket", pGKey, pBucket, pS))' in outside

    def test_nothing_else_increments_the_bucket_counters_of_a_write_or_a_delete(self):
        for name in ("WriteStores", "RemoveTemporalRow"):
            body = self._body(name)
            assert 'Increment(^KG("tagg"' not in body, name
            if name == "WriteStores":
                assert 'Increment(^KG("bucket"' not in body
        # a delete's decrement is a $Increment only outside a transaction
        remove = self._body("RemoveTemporalRow")
        assert remove.count("$Increment(") == 1 and remove.index("$Increment(") > remove.index("} Else {")

    def test_the_writers_lock_is_released_on_an_error_and_at_the_end(self):
        for name in ("WriteStores", "RemoveTemporalRow"):
            body = self._body(name)
            assert "Do ..LockBucket(" in body
            assert body.count('Lock -^KG("tagg"') == 2, name  # in the Catch, and after it
            assert "Catch tEx" in body
