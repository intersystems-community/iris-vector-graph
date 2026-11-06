"""4.6.0 R050 (expert walkthrough W1, the lead's follow-up): a class probe the routines database refuses is not "absent".

On a namespace whose routines are in a database of their own, a reader without `R` on that database's resource is
refused the class dictionary SELECT (SQLCODE -99, as every §3 role is), and the Native API fallback
`%Dictionary.CompiledClass.IDKEYExists` then answers 0 for every class: the dictionary is in the routines database,
and IRIS does not say it refused. The engine concluded IVG was not deployed and `khop()` answered `[]` (measured on
2026.3.0AI). Running one of IVG's classes tells the two apart: absent, IRIS answers `<CLASS DOES NOT EXIST>`; refused,
`<PROTECT>` naming the routines database. A refusal now raises `ClassesUnreadable`, naming that database and
ADMIN_GUIDE §3, wherever the engine asks what is deployed; `status()` reports it instead of raising. Live:
tests/e2e/test_460_r202_reading_role_e2e.py (split-database tests).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.exceptions import ClassesUnreadable
from iris_vector_graph.schema import GraphSchema

REFUSED_SQL = "[SQLCODE: <-99>:<Privilege violation>] [%msg: <User R is not privileged for the operation>]"
ROUTINES_DIR = "/usr/irissys/mgr/ivg460fx58r3r/"
PROTECT = f"<PROTECT>^%SYS.DBSRV.1 *^|^^{ROUTINES_DIR}|Graph.KG.Meta.1"


def _refusing_cursor():
    cursor = MagicMock()
    cursor.execute.side_effect = RuntimeError(REFUSED_SQL)
    return cursor


class _Native:
    """IDKEYExists answers 0 for everything (what the refused dictionary gives); running Graph.KG.Meta raises
    `meta_error`, or answers 0 when it is None."""

    def __init__(self, meta_error):
        self.meta_error = meta_error
        self.calls = []
        self.iris = self  # the native object the host takes from a stand-in (iris_vector_graph/host.py: host_for)

    def classMethodValue(self, cls, method, *args):
        self.calls.append((cls, method))
        if method == "IDKEYExists":
            return 0
        if (cls, method) == ("Graph.KG.Meta", "IsSet"):
            if self.meta_error:
                raise RuntimeError(self.meta_error)
            return 0
        raise AssertionError(f"unexpected {cls}.{method}")


def test_a_refused_routines_database_raises_naming_it_and_the_guide():
    with pytest.raises(ClassesUnreadable) as raised:
        GraphSchema.check_objectscript_classes(_refusing_cursor(), conn=_Native(PROTECT))
    text = str(raised.value)
    assert ROUTINES_DIR in text
    assert "routines database" in text and "ADMIN_GUIDE" in text and "§3" in text
    assert "<PROTECT>" in text


def test_classes_that_are_absent_are_still_absent():
    caps = GraphSchema.check_objectscript_classes(_refusing_cursor(),
                                                  conn=_Native("<CLASS DOES NOT EXIST> *Graph.KG.Meta"))
    assert caps.objectscript_deployed is False


def test_a_refusal_naming_no_database_is_not_taken_for_the_routines_database():
    """Without %Native_ClassExecution IRIS refuses every class method, with no database in the error."""
    caps = GraphSchema.check_objectscript_classes(
        _refusing_cursor(), conn=_Native("<PROTECT> *Resource %Native_ClassExecution required"))
    assert caps.objectscript_deployed is False


def test_a_readable_dictionary_that_says_absent_is_not_second_guessed():
    cursor = MagicMock()
    cursor.fetchone.return_value = (0,)
    native = _Native(PROTECT)
    caps = GraphSchema.check_objectscript_classes(cursor, conn=native)
    assert caps.objectscript_deployed is False
    assert native.calls == []


def _engine_meeting_the_refusal(monkeypatch):
    def refused(cursor, conn=None):
        raise ClassesUnreadable(f"refused: {PROTECT}")

    monkeypatch.setattr(GraphSchema, "check_objectscript_classes", staticmethod(refused))
    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = MagicMock()
    engine._capabilities = None
    return engine


def test_khop_raises_instead_of_answering_no_nodes(monkeypatch):
    engine = _engine_meeting_the_refusal(monkeypatch)
    with pytest.raises(ClassesUnreadable, match="refused"):
        engine.khop("a", hops=2)


def test_the_refusal_is_not_cached_as_absent(monkeypatch):
    engine = _engine_meeting_the_refusal(monkeypatch)
    for _ in range(2):
        with pytest.raises(ClassesUnreadable):
            engine.capabilities  # noqa: B018 - the property is the probe


def test_it_is_exported():
    import iris_vector_graph

    assert iris_vector_graph.ClassesUnreadable is ClassesUnreadable
    assert issubclass(ClassesUnreadable, RuntimeError)


def test_the_docstring_says_what_to_grant():
    """The API reference shows the first paragraph: it names the grant and where the guide explains it."""
    first = " ".join(ClassesUnreadable.__doc__.strip().split("\n\n")[0].split())
    assert "<routines database resource>:R" in first and "ADMIN_GUIDE §3" in first, first
