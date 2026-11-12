"""tests/conftest.py recompiles only the ObjectScript classes whose source changed since the last deploy, over TCP
(4.7.0: no `iris session`, which Cortex XDR kills)."""

import re

import pytest

from tests.conftest import classes_to_deploy


def test_everything_is_deployed_when_nothing_is_recorded():
    assert classes_to_deploy({"A.cls": "h1", "B/C.cls": "h2"}, {}) == ["A.cls", "B/C.cls"]


def test_an_unchanged_class_is_skipped():
    sources = {"A.cls": "h1", "B.cls": "h2"}
    assert classes_to_deploy(sources, {"A.cls": "h1", "B.cls": "h2"}) == []


def test_a_changed_or_new_class_is_deployed():
    sources = {"A.cls": "h1", "B.cls": "h2new", "C.cls": "h3"}
    assert classes_to_deploy(sources, {"A.cls": "h1", "B.cls": "h2old"}) == ["B.cls", "C.cls"]


def test_a_class_removed_from_the_source_is_not_deployed():
    assert classes_to_deploy({"A.cls": "h1"}, {"A.cls": "h1", "Gone.cls": "x"}) == []


# ------------------------------------------------------------------ 4.6.0 R235: load order and load status


def test_the_sql_procedure_forwarder_loads_last():
    """Graph.KG.SqlProcs projects every SQL procedure (R084). In a namespace upgraded from before R084, the old
    Traversal, TemporalIndex and the classes after S still project the same names until they are recompiled, so
    SqlProcs loaded in name order failed ERROR #5083 and the session ran without it (fhirfix; the FHIR fixture's
    deploy_sources orders it the same way)."""
    sources = {"Graph/KG/Traversal.cls": "t", "Graph/KG/SqlProcs.cls": "s", "Graph/KG/Arno.cls": "a",
               "Graph/KG/TemporalIndex.cls": "x"}
    order = classes_to_deploy(sources, {})
    assert order[-1] == "Graph/KG/SqlProcs.cls"
    assert order[:-1] == sorted(order[:-1])
    unchanged = classes_to_deploy(sources, {"Graph/KG/SqlProcs.cls": "s"})
    assert unchanged == sorted(set(sources) - {"Graph/KG/SqlProcs.cls"})


# ------------------------------------------------------------------ 4.7.0: over TCP, no `iris session`


class _Stream:
    def __init__(self):
        self.text = ""

    def invoke(self, method, *args):
        if method == "Write":
            self.text += args[0]


class _Native:
    """The Native API handle _deploy_objectscript uses: the source sent as a stream and loaded with LoadStream, the
    schema record and the deploy hashes read and written as globals. Only names `iris.IRIS` has."""

    def __init__(self, fail=None, schema=None):
        self.fail, self.loaded, self.flags = fail or {}, [], set()
        self.globals = {("^IVG.Deploy", "schema"): schema} if schema else {}

    def classMethodObject(self, cls, method, *args):
        assert (cls, method) == ("%Stream.GlobalCharacter", "%New")
        return _Stream()

    def classMethodValue(self, cls, method, *args):
        if (cls, method) == ("%SYSTEM.OBJ", "LoadStream"):
            stream, flags = args
            name = re.search(r"^Class\s+([\w.%]+)", stream.text, re.M).group(1)
            rel = name.replace(".", "/") + ".cls"
            self.loaded.append(rel)
            self.flags.add(flags)
            return self.fail.get(rel, 1)
        if (cls, method) == ("%SYSTEM.Status", "GetErrorText"):
            return args[0]
        raise AssertionError((cls, method))

    def get(self, name, *subs):
        return self.globals.get((name, *subs))

    def set(self, value, name, *subs):
        self.globals[(name, *subs)] = value


def _deploy(native):
    from unittest import mock

    import tests.conftest as cf

    def no_subprocess(*a, **k):
        raise AssertionError(f"the deploy ran a process: {a}")

    with mock.patch.object(cf.subprocess, "run", no_subprocess):
        cf._deploy_objectscript("ivg-iris-enterprise", "IVG460FXY", native=native)


def test_every_class_loads_with_sql_procs_last():
    native = _Native()
    _deploy(native)
    assert native.loaded[-1] == "Graph/KG/SqlProcs.cls"
    assert "Graph/KG/Traversal.cls" in native.loaded
    assert native.flags == {"ck-d"}


def test_a_loaded_class_records_its_hash_and_is_not_loaded_again():
    from tests.conftest import _source_hash

    native = _Native()
    _deploy(native)
    first = len(native.loaded)
    assert native.get("^IVGDeployed", "IVG460FXY", "Graph/KG/Traversal.cls") == _source_hash(
        "iris_src/src/Graph/KG/Traversal.cls")
    _deploy(native)
    assert len(native.loaded) == first


def test_a_class_that_fails_to_load_fails_the_deploy_naming_it():
    err = ("ERROR #5083: Stored procedure name is not unique: DijkstraPath, projected from "
           "Graph.KG.SqlProcs.DijkstraPath (function) and Graph.KG.Traversal.DijkstraProc (function)")
    native = _Native(fail={"Graph/KG/SqlProcs.cls": err})
    with pytest.raises(RuntimeError, match=r"Graph/KG/SqlProcs\.cls.*#5083"):
        _deploy(native)
    assert native.get("^IVGDeployed", "IVG460FXY", "Graph/KG/SqlProcs.cls") is None


def test_a_namespace_recorded_under_another_schema_is_refused_before_loading():
    from iris_vector_graph.schema_name import IVGSchemaError

    with pytest.raises(IVGSchemaError):
        _deploy(_Native(schema="IVGALT"))


