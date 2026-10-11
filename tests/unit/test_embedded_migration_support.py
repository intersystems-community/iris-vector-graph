"""What the 4.0.0 upgrade needs when it runs inside IRIS (irispython), reported by Mindwalk on 4.2.1.

1. ``rekey_kg_node_stores`` asked ``iris.createIRIS`` for a Native handle; an ``EmbeddedConnection`` has none.
2. The embeddings step refused a legacy table that ``initialize_schema`` adopted with no recorded width, though
   the width is in the data.
"""

from types import SimpleNamespace

from iris_vector_graph.embedded import EmbeddedConnection, EmbeddedIRIS
from iris_vector_graph.migrations import kg_node_stores
from iris_vector_graph.migrations.graph_scoped_embeddings import _Migrator


def test_an_embedded_connection_gets_the_embedded_native_handle():
    assert isinstance(kg_node_stores._native(EmbeddedConnection()), EmbeddedIRIS)


def test_a_connection_that_carries_a_handle_still_uses_it():
    handle = object()
    assert kg_node_stores._native(SimpleNamespace(iris=handle)) is handle


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(sql)

    def fetchall(self):
        return self.rows


def _migrator():
    m = _Migrator.__new__(_Migrator)
    m._t = lambda name: f"Graph_KG.{name}"
    return m


def test_the_width_is_measured_from_a_stored_vector_when_nothing_declares_it():
    cursor = Cursor([("0.1,0.2,0.3,0.4,0.5",)])

    assert _migrator()._measured_width(cursor, "kg_NodeEmbeddings") == 5
    assert "TOP 1" in cursor.sql[0] and "emb IS NOT NULL" in cursor.sql[0]


def test_an_empty_table_has_no_measurable_width():
    assert _migrator()._measured_width(Cursor([]), "kg_NodeEmbeddings") is None


def test_a_vector_that_comes_back_as_a_list_is_counted_too():
    assert _migrator()._measured_width(Cursor([([1.0, 2.0, 3.0],)]), "t") == 3
