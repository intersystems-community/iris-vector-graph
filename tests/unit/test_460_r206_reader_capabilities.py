"""4.6.0 R206: a reader at database `R` found no IVG classes, so `khop` answered `[]` (walkthrough 1 P8).

ADMIN_GUIDE §3's reading role holds no SELECT on the class dictionary: `SELECT ... FROM %Dictionary.ClassDefinition`
is refused (SQLCODE -99). The capability probe's fallback then called `Graph.KG.PageRank.%Exists(1)`, a method a
non-persistent class lacks, so it answered "not deployed", and every read gated on the capability took its empty or
Python path: `khop` answered two empty lists for a graph whose `BFSFastJson` answers correctly under that role. The
fallback now asks `%Dictionary.CompiledClass.IDKEYExists` over the Native API, which `%Native_ClassExecution` allows.
`khop` itself raises when the walk fails instead of answering `[]`. Live: tests/e2e/test_460_r206_reader_reads_e2e.py.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.capabilities import IRISCapabilities
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema

REFUSED = "[SQLCODE: <-99>:<Privilege violation>] [%msg: <User R is not privileged for the operation>]"


class _Native:
    """The Native API a reader holds: class methods run; `classes` are the compiled ones."""

    def __init__(self, classes):
        self.classes = set(classes)
        self.calls = []

    def classMethodValue(self, cls, method, *args):
        self.calls.append((cls, method) + args)
        if (cls, method) == ("%Dictionary.CompiledClass", "IDKEYExists"):
            return 1 if args[0] in self.classes else 0
        if (cls, method) == ("Graph.KG.Meta", "IsSet"):
            return 0
        raise RuntimeError(f"<METHOD DOES NOT EXIST> *{method},{cls}")


class _Conn:
    def __init__(self, native):
        self.iris = native


def _refusing_cursor():
    cursor = MagicMock()
    cursor.execute.side_effect = RuntimeError(REFUSED)
    return cursor


def test_a_reader_without_the_dictionary_in_sql_still_finds_the_classes():
    native = _Native({"Graph.KG.PageRank"})
    caps = GraphSchema.check_objectscript_classes(_refusing_cursor(), conn=_Conn(native))
    assert caps.objectscript_deployed is True
    assert caps.graphoperators_deployed is False
    assert ("%Dictionary.CompiledClass", "IDKEYExists", "Graph.KG.PageRank") in native.calls


def test_a_namespace_without_the_classes_is_still_not_deployed_for_a_reader():
    native = _Native(set())
    caps = GraphSchema.check_objectscript_classes(_refusing_cursor(), conn=_Conn(native))
    assert caps.objectscript_deployed is False


def _engine(walk):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = (1,)
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    eng._capabilities = IRISCapabilities(objectscript_deployed=True)
    native = MagicMock()
    native.classMethodValue.side_effect = walk
    eng._iris_obj = lambda: native
    return eng


def test_khop_answers_the_walk():
    edges = [{"s": "a", "p": "KNOWS", "o": "b", "w": 1, "step": 1}, {"s": "b", "p": "KNOWS", "o": "c", "w": 1, "step": 2}]
    eng = _engine(lambda cls, method, *args: json.dumps(edges))
    assert eng.khop("a", hops=2) == {"nodes": ["a", "b", "c"], "edges": edges}


def test_khop_raises_when_the_walk_fails_instead_of_answering_empty():
    def refuse(cls, method, *args):
        raise RuntimeError('<PROTECT>BFSFast+12^Graph.KG.TraversalBFS.1 ^KG("out",0,"a"),/db/')

    eng = _engine(refuse)
    with pytest.raises(RuntimeError, match=r"khop\('a'\) failed.*<PROTECT>"):
        eng.khop("a", hops=2)


# -- the dictionary helpers every other probe uses (host.class_exists, the ledger guard, Arno, the Eraser) --------


class _IdKeyNative:
    def __init__(self, classes=(), methods=()):
        self.classes, self.methods, self.calls = set(classes), set(methods), []

    def classMethodValue(self, cls, method, *args):
        self.calls.append((cls, method) + args)
        assert not method.startswith("%"), "a %-method is not safe from every host"
        if (cls, method) == ("%Dictionary.CompiledClass", "IDKEYExists"):
            return 1 if args[0] in self.classes else 0
        if (cls, method) == ("%Dictionary.CompiledMethod", "IDKEYExists"):
            return 1 if args in self.methods else 0
        raise AssertionError((cls, method))


class _RefusingConn:
    def cursor(self):
        return _refusing_cursor()


def test_compiled_class_exists_asks_idkeyexists_when_the_dictionary_select_is_refused():
    from iris_vector_graph._dictionary import compiled_class_exists

    native = _IdKeyNative(classes={"Graph.KG.Ledger"})
    assert compiled_class_exists(_RefusingConn(), "Graph.KG.Ledger", native=native) is True
    assert compiled_class_exists(_RefusingConn(), "Graph.KG.Nope", native=native) is False


def test_compiled_method_exists_asks_idkeyexists_when_the_dictionary_select_is_refused():
    from iris_vector_graph._dictionary import compiled_method_exists

    native = _IdKeyNative(methods={("Graph.KG.Eraser", "EraseNodeIds")})
    assert compiled_method_exists(_RefusingConn(), "Graph.KG.Eraser", "EraseNodeIds", native=native) is True
    assert compiled_method_exists(_RefusingConn(), "Graph.KG.Eraser", "Nope", native=native) is False


def test_another_sql_failure_still_raises():
    """Only a privilege refusal is answered from the Native API: a dead connection is not "absent"."""
    from iris_vector_graph._dictionary import compiled_class_exists

    class _Dead:
        def cursor(self):
            cursor = MagicMock()
            cursor.execute.side_effect = RuntimeError("Communication link failure")
            return cursor

    with pytest.raises(RuntimeError, match="Communication link"):
        compiled_class_exists(_Dead(), "Graph.KG.Eraser", native=_IdKeyNative())


def test_the_ledger_guard_of_a_writer_without_the_dictionary_sees_strict_mode():
    """It probed the ledger class in SQL only; refused, the probe raised, and the guard read "never enabled", so a
    writer holding ADMIN_GUIDE's writing role was never stopped by strict mode."""
    from iris_vector_graph.ledger.errors import LedgerStrictModeError
    from iris_vector_graph.ledger.guard import LedgerGuard

    native = _IdKeyNative(classes={"Graph.KG.Ledger"})

    def answer(cls, method, *args):
        if method == "Meta":
            return json.dumps({"state": "enabled", "strict": True})
        if method == "Count":
            return 1
        return native.classMethodValue(cls, method, *args)

    engine = MagicMock()
    engine.conn = _RefusingConn()
    iris_obj = MagicMock()
    iris_obj.classMethodValue.side_effect = answer
    engine._iris_obj.return_value = iris_obj
    with pytest.raises(LedgerStrictModeError):
        LedgerGuard(engine).check_structural_write("create_node")
