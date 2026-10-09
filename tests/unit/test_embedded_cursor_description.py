"""EmbeddedCursor.description from ``iris.sql`` result metadata.

``iris.sql`` results have no columnCount()/columnName(), so the cursor's description stayed
None and an embedded Cypher result had rows but no columns (found through IVG.CypherEngine).
"""

from iris_vector_graph.embedded import _description_from_metadata


class _Col:
    def __init__(self, name):
        self.colName = name


class _Columns:
    def __init__(self, names):
        self._cols = [_Col(n) for n in names]

    def Count(self):
        return len(self._cols)

    def GetAt(self, i):  # 1-based, like a %ListOfObjects
        return self._cols[i - 1]


class _Meta:
    def __init__(self, names):
        self.columns = _Columns(names)


class _Underlying:
    def __init__(self, names):
        self._names = names

    def _GetMetadata(self):
        return _Meta(self._names)


class _Result:
    def __init__(self, names):
        self.ResultSet = _Underlying(names)


def test_names_come_from_the_underlying_result_metadata():
    desc = _description_from_metadata(_Result(["x", "y"]))
    assert [d[0] for d in desc] == ["x", "y"]
    assert all(len(d) == 7 for d in desc)  # DB-API: seven fields per column


def test_a_result_without_metadata_has_no_description():
    assert _description_from_metadata(object()) is None
    assert _description_from_metadata(_Result([])) is None


def test_embedded_iris_dispatches_class_methods_through_iris_cls():
    from iris_vector_graph.embedded import EmbeddedIRIS

    calls = []

    class _Cls:
        def Echo(self, *args):
            calls.append(args)
            return "v"

    class _Iris:
        @staticmethod
        def cls(name):
            calls.append(name)
            return _Cls()

    obj = EmbeddedIRIS(_Iris)
    assert obj.classMethodValue("A.B", "Echo", 1, "x") == "v"
    assert obj.classMethodVoid("A.B", "Echo") is None
    assert obj.classMethodObject("A.B", "Echo", 2) == "v"
    assert calls == ["A.B", (1, "x"), "A.B", (), "A.B", (2,)]
