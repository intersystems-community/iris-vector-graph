"""4.7.2: a purge that does not retire nodes sends the class the arguments an older class takes.

4.6.5 sent `PurgeRawBefore(graph, tsEnd, tsStart)` when `retire_bare_nodes` was off ("a deployed class older than the
option never sees a fourth"); 4.7.0 made it default on and always sent the fourth argument, so a namespace whose classes
predate 4.5.0 (no `pRetireNodes`) failed with <PARAMETER> even with `retire_bare_nodes=False`. A class with the
parameter takes 0 as its default, so leaving the argument out is the same answer. The mechanism, measured on IRIS
2026.3: a class method of three parameters called with four raises `<PARAMETER>`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from iris_vector_graph._engine.temporal import TemporalMixin
from iris_vector_graph.stores.iris_sql_store import IRISGraphStore


def _store(answer="3:0"):
    store = IRISGraphStore.__new__(IRISGraphStore)
    store._call_classmethod = MagicMock(return_value=answer)
    return store


def test_purge_raw_before_without_retiring_sends_three_arguments():
    store = _store()
    store.purge_raw_before(500, ts_start=100, retire_bare_nodes=False)
    store._call_classmethod.assert_called_once_with("Graph.KG.TemporalIndex", "PurgeRawBefore", "", "500", "100")


def test_purge_raw_before_retiring_sends_the_fourth():
    store = _store("3:0:2")
    got = store.purge_raw_before(500, retire_bare_nodes=True)
    store._call_classmethod.assert_called_once_with("Graph.KG.TemporalIndex", "PurgeRawBefore", "", "500", "0", 1)
    assert (got.deleted, got.skipped, got.nodes_retired) == (3, 0, 2)


def _engine_with_native(answer=2):
    engine = TemporalMixin.__new__(TemporalMixin)
    native = MagicMock()
    native.classMethodValue.return_value = answer
    engine._iris_obj = lambda: native
    return engine, native


def test_purge_before_without_retiring_sends_the_timestamp_alone():
    engine, native = _engine_with_native()
    assert engine.purge_before(500, retire_bare_nodes=False) == 2
    native.classMethodValue.assert_called_once_with("Graph.KG.TemporalIndex", "PurgeBefore", 500)


def test_purge_before_retiring_sends_the_flag():
    engine, native = _engine_with_native()
    engine.purge_before(500)
    native.classMethodValue.assert_called_once_with("Graph.KG.TemporalIndex", "PurgeBefore", 500, 1)
