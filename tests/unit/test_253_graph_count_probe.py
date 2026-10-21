"""Spec 253, FR-009: all-graphs reads are correlated on graph_id only where the namespace holds more than one graph.

`IRISGraphEngine._more_than_one_graph` is asked per translation. A DBAPI DataRow is unreadable once its cursor is
closed; the first version closed the cursor first, raised, and the translator's failure path then correlated every
namespace (the TCK's multi-join scenarios went from seconds to past the 900 s area timeout). It read MIN/MAX(graph_id),
which IRIS answers by reading every row: 90 ms a translation on DRKG's 97,238 nodes, most of a 97 ms shortest path.
It now reads one row's graph and asks the (graph_id, node_id) index for a graph above it and one below (0.4 ms);
a NULL graph id (a DDL-built legacy table) keeps the MIN/MAX read, which skips NULLs.
"""

import re

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
    """Graph_KG.nodes as its graph_id column, answering the probe's statements."""

    def __init__(self, conn):
        self.conn, self.closed, self.rows = conn, False, []

    def execute(self, sql, params=None):
        self.conn.statements.append(sql)
        gids = self.conn.gids
        if "MIN(graph_id)" in sql:
            present = [g for g in gids if g is not None]
            self.rows = [(min(present), max(present)) if present else (None, None)]
            return
        m = re.search(r"WHERE graph_id ([<>]) \?", sql)
        if m:
            op, ref = m.group(1), params[0]
            self.rows = [(g,) for g in gids if g is not None and (g > ref if op == ">" else g < ref)][:1]
        else:
            self.rows = [(g,) for g in gids][:1]

    def fetchone(self):
        return _Row(self, self.rows[0]) if self.rows else None

    def close(self):
        self.closed = True


class _Conn:
    def __init__(self, gids):
        self.gids, self.statements = gids, []

    def cursor(self):
        return _Cursor(self)


def _engine(*gids):
    e = IRISGraphEngine.__new__(IRISGraphEngine)
    e.conn = _Conn(list(gids))
    return e


def test_one_graph_is_not_many_even_though_the_row_dies_with_its_cursor():
    assert _engine("", "", "")._more_than_one_graph() is False


def test_two_graphs_are_many():
    assert _engine("", "g1")._more_than_one_graph() is True


def test_the_other_graph_may_sort_before_the_first_row_s():
    assert _engine("g1", "g1", "")._more_than_one_graph() is True


def test_an_empty_table_is_one_graph():
    assert _engine()._more_than_one_graph() is False


def test_the_probe_never_reads_every_row():
    e = _engine("", "g1", "g2")
    e._more_than_one_graph()
    assert not any("MIN(" in sql for sql in e.conn.statements), e.conn.statements


def test_a_null_graph_id_keeps_the_min_max_read():
    # a DDL-built legacy table: graph_id is nullable, and NULL compares as nothing
    assert _engine(None, "g1")._more_than_one_graph() is False
    assert _engine(None, "g1", "g2")._more_than_one_graph() is True


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
