"""4.6.0 R319 follow-up: the bound-source checks read the namespace's IVG Schema, not Graph_KG.

IRISGraphStore keeps its schema in `_schema` (no `_schema_prefix`), so `node_with_labels` and `id_holders` read
`Graph_KG.nodes` in a namespace set up under another schema and failed SQLCODE -30 (final gate on 1ee67c4b, the R319
E2E in the `RAG` namespace).
"""

from __future__ import annotations

from iris_vector_graph._engine.query import id_holders, node_with_labels


class _Cursor:
    def __init__(self, sink):
        self.sink = sink

    def execute(self, sql, args):
        self.sink.append(sql)

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def close(self):
        pass


class _Conn:
    def __init__(self):
        self.sql = []

    def cursor(self):
        return _Cursor(self.sql)


class _Store:
    """The shape of IRISGraphStore: its schema in `_schema`."""

    def __init__(self, schema):
        self._schema = schema
        self.conn = _Conn()


def test_node_with_labels_reads_the_stores_schema():
    store = _Store("RAG")
    node_with_labels(store, "n1", ["L"], "g")
    assert all("RAG." in s and "Graph_KG." not in s for s in store.conn.sql), store.conn.sql


def test_id_holders_reads_the_stores_schema():
    store = _Store("RAG")
    id_holders(store, "x", ["L"], "g")
    assert all("RAG." in s and "Graph_KG." not in s for s in store.conn.sql), store.conn.sql
