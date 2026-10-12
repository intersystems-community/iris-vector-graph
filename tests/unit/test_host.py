"""The native-handle module: IVG operations over whichever host the code is running on.

ADR-0008's first step and the architecture review's candidate 2. Seven sites used to build "a handle" and call the
Native API's vocabulary on it, so each caller chose host-sensitive calls itself: a ``%`` method (``_New`` under
irispython, a segfault through the wgproto worker), a statement with hundreds of parameters, a chunked reply it had to
reassemble. These tests pin the interface callers use instead, against both adapters, with no IRIS needed.
"""

from __future__ import annotations

import sys
import types

import pytest

from iris_vector_graph import host as H
from iris_vector_graph.embedded import EmbeddedConnection


class FakeNative:
    """What ``iris.createIRIS(conn)`` returns: the Native API surface, over a dict of globals."""

    def __init__(self, replies=None):
        self.globals = {}
        self.calls = []
        self.replies = replies or {}

    def classMethodValue(self, cls, method, *args):
        self.calls.append((cls, method, args))
        return self.replies[(cls, method)](*args) if (cls, method) in self.replies else None

    def get(self, name, *subs):
        return self.globals.get((name, subs))

    def set(self, value, name, *subs):
        self.globals[(name, subs)] = value

    def kill(self, name, *subs):
        for key in [k for k in self.globals if k[0] == name and k[1][: len(subs)] == subs]:
            del self.globals[key]

    def isDefined(self, name, *subs):
        has_value = (name, subs) in self.globals
        has_child = any(k[0] == name and len(k[1]) > len(subs) and k[1][: len(subs)] == subs for k in self.globals)
        return (10 if has_child else 0) + (1 if has_value else 0)

    def nextSubscript(self, reverse, name, *subs):
        parent, after = subs[:-1], subs[-1]
        sibs = sorted({k[1][len(parent)] for k in self.globals if k[0] == name and k[1][: len(parent)] == parent and len(k[1]) > len(parent)})
        if reverse:
            sibs.reverse()
        if after == "":
            return sibs[0] if sibs else None
        later = [s for s in sibs if (s < after if reverse else s > after)]
        return later[0] if later else None


@pytest.fixture
def client(monkeypatch):
    native = FakeNative()
    made = []

    def create_iris(conn):
        made.append(conn)
        return native

    fake_iris = types.ModuleType("iris")
    fake_iris.createIRIS = create_iris
    monkeypatch.setitem(sys.modules, "iris", fake_iris)
    return native, made


# ----------------------------------------------------------------------------------------------- choosing the host


def test_an_embedded_connection_gets_the_in_process_host():
    assert isinstance(H.host_for(EmbeddedConnection()), H.InProcessHost)


def test_any_other_connection_gets_the_client_host_built_with_iris_createiris(client):
    native, made = client
    conn = object()

    host = H.host_for(conn)

    assert isinstance(host, H.ClientHost)
    host.get("^KG", "n")
    assert made == [conn], "the handle must come from iris.createIRIS, so a test's patch of it still applies"


# -------------------------------------------------------------------------------------- the operations, both hosts


class FakeGref:
    def __init__(self, store):
        self.store = store

    def __getitem__(self, k):
        return self.store.get(k if isinstance(k, tuple) else (k,))

    def __setitem__(self, k, v):
        self.store[k if isinstance(k, tuple) else (k,)] = v

    def kill(self, subs):
        subs = tuple(subs)
        for key in [k for k in self.store if k[: len(subs)] == subs]:
            del self.store[key]

    def order(self, subs, direction=1):
        subs = list(subs)
        parent, after = tuple(subs[:-1]), subs[-1]
        sibs = sorted({k[len(parent)] for k in self.store if k[: len(parent)] == parent and len(k) > len(parent)})
        if direction == -1:
            sibs.reverse()
        if after == "":
            return sibs[0] if sibs else None
        later = [s for s in sibs if (s < after if direction == -1 else s > after)]
        return later[0] if later else None

    def data(self, subs):
        subs = tuple(subs)
        child = any(k[: len(subs)] == subs and len(k) > len(subs) for k in self.store)
        return (10 if child else 0) + (1 if subs in self.store else 0)


class FakeCls:
    def __init__(self, replies):
        self.replies = replies

    def __getattr__(self, method):
        return lambda *a: self.replies[method](*a) if method in self.replies else None


def in_process(replies=None):
    stores = {}
    fake = types.SimpleNamespace(
        cls=lambda name: FakeCls((replies or {}).get(name, {})),
        gref=lambda name: FakeGref(stores.setdefault(name, {})),
    )
    return H.InProcessHost(iris_module=fake), stores


@pytest.fixture(params=["client", "in_process"])
def host(request, client):
    if request.param == "client":
        return H.host_for(object())
    return in_process()[0]


def test_a_global_node_round_trips(host):
    host.set("v", "^KG", "a", "b")

    assert host.get("^KG", "a", "b") == "v"
    assert host.get("^KG", "a", "missing") is None


def test_is_defined_reports_value_and_children(host):
    host.set(1, "^KG", "a", "b")

    assert host.is_defined("^KG", "a") == 10
    assert host.is_defined("^KG", "a", "b") == 1
    assert host.is_defined("^KG", "zz") == 0


def test_next_subscript_walks_siblings_in_order_and_ends_with_none(host):
    for k in ("b", "a", "c"):
        host.set(1, "^KG", "out", k)

    seen, cur = [], host.next_subscript("^KG", "out", "")
    while cur is not None:
        seen.append(cur)
        cur = host.next_subscript("^KG", "out", cur)

    assert seen == ["a", "b", "c"]


def test_kill_takes_the_node_and_its_descendants(host):
    host.set(1, "^KG", "out", "a", "x")
    host.set(1, "^KG", "out", "b")

    host.kill("^KG", "out", "a")

    assert host.is_defined("^KG", "out", "a") == 0 and host.is_defined("^KG", "out", "b") == 1


# ------------------------------------------------------------------------------------------------------- calling


def test_call_runs_a_class_method_on_the_client_host(client):
    native, _ = client
    native.replies[("Graph.KG.X", "Go")] = lambda a, b: f"{a}-{b}"

    assert H.host_for(object()).call("Graph.KG.X", "Go", 1, 2) == "1-2"


def test_call_runs_a_class_method_on_the_in_process_host():
    host, _ = in_process({"Graph.KG.X": {"Go": lambda a, b: f"{a}-{b}"}})

    assert host.call("Graph.KG.X", "Go", 1, 2) == "1-2"


@pytest.mark.parametrize("method", ["%New", "%ExistsId", "%Save"])
def test_a_percent_method_is_refused_on_every_host(host, method):
    """Through the wgproto worker a %-method that really runs segfaults it (Mindwalk, Build 154); under irispython it
    must be spelled with an underscore. The interface has no %-method surface; existence is asked with class_exists."""
    with pytest.raises(H.HostError, match="class_exists|method_exists|percent|%"):
        host.call("%Dictionary.CompiledClass", method, "X")


# ---------------------------------------------------------------------------------------------- large replies


def test_call_large_reassembles_a_chunked_reply(client):
    native, _ = client
    native.replies[("Graph.KG.X", "Big")] = lambda: "CHUNKED:tag7:3"
    native.replies[("Graph.KG.X", "ReadLargeOutChunk")] = lambda tag, i: f"[{tag}:{i}]"

    assert H.host_for(object()).call_large("Graph.KG.X", "Big") == "[tag7:1][tag7:2][tag7:3]"


def test_call_large_returns_a_plain_reply_untouched(client):
    native, _ = client
    native.replies[("Graph.KG.X", "Small")] = lambda: '{"a":1}'

    assert H.host_for(object()).call_large("Graph.KG.X", "Small") == '{"a":1}'


def test_call_large_decodes_the_sorted_marker_for_an_empty_tree(client):
    native, _ = client
    native.replies[("Graph.KG.NKGAccel", "BFSJson")] = lambda *a: "SORTED:0"

    assert H.host_for(object()).call_large("Graph.KG.NKGAccel", "BFSJson", "n") == "[]"


# --------------------------------------------------------------------------------------------- class existence


class Cursor:
    def __init__(self, count):
        self.count, self.sql = count, []

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), list(params or [])))

    def fetchone(self):
        return (self.count,)

    def close(self):
        pass


class SqlConn:
    def __init__(self, count):
        self.cursor_ = Cursor(count)

    def cursor(self):
        return self.cursor_


def test_class_and_method_existence_are_asked_in_sql_on_the_connection(client):
    conn = SqlConn(1)
    host = H.host_for(conn)

    assert host.class_exists("Graph.KG.ArnoAccel") is True
    assert host.method_exists("Graph.KG.Eraser", "EraseNodeIds") is True
    assert [p for _, p in conn.cursor_.sql] == [["Graph.KG.ArnoAccel"], ["Graph.KG.Eraser", "EraseNodeIds"]]
    assert all("CompiledClass" in s or "CompiledMethod" in s for s, _ in conn.cursor_.sql)


def test_existence_works_on_the_in_process_host_too():
    conn = EmbeddedConnection(iris_sql=None)
    conn.cursor = lambda: Cursor(0)  # the embedded cursor is covered elsewhere

    assert H.host_for(conn).class_exists("Nope") is False
