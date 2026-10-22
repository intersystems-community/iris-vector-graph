"""Spec 254 FR-003, the Python half: a batch reports written and collision counts separately.

The existing integer result keeps meaning "written": ``bulk_create_edges_temporal`` returns an ``int`` subclass
whose value is the written count and which carries ``.collisions`` and ``.skipped``. The store reads
``BulkInsertCounts``' ``"written:collisions:skipped"`` and still accepts a bare integer from an older class.

With the adjacency functional index on, ``Graph.KG.TemporalIndex.WriteStores`` asserts the marked structural row
itself (ADR-0009), so the Python mirror's three SQL statements per edge are skipped. The ObjectScript half (what a
collision leaves in the globals) is ``tests/e2e/test_254_collisions_e2e.py``.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from iris_vector_graph._engine.temporal import TemporalWriteCount
from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

REPO = Path(__file__).resolve().parents[2]
TEMPORAL_CLS = (REPO / "iris_src/src/Graph/KG/TemporalIndex.cls").read_text()

_EDGES = [{"s": "a", "p": "P", "o": "b", "ts": 1000, "w": 0.9}]
_STORE_EDGES = [{"source": "a", "predicate": "P", "target": "b", "timestamp": 1000, "weight": 0.9}]


def _store(classmethod_return):
    s = IRISGraphStore.__new__(IRISGraphStore)
    s._call_classmethod = MagicMock(return_value=classmethod_return)
    return s


def _engine(rows, indexed):
    from iris_vector_graph.engine import IRISGraphEngine

    e = IRISGraphEngine.__new__(IRISGraphEngine)
    e.conn = MagicMock()
    e._schema_prefix = "Graph_KG"
    e._in_bulk_load = False
    e._adjacency_indexed_cached = indexed
    store = MagicMock()
    result = MagicMock()
    result.rows = rows
    store.bulk_write_temporal_edges.return_value = result
    e._store = store
    return e, store


class TestTemporalWriteCount:
    def test_it_is_the_written_count(self):
        c = TemporalWriteCount(3, collisions=2, skipped=1)
        assert c == 3 and int(c) == 3 and isinstance(c, int)
        assert (c.written, c.collisions, c.skipped) == (3, 2, 1)
        assert c + 1 == 4 and c > 2

    def test_defaults_are_zero(self):
        c = TemporalWriteCount(5)
        assert (c.collisions, c.skipped) == (0, 0)

    def test_repr_names_the_counts(self):
        assert "collisions=2" in repr(TemporalWriteCount(1, collisions=2))


class TestTheStoreReadsTheCounts:
    def test_it_calls_bulk_insert_counts(self):
        store = _store("1:1:0")
        store.bulk_write_temporal_edges(_STORE_EDGES)
        args = store._call_classmethod.call_args[0]
        assert args[0] == "Graph.KG.TemporalIndex" and args[1] == "BulkInsertCounts"

    def test_written_collisions_skipped(self):
        res = _store("4:2:1").bulk_write_temporal_edges(_STORE_EDGES)
        assert res.columns == ["inserted", "collisions", "skipped"]
        assert res.rows == [[4, 2, 1]]

    def test_a_bare_integer_from_an_older_class_is_written_only(self):
        res = _store("7").bulk_write_temporal_edges(_STORE_EDGES)
        assert res.rows == [[7, 0, 0]]

    def test_an_empty_batch_is_all_zero(self):
        res = _store("x").bulk_write_temporal_edges([])
        assert res.rows == [[0, 0, 0]]


class TestTheEngineReportsThem:
    def test_collisions_reach_the_caller(self):
        engine, _ = _engine([[1, 1, 0]], indexed=True)
        n = engine.bulk_create_edges_temporal(_EDGES + _EDGES)
        assert isinstance(n, TemporalWriteCount)
        assert n == 1 and n.collisions == 1 and n.skipped == 0

    def test_a_one_column_result_has_no_collisions(self):
        engine, _ = _engine([[2]], indexed=True)
        n = engine.bulk_create_edges_temporal(_EDGES)
        assert n == 2 and n.collisions == 0


class TestThePythonMirror:
    def test_skipped_when_the_index_asserted_the_row(self):
        engine, _ = _engine([[1, 0, 0]], indexed=True)
        engine.bulk_create_edges_temporal(_EDGES, graph="g")
        engine.conn.cursor.return_value.execute.assert_not_called()

    def test_kept_without_the_index(self):
        engine, _ = _engine([[1, 0, 0]], indexed=False)
        engine.bulk_create_edges_temporal(_EDGES, graph="g")
        assert engine.conn.cursor.return_value.execute.called


def _method(name):
    m = re.search(rf"^ClassMethod {name}\(.*?^\}}", TEMPORAL_CLS, re.M | re.S)
    assert m, name
    return m.group(0)


class TestTheObjectScriptShape:
    def test_write_stores_tests_occupancy_before_any_set(self):
        body = _method("WriteStores")
        occupied = body.find('$Data(^KG("tout", pGKey, pTs, pS, pP, pO))')
        first_set = body.find("Set ^KG(")
        assert 0 <= occupied < first_set, "the collision check must come before the first store write"

    def test_bulk_insert_keeps_its_integer(self):
        assert re.search(r"^ClassMethod BulkInsert\(.*\) As %Integer$", TEMPORAL_CLS, re.M)
        assert "BulkInsertCounts(" in _method("BulkInsert")

    @pytest.mark.parametrize("purge", ["PurgeRawBefore", "PurgeBefore"])
    def test_both_purges_retire_rows_before_the_raw_kills(self, purge):
        body = _method(purge)
        retire = body.find("..RetireExpired(")
        kill = body.find('Kill ^KG("tout"')
        assert 0 <= retire < kill
