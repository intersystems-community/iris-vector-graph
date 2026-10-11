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


# ----- the declared width, as an embedded cursor returns it ---------------------------------------


class DictionaryCursor:
    """Answers the %Dictionary.CompiledProperty read with the given Parameters value."""

    def __init__(self, parameters):
        self.parameters = parameters
        self._rows = []

    def execute(self, sql, params=None):
        self._rows = [(self.parameters,)] if "CompiledProperty" in sql else []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


# Measured under irispython (IRIS 2026.3.0AI): a client cursor returns 'LEN,8', an embedded one returns
# the $LIST blob below, so the 'LEN,' search found nothing and every legacy table looked width-less.
EMBEDDED_PARAMETERS = (
    "\x16\x01\x11\x01CALCSELECTIVITY\x03\x011\r\x01\t\x01CAPTION\x02\x01\x14\x01\n\x01DATATYPE\x08\x01DOUBLE"
    "\x15\x01\x11\x01EXTERNALSQLNAME\x02\x01\x05\x01LEN\x03\x01%s\x1e\x01\x10\x01STORAGEDEFAULT"
)


def test_the_width_is_read_from_a_client_style_parameters_string():
    from iris_vector_graph.schema import GraphSchema

    cursor = DictionaryCursor("DATATYPE,DOUBLE,LEN,768")
    assert GraphSchema.get_embedding_dimension(cursor, "Graph_KG.kg_NodeEmbeddings") == 768


def test_the_width_is_read_from_an_embedded_list_blob():
    from iris_vector_graph.schema import GraphSchema

    for width in ("8", "768", "1536"):
        cursor = DictionaryCursor(EMBEDDED_PARAMETERS % width)
        assert GraphSchema.get_embedding_dimension(cursor, "Graph_KG.kg_NodeEmbeddings") == int(width)
