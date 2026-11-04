"""4.6.0 R164: `Graph.KG.PyOps` reads its SQL through one helper, and an exception that carries no failure SQLCODE
is raised, not turned into a `NameError`.

`VectorSearch` and `HybridSearch` set `rows` only inside the `try`. An exception from `iris.sql.exec` whose `sqlcode`
was 0 or more made `ReadFailure` answer `""` (no failure), and the next `for r in rows` raised
`NameError: name 'rows' is not defined`, which named nothing that went wrong. Four methods (those two, getVectorTable,
getExpectedDimension) each held a copy of the exec / read the result's SQLCODE / raise block; they now call
`PyOps.ReadRows`, which sets `rows = []` before its `try` and re-raises an exception whose SQLCODE is not a failure,
since an exception with no failure code is still a failure this method cannot describe better.

The method bodies run outside IRIS, taken from `iris_src/src/Graph/KG/PyOps.cls`, with the stand-in `iris` of
tests/unit/test_460_pyops_registry_read.py (R146); `ReadRows` is the shipped body too.
"""

from __future__ import annotations

import re
import sys
from unittest.mock import patch

import pytest

from tests.unit.test_460_pyops_registry_read import PYOPS, SQLError, _python_method


class _Result(list):
    def __init__(self, rows, sqlcode=100, message=""):
        super().__init__(rows)
        self.ResultSet = type("RS", (), {"_SQLCODE": sqlcode, "_Message": message})()


class _Dyn:
    """%DynamicArray / %DynamicObject, as far as the two searches use them."""

    def __init__(self):
        self.items: list = []
        self.fields: dict = {}

    def _Push(self, v):
        self.items.append(v)

    def _Set(self, k, v, *_type):
        self.fields[k] = v


class _Vec:
    def __init__(self, values):
        self.values = values

    def _Size(self):
        return len(self.values)

    def _Get(self, i):
        return self.values[i]


class FakeIris:
    """`iris.sql.exec` answers `answer` (a _Result) or raises it (an exception) for the search statement."""

    def __init__(self, answer):
        read_rows = _python_method("ReadRows") if "ClassMethod ReadRows(" in PYOPS.read_text() else None
        # R265: a search of the default graph ("" here) never reads a route, so RouteVectorRows answers None
        route_rows = _python_method("RouteVectorRows")
        fuse_ranks = _python_method("FuseRanks")
        fake = self
        self.answer = answer

        class PyOps:
            getVectorColumn = staticmethod(lambda: "emb")
            getDefaultDimension = staticmethod(lambda: 768)
            getLegacyVectorTable = staticmethod(lambda: "Graph_KG.kg_NodeEmbeddings")
            vectorToJson = staticmethod(lambda vec, dim, graph, model: "[1.0, 0.0]")
            NoteNoRegistry = staticmethod(lambda what: None)

            @staticmethod
            def ReadFailure(what, sqlcode, message, tables, procedures):
                return "" if sqlcode in ("", None) or sqlcode >= 0 else f"{what} failed: SQLCODE {sqlcode} {message}"

            @staticmethod
            def ReadRows(*args):
                return read_rows(*args)

            @staticmethod
            def RouteVectorRows(*args):
                return route_rows(*args)

            @staticmethod
            def FuseRanks(*args):
                return fuse_ranks(*args)

        self._pyops = PyOps

        class Sql:
            @staticmethod
            def exec(sql, *args):
                if isinstance(fake.answer, BaseException):
                    raise fake.answer
                return fake.answer

        self.sql = Sql

    def cls(self, name):
        if name == "Graph.KG.PyOps":
            return self._pyops
        assert name in ("%DynamicArray", "%DynamicObject"), name
        return type("D", (), {"_New": staticmethod(_Dyn)})


def _run(method: str, answer, *args):
    with patch.dict(sys.modules, {"iris": FakeIris(answer)}):
        return _python_method(method)(*args)


SEARCHES = {
    "VectorSearch": (_Vec([1.0, 0.0]), 2, "", ""),
    "HybridSearch": (_Vec([1.0, 0.0]), "text", 2, 60, ""),
}


@pytest.mark.parametrize("method", sorted(SEARCHES))
@pytest.mark.parametrize("sqlcode", [0, 100])
def test_an_exception_without_a_failure_sqlcode_is_raised_not_a_name_error(method, sqlcode):
    error = SQLError("the driver raised, reporting no failure", sqlcode)
    with pytest.raises(SQLError) as raised:
        _run(method, error, *SEARCHES[method])
    assert raised.value is error


@pytest.mark.parametrize("method", sorted(SEARCHES))
def test_a_failure_sqlcode_still_raises_naming_it(method):
    with pytest.raises(PermissionError) as raised:
        _run(method, SQLError("not privileged", -99), *SEARCHES[method])
    assert "SQLCODE -99" in str(raised.value)


@pytest.mark.parametrize("method", sorted(SEARCHES))
def test_a_failed_fetch_on_the_result_raises(method):
    with pytest.raises(RuntimeError) as raised:
        _run(method, _Result([], sqlcode=-400, message="fetch failed"), *SEARCHES[method])
    assert "SQLCODE -400 fetch failed" in str(raised.value)


def test_vector_search_answers_its_rows():
    out = _run("VectorSearch", _Result([("n1", "0.9"), ("n2", "0.5")]), *SEARCHES["VectorSearch"])
    assert [(o.fields["id"], o.fields["score"]) for o in out.items] == [("n1", 0.9), ("n2", 0.5)]


def test_hybrid_search_answers_its_rows():
    out = _run("HybridSearch", _Result([("n1", "0.03", "0.9", "")]), *SEARCHES["HybridSearch"])
    [row] = out.items
    assert (row.fields["id"], row.fields["score"]) == ("n1", 0.03)
    assert row.fields["extras"].fields == {"vs": 0.9, "bm25": ""}


def test_one_copy_of_the_read_block():
    """The exec / SQLCODE / raise block lives in ReadRows alone."""
    text = PYOPS.read_text()
    assert len(re.findall(r"ResultSet\._SQLCODE", text)) == 1
    # VectorSearch, HybridSearch (the procedure and, for a routed graph, kg_TXT), RouteVectorRows (the registry and
    # the route), getVectorTable, getExpectedDimension (R265 added three)
    assert text.count(".ReadRows(") == 7
