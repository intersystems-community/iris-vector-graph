"""deploy_packaged_classes inside IRIS (irispython), where ``iris.createIRIS`` does not exist.

Reported by Mindwalk against 4.2.0: ``initialize_schema()`` on an ``EmbeddedConnection`` logged
"Packaged ObjectScript deploy failed: Cannot call an iris.package wrapper ... createIRIS" and
deployed nothing. The fake ``iris`` module below has ``cls`` and ``gref`` (what a process inside
IRIS has) and no ``createIRIS``.
"""

from __future__ import annotations

import sys
import types

import pytest

from iris_vector_graph._engine import class_deploy as cd
from iris_vector_graph.embedded import EmbeddedConnection


class FakeStream:
    def __init__(self):
        self.text = ""

    def Write(self, chunk):
        self.text += chunk

    def Rewind(self):
        pass


class FakeServer:
    """The ObjectScript side: classes loaded, compiled, and the global the marker lives in."""

    def __init__(self, existing=()):
        self.loaded = {}
        self.compiled = []
        self.existing = set(existing)
        self.globals = {}

    def cls(self, name):
        server = self

        class Cls:
            def __getattr__(self_inner, method):
                def call(*args):
                    return server.call(name, method, args)

                return call

        return Cls()

    def call(self, cls, method, args):
        method = "%" + method[1:] if method.startswith("_") else method  # as irispython spells it
        if (cls, method) == ("%SYSTEM.SYS", "NameSpace"):
            return "MINDWALK"
        if (cls, method) == ("%SYS.Python", "Import"):
            return object()
        if (cls, method) == ("%Dictionary.CompiledClass", "%ExistsId"):
            return 1 if args[0] in self.existing else 0
        if (cls, method) == ("%Stream.GlobalCharacter", "%New"):
            return FakeStream()
        if (cls, method) == ("%SYSTEM.OBJ", "LoadStream"):
            stream = args[0]
            assert isinstance(stream, FakeStream), "the stream must reach ObjectScript unwrapped"
            first = stream.text.splitlines()[0]
            self.loaded[first.split()[1]] = stream.text
            return 1
        if (cls, method) == ("%SYSTEM.OBJ", "CompileList"):
            self.compiled.append(args[0])
            return 1
        if (cls, method) == ("%SYSTEM.Status", "IsOK"):
            return 1
        raise AssertionError(f"unexpected call {cls}.{method}{args}")

    def gref(self, name):
        store = self.globals

        class Ref:
            def __getitem__(self, subs):
                return store.get((name, subs))

            def __setitem__(self, subs, value):
                store[(name, subs)] = value

        return Ref()


@pytest.fixture
def server(monkeypatch, tmp_path):
    srv = FakeServer(existing={cd.SENTINEL_CLASS})
    fake = types.ModuleType("iris")
    fake.cls = srv.cls
    fake.gref = srv.gref  # no createIRIS: a process inside IRIS does not have one
    monkeypatch.setitem(sys.modules, "iris", fake)
    classes = tmp_path / "Graph" / "KG"
    classes.mkdir(parents=True)
    (classes / "Alpha.cls").write_text("Class Graph.KG.Alpha Extends %RegisteredObject\n{\n}\n")
    monkeypatch.setattr(cd, "packaged_class_dir", lambda: tmp_path)
    return srv


def test_deploys_the_packaged_classes_without_createiris(server):
    result = cd.deploy_packaged_classes(EmbeddedConnection())
    assert result.errors == []
    assert result.deployed and not result.unchanged
    assert list(server.loaded) == ["Graph.KG.Alpha"]
    assert server.compiled[0] == "Graph.KG.Alpha.cls"


def test_the_second_deploy_is_a_no_op_once_the_marker_is_written(server):
    cd.deploy_packaged_classes(EmbeddedConnection())
    assert server.globals[("^IVG.Deploy", "fingerprint")], "the marker was not written"
    loaded = dict(server.loaded)
    again = cd.deploy_packaged_classes(EmbeddedConnection())
    assert again.unchanged and server.loaded == loaded


def test_percent_methods_are_spelled_with_an_underscore_inside_iris():
    """Measured under irispython: ``iris.cls(...).%New`` is ``SystemError``, ``_New`` works."""
    from iris_vector_graph.embedded import EmbeddedIRIS

    seen = []

    class Cls:
        def __getattr__(self, name):
            seen.append(name)
            return lambda *a: 1

    fake = types.SimpleNamespace(cls=lambda n: Cls())
    e = EmbeddedIRIS(fake)
    e.classMethodValue("%Dictionary.CompiledClass", "%ExistsId", "X")
    e.classMethodObject("%Stream.GlobalCharacter", "%New")
    e.classMethodVoid("A.B", "Plain")
    assert seen == ["_ExistsId", "_New", "Plain"]


def test_embedded_iris_reads_and_writes_a_global_node():
    from iris_vector_graph.embedded import EmbeddedIRIS

    store = {}

    class Ref:
        def __getitem__(self, k):
            return store.get(k)

        def __setitem__(self, k, v):
            store[k] = v

    e = EmbeddedIRIS(types.SimpleNamespace(gref=lambda name: Ref()))
    assert e.get("^G", "a") is None
    e.set("v", "^G", "a")
    e.set("w", "^G", "a", "b")
    assert (e.get("^G", "a"), e.get("^G", "a", "b")) == ("v", "w")
