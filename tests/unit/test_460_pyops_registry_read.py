"""4.6.0 R146: `Graph.KG.PyOps` falls back to the legacy vector table only when `embedding_registry` is absent.

`getVectorTable` and `getExpectedDimension` read the registry in `try: ... except Exception:` and answered the legacy
table (`getExpectedDimension`: went on to the legacy table's column width) whatever the failure was. A user who may not
read the registry, or any other failure, got the default graph's table and width for a named graph, so its width
check could be wrong without a word. They now fall back only when the table does not exist (SQLCODE -30, a namespace
from before spec 226), noting it once per process in messages.log at information severity (`NoteNoRegistry`), and
raise on anything else.

These run the two methods' Python bodies, taken from `iris_src/src/Graph/KG/PyOps.cls`, outside IRIS: `iris` is a
stand-in whose `sql.exec` raises the SQLError embedded Python raises (an exception with `sqlcode`) for the registry,
and answers the INFORMATION_SCHEMA width (346 x 8, so 8) for the legacy table. The privilege case runs live in
tests/e2e/test_460_pyops_registry_privilege_e2e.py.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PYOPS = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG" / "PyOps.cls"
LEGACY = "Graph_KG.kg_NodeEmbeddings"


def _python_method(name: str):
    """The [ Language = python ] class method `name` of PyOps.cls as a Python function."""
    text = PYOPS.read_text()
    match = re.search(rf"^ClassMethod {name}\((.*?)\) As [%\w.]+ \[[^\]]*Language = python[^\]]*\]\n\{{\n", text, re.M)
    assert match, name
    body = text[match.end():text.index("\n}\n", match.end())]
    params = ", ".join(f'{p.split(" As ")[0].strip()}=""' for p in match.group(1).split(","))
    scope: dict = {}
    exec(f"def {name}({params}):\n{body}\n", scope)  # noqa: S102 - the shipped method body
    return scope[name]


class SQLError(Exception):
    """What embedded Python's iris.sql raises: the message, and `sqlcode`."""

    def __init__(self, message: str, sqlcode: int):
        super().__init__(message)
        self.sqlcode = sqlcode


class _Result(list):
    class ResultSet:
        _SQLCODE = 100
        _Message = ""


class FakeIris:
    def __init__(self, registry_error: SQLError):
        self.registry_error = registry_error
        self.notes: list = []
        self.statements: list = []
        get_vector_table = _python_method("getVectorTable")
        read_rows = _python_method("ReadRows")  # the one read both methods make (4.6.0 R164)
        fake = self

        class PyOps:
            getLegacyVectorTable = staticmethod(lambda: LEGACY)
            getVectorColumn = staticmethod(lambda: "emb")
            getDefaultDimension = staticmethod(lambda: 768)
            Lacks = staticmethod(lambda tables, procedures: "")
            @staticmethod
            def ReadFailure(what, sqlcode, message, tables, procedures):
                # As the ObjectScript method answers, less the privilege lookup (Lacks, live in the e2e file).
                return "" if sqlcode in ("", None) or sqlcode >= 0 else f"{what} failed: SQLCODE {sqlcode} {message}"

            @staticmethod
            def ReadRows(*args):
                return read_rows(*args)

            @staticmethod
            def getVectorTable(graph="", model=""):
                return get_vector_table(graph, model)

            @staticmethod
            def NoteNoRegistry(what):
                fake.notes.append(what)

        self._pyops = PyOps

        class Sql:
            @staticmethod
            def exec(sql, *args):
                fake.statements.append(sql)
                if "embedding_registry" in sql:
                    raise fake.registry_error
                assert "INFORMATION_SCHEMA.COLUMNS" in sql, sql
                assert args == ("Graph_KG", "kg_NodeEmbeddings", "emb"), args
                return _Result([[346 * 8]])

        self.sql = Sql

    def cls(self, name):
        assert name == "Graph.KG.PyOps", name
        return self._pyops


ABSENT = SQLError(" Table 'GRAPH_KG.EMBEDDING_REGISTRY' not found", -30)
DENIED = SQLError("User IVG460FX8_R146_USER is not privileged for the operation", -99)
OTHER = SQLError("Field 'KIND' not found in the applicable tables", -29)


def _run(name: str, error: SQLError, *args):
    fake = FakeIris(error)
    with patch.dict(sys.modules, {"iris": fake}):
        return _python_method(name)(*args), fake


@pytest.mark.parametrize("graph", ["", "g1"])
def test_an_absent_registry_falls_back_to_the_legacy_table_and_says_so(graph):
    table, fake = _run("getVectorTable", ABSENT, graph, "")
    assert table == LEGACY
    assert fake.notes == ["getVectorTable"]


def test_an_absent_registry_gives_the_legacy_tables_declared_width():
    width, fake = _run("getExpectedDimension", ABSENT, "", "")
    assert width == 8
    assert fake.notes == ["getExpectedDimension", "getVectorTable"]
    assert any("INFORMATION_SCHEMA.COLUMNS" in s for s in fake.statements)


@pytest.mark.parametrize("method", ["getVectorTable", "getExpectedDimension"])
@pytest.mark.parametrize("error", [DENIED, OTHER], ids=["denied", "other"])
def test_a_registry_that_exists_and_fails_raises(method, error):
    with pytest.raises(Exception) as raised:
        _run(method, error, "g1", "")
    assert error.args[0] in str(raised.value)
    if error is DENIED:
        assert isinstance(raised.value, PermissionError)
