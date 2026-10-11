"""Class and method existence is asked in SQL, never through a %-method.

Mindwalk (Build 154): with the 4.2.1/4.2.2 `%` -> `_` mapping the engine's own `%ExistsId` probes really ran
inside the wgproto Callin worker and segfaulted it; under 4.2.0 the same probes raised AttributeError and were
read as "absent", which silently turned Arno detection off and sent `delete_node` down a path that returned False.
"""

from unittest.mock import MagicMock

from iris_vector_graph import _dictionary
from iris_vector_graph.stores.iris_sql_store import IRISGraphStore


class Cursor:
    def __init__(self, count):
        self.count = count
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), list(params or [])))

    def fetchone(self):
        return (self.count,)

    def close(self):
        pass


class Conn:
    def __init__(self, count):
        self.cur = Cursor(count)

    def cursor(self):
        return self.cur


def test_a_class_is_looked_up_in_the_compiled_dictionary():
    conn = Conn(1)
    assert _dictionary.compiled_class_exists(conn, "Graph.KG.ArnoAccel") is True
    assert conn.cur.sql == [("SELECT COUNT(*) FROM %Dictionary.CompiledClass WHERE Name = ?", ["Graph.KG.ArnoAccel"])]
    assert _dictionary.compiled_class_exists(Conn(0), "Nope") is False


def test_a_method_is_looked_up_by_parent_and_name():
    conn = Conn(1)
    assert _dictionary.compiled_method_exists(conn, "Graph.KG.Eraser", "EraseNodeIds") is True
    assert conn.cur.sql[0][1] == ["Graph.KG.Eraser", "EraseNodeIds"]
    assert _dictionary.compiled_method_exists(Conn(0), "Graph.KG.Eraser", "Nope") is False


def test_the_eraser_probe_makes_no_percent_method_call():
    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = Conn(1)
    native = MagicMock()
    store._iris_obj = lambda: native

    assert store._has_eraser_method("EraseNodeIds") is True

    native.classMethodValue.assert_not_called()
    assert store._has_eraser_method("EraseNodeIds") is True  # cached; still no call
