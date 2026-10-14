"""`execute_cypher(raise_on_error=True)` turns a SQL error result into an exception.

`execute_sql` logs and returns IVGResult(error=...), which callers saw as an empty
result. The default stays (the TCK harness reads `IVGResult.error` itself).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.result import IVGResult

_MATCH = "MATCH (n:Person) RETURN n.node_id"


class _Store:
    _schema_prefix = "Graph_KG"

    def __init__(self, result):
        self.result = result

    def execute_sql(self, sql, params=None):
        return self.result


def _engine(result):
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine._store = _Store(result)
    engine._nkg_dirty = False
    engine.conn = MagicMock()
    engine._table_mapping_cache = {}
    engine._store_capabilities = {"native_sql": True}
    engine._probe_if_idle = lambda: None
    return engine


def test_default_returns_the_error_result_without_raising():
    failed = IVGResult(columns=[], rows=[], error="SQLCODE -30 table not found")
    result = _engine(failed).execute_cypher(_MATCH)
    assert result.error == "SQLCODE -30 table not found"


def test_raise_on_error_raises_with_the_message():
    failed = IVGResult(columns=[], rows=[], error="SQLCODE -30 table not found")
    with pytest.raises(RuntimeError, match="SQLCODE -30 table not found"):
        _engine(failed).execute_cypher(_MATCH, raise_on_error=True)


def test_success_is_unchanged_with_raise_on_error():
    ok = IVGResult(columns=["n.node_id"], rows=[["a"], ["b"]])
    result = _engine(ok).execute_cypher(_MATCH, raise_on_error=True)
    assert result.rows == [["a"], ["b"]]
    assert not getattr(result, "error", None)


def test_the_sub_engine_forwards_the_keyword():
    from iris_vector_graph.engine import _CypherSubEngine

    failed = IVGResult(columns=[], rows=[], error="boom")
    with pytest.raises(RuntimeError, match="boom"):
        _CypherSubEngine(_engine(failed)).execute_cypher(_MATCH, raise_on_error=True)


def test_a_query_with_a_with_clause_is_checked():
    failed = IVGResult(columns=[], rows=[], error="with boom")
    query = "MATCH (n:Person) WITH n.node_id AS i RETURN i"
    with pytest.raises(RuntimeError, match="with boom"):
        _engine(failed).execute_cypher(query, raise_on_error=True)
