"""The small Python sites of the degree-counter removal (spec 249): the namespace probe and `backfill_degp`.

* `IRISGraphStore._check_namespace` asked `$Data(^KG("deg"))` to learn whether the namespace holds a graph. With no
  counters that probe reads an empty tree on a perfectly good graph and warns about a missing mapping; it asks for
  `^KG("out")` now.
* `engine.backfill_degp()` rebuilt the per-predicate counter on the server. Nothing is stored, so there is nothing to
  rebuild: it is a deprecated no-op that makes no server call.
"""

import warnings
from unittest.mock import MagicMock

import pytest


def _store():
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    conn = MagicMock()
    del conn.namespace
    return IRISGraphStore(conn, namespace="USER"), conn


def test_the_namespace_probe_asks_whether_out_is_populated():
    store, conn = _store()
    cursor = MagicMock()
    cursor.fetchone.return_value = (10,)
    conn.cursor.return_value = cursor

    store._check_namespace()

    sql = [c.args[0] for c in cursor.execute.call_args_list]
    assert sql == ['SELECT $Data(^KG("out"))'], sql


def test_the_namespace_probe_does_not_mention_the_counters():
    store, conn = _store()
    cursor = MagicMock()
    cursor.fetchone.return_value = (10,)
    conn.cursor.return_value = cursor

    store._check_namespace()

    assert all("deg" not in c.args[0] for c in cursor.execute.call_args_list)


def _engine():
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(MagicMock(), embedding_dimension=4)


def test_backfill_degp_is_a_deprecated_no_op_that_calls_no_server_method():
    engine = _engine()
    native = MagicMock()
    engine._iris_obj = MagicMock(return_value=native)

    with pytest.warns(DeprecationWarning, match="degp"):
        result = engine.backfill_degp()

    assert result == 0
    native.classMethodValue.assert_not_called()
    native.classMethodVoid.assert_not_called()


def test_backfill_degp_still_returns_an_int_for_old_callers():
    engine = _engine()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert isinstance(engine.backfill_degp(), int)
