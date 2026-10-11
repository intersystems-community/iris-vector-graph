"""A failed statement is an error to the caller, and a statement with no parameters is sent with none.

Mindwalk (4.2.2, a populated 653K-node graph): ``MATCH (n) RETURN count(n) AS c`` through ``/api/cypher`` came back
``status OK, rows []`` while the server logged ``<PARAMETER ERROR> Invalid number of parameters``. ``execute_sql``
had caught the error into ``IVGResult.error`` and ``_run_cypher`` never looked at it. The statement itself was
sent as ``cursor.execute(sql, [])`` for a query with no placeholders.
"""

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.stores.iris_sql_store import IRISGraphStore


def _store(cursor):
    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = MagicMock()
    store.conn.cursor.return_value = cursor
    return store


def test_a_statement_without_parameters_is_executed_without_a_parameter_list():
    cursor = MagicMock()
    cursor.description = [("c",)]
    cursor.fetchall.return_value = [(7,)]

    result = _store(cursor).execute_sql("SELECT COUNT(*) AS c FROM Graph_KG.nodes", [])

    cursor.execute.assert_called_once_with("SELECT COUNT(*) AS c FROM Graph_KG.nodes")
    assert result.rows == [[7]]


def test_a_statement_with_parameters_still_gets_them():
    cursor = MagicMock()
    cursor.description = [("n",)]
    cursor.fetchall.return_value = []

    _store(cursor).execute_sql("SELECT 1 FROM t WHERE a = ?", ["x"])

    cursor.execute.assert_called_once_with("SELECT 1 FROM t WHERE a = ?", ["x"])


def test_the_cypher_api_turns_a_failed_statement_into_an_error():
    from iris_vector_graph import cypher_api
    from iris_vector_graph.stores.iris_sql_store import IVGResult

    class Engine:
        def execute_cypher(self, query, parameters=None):
            return IVGResult(columns=[], rows=[], error="<PARAMETER ERROR>; Details: Invalid number of parameters.")

    cypher_api._engine_cache = Engine()
    try:
        with pytest.raises(Exception) as caught:
            cypher_api._run_cypher("MATCH (n) RETURN count(n) AS c")
    finally:
        cypher_api._reset_engine()
    assert "Invalid number of parameters" in str(caught.value)
