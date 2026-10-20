"""Spec 253, FR-009: all-graphs reads are correlated on graph_id only where the namespace holds more than one graph.

`IRISGraphEngine._more_than_one_graph` reads MIN/MAX(graph_id). A DBAPI DataRow is unreadable once its cursor is
closed; the first version closed the cursor first, raised, and the translator's failure path then correlated every
namespace (the TCK's multi-join scenarios went from seconds to past the 900 s area timeout).
"""

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.engine import IRISGraphEngine


class _Row:
    def __init__(self, cursor, values):
        self._cursor, self._values = cursor, values

    def __getitem__(self, i):
        if self._cursor.closed:
            raise RuntimeError("DataRow is inaccessible and/or Cursor is closed")
        return self._values[i]


class _Cursor:
    def __init__(self, values):
        self.values, self.closed = values, False

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchone(self):
        return _Row(self, self.values)

    def close(self):
        self.closed = True


class _Conn:
    def __init__(self, values):
        self.values = values

    def cursor(self):
        return _Cursor(self.values)


def _engine(lo, hi):
    e = IRISGraphEngine.__new__(IRISGraphEngine)
    e.conn = _Conn((lo, hi))
    return e


def test_one_graph_is_not_many_even_though_the_row_dies_with_its_cursor():
    assert _engine("", "")._more_than_one_graph() is False


def test_two_graphs_are_many():
    assert _engine("", "g1")._more_than_one_graph() is True


def _translating_engine(many):
    from unittest.mock import MagicMock

    e = IRISGraphEngine(MagicMock())
    e._more_than_one_graph = lambda: many
    return e


def test_a_single_graph_namespace_gets_no_correlation():
    q = translate_to_sql(parse_query("MATCH (n:L)-[:R]->(m) RETURN m.name"), {}, engine=_translating_engine(False))
    assert ".graph_id = " not in q.sql


def test_a_many_graph_namespace_is_correlated():
    q = translate_to_sql(parse_query("MATCH (n:L)-[:R]->(m) RETURN m.name"), {}, engine=_translating_engine(True))
    assert ".graph_id = " in q.sql
