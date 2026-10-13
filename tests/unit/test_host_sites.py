"""The call sites that moved onto ``iris_vector_graph.host``, tested without IRIS.

Each test gives its connection a native object on ``conn.iris`` and makes ``iris.createIRIS`` raise, so a site that
still builds its own handle fails here instead of passing by accident. The native object is the fake from
``test_host.py``, which answers the Native API's vocabulary over a dict of globals.
"""

from __future__ import annotations

import json
import sys
import types

import pytest

from iris_vector_graph import host as H
from tests.unit.test_host import FakeNative


@pytest.fixture
def no_createiris(monkeypatch):
    def refuse(conn):
        raise AssertionError("iris.createIRIS must not be called at a migrated site")

    module = types.ModuleType("iris")
    module.createIRIS = refuse
    monkeypatch.setitem(sys.modules, "iris", module)


class _Cursor:
    def __init__(self, count):
        self.row = (count,)

    def execute(self, sql, params=None):
        pass

    def fetchone(self):
        return self.row

    def fetchall(self):
        return [self.row]

    def close(self):
        pass


class _SqlConn:
    """A connection that carries a native object and answers every dictionary probe with ``count``."""

    def __init__(self, native, count=1):
        self.iris = native
        self._count = count

    def cursor(self):
        return _Cursor(self._count)


# ------------------------------------------------------------------------------------- migrations/kg_node_stores.py


def test_count_entries_reads_through_the_host_and_never_createiris(no_createiris):
    from iris_vector_graph.migrations import kg_node_stores as K

    native = FakeNative()
    native.set("1", "^KG", "prop", "a")
    native.set("1", "^KG", "prop", "b")
    host = H.host_for(types.SimpleNamespace(iris=native))

    assert K._count_entries(host, "prop") == 2


def test_recompile_asks_existence_in_sql_and_still_compiles_the_shipped_classes(no_createiris):
    from iris_vector_graph.migrations import kg_node_stores as K

    native = FakeNative()
    host = H.host_for(_SqlConn(native, count=1))

    K._recompile_rebuild_classes(host)

    probes = [call for call in native.calls if call[1] == "%ExistsId"]
    compiles = [call for call in native.calls if call[:2] == ("%SYSTEM.OBJ", "CompileList")]
    assert probes == [], "existence is asked in SQL, not through the %Dictionary %ExistsId method"
    assert len(compiles) == 1, "the shipped classes are still compiled in one CompileList call"
    assert "Graph.KG." in compiles[0][2][0]


# ----------------------------------------------------------------------------------------- stores/lazy_kg.py


def test_lazy_kg_reads_neighbours_through_the_host(no_createiris):
    from iris_vector_graph.stores.lazy_kg import LazyKG

    # Out-degree is not a stored counter any more: LazyKG.degree asks Graph.KG.Adj.Degree through the host (spec 249).
    native = FakeNative(replies={("Graph.KG.Adj", "Degree"): lambda graph, node, pred="": 3})
    native.set("1", "^KG", "out", 0, "a", "p", "b")
    native.set("1", "^KG", "in", 0, "b", "p", "a")
    kg = LazyKG(types.SimpleNamespace(iris=native))

    assert kg.out_neighbors("a") == ["b"]
    assert kg.in_neighbors("b") == ["a"]
    assert kg.degree("a") == 3
    assert native.calls == [("Graph.KG.Adj", "Degree", (0, "a", ""))]


# ------------------------------------------------------------------------------------- stores/arno_bridge.py


def test_build_kg_adjacency_json_walks_through_the_host(no_createiris):
    from iris_vector_graph.stores.arno_bridge import build_kg_adjacency_json

    native = FakeNative()
    native.set("1", "^KG", "out", 0, "a", "p", "b")

    out = build_kg_adjacency_json(types.SimpleNamespace(iris=native))

    assert json.loads(out) == {"nodes": ["a", "b"], "edges": [{"s": "a", "d": "b"}]}


def test_build_kg_adjacency_chunked_walks_through_the_host_before_refusing_without_arno(
    no_createiris, monkeypatch
):
    from iris_vector_graph.stores.arno_bridge import ArnoError, build_kg_adjacency_chunked

    monkeypatch.setenv("IVG_DISABLE_ARNO", "1")
    native = FakeNative()
    native.set("1", "^KG", "out", 0, "a", "p", "b")

    # With arno disabled the walk runs in Python and then refuses to upload: the refusal is the proof the walk
    # got through the host without touching createIRIS.
    with pytest.raises(ArnoError):
        build_kg_adjacency_chunked(types.SimpleNamespace(iris=native))


# ------------------------------------------------------------------------------------------------ schema.py


def test_call_classmethod_runs_through_host_for(no_createiris):
    from iris_vector_graph.schema import _call_classmethod

    native = FakeNative(replies={("Graph.KG.X", "Go"): lambda a: f"got {a}"})

    assert _call_classmethod(types.SimpleNamespace(iris=native), "Graph.KG.X", "Go", 7) == "got 7"


def test_call_classmethod_large_delegates_to_host_call_large(monkeypatch):
    from iris_vector_graph.schema import _call_classmethod_large

    seen = []

    def spy(self, cls, method, *args):
        seen.append((cls, method, args))
        return "via the host"

    monkeypatch.setattr(H.ClientHost, "call_large", spy)

    assert _call_classmethod_large(FakeNative(), "Graph.KG.X", "Go", 1) == "via the host"
    assert seen == [("Graph.KG.X", "Go", (1,))]


def test_call_classmethod_large_accepts_a_host():
    from iris_vector_graph.schema import _call_classmethod_large

    native = FakeNative(replies={("Graph.KG.X", "Go"): lambda: "plain"})
    host = H.ClientHost(None, native=native)

    assert _call_classmethod_large(host, "Graph.KG.X", "Go") == "plain"


def test_value_at_reads_a_node_in_one_host_call():
    """The baseline read each node with one getString. Checking is_defined first and then reading doubled the round trips of
    the kg_node_stores migration step over tens of thousands of nodes (+12 s on 50,000 nodes)."""
    from iris_vector_graph.migrations import kg_node_stores as k

    class Host:
        def __init__(self):
            self.ops = []

        def get_string(self, name, *subs):
            self.ops.append(("get_string", name, subs))
            return "v"

        def is_defined(self, *a):  # pragma: no cover - must not be called
            self.ops.append(("is_defined",))
            return 1

        def get(self, *a):  # pragma: no cover - must not be called
            self.ops.append(("get",))
            return "v"

    host = Host()
    assert k._value_at(host, ("out", "0", "a")) == "v"
    assert host.ops == [("get_string", "^KG", ("out", "0", "a"))]
