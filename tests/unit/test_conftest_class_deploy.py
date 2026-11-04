"""tests/conftest.py recompiles only the ObjectScript classes whose source changed since the last deploy."""

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


class _Docker:
    """subprocess.run as _deploy_objectscript calls it: docker exec/cp, an IRIS session answering per class."""

    def __init__(self, fail=None, silent=None):
        self.fail, self.silent, self.loaded = fail or {}, silent or set(), []

    def __call__(self, argv, input=None, **kw):
        import subprocess

        out = ""
        if input and "IVGSCHEMA=" in input:
            out = "IVGSCHEMA=\n"
        elif input and "$system.OBJ.Load(" in input:
            rel = input.split('$system.OBJ.Load("', 1)[1].split('"', 1)[0].split("/", 3)[3]
            self.loaded.append(rel)
            if rel in self.silent:
                out = "USER>\n"
            elif rel in self.fail:
                out = f"IVGLOAD=ERR {self.fail[rel]}\n"
            else:
                out = "IVGLOAD=OK\n"
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")


def _deploy(docker):
    from unittest import mock

    import tests.conftest as cf

    with mock.patch.object(cf.subprocess, "run", docker), \
            mock.patch.object(cf, "_recorded_deploy_hashes", lambda *a, **k: {}):
        cf._deploy_objectscript("ivg-iris-enterprise", "IVG460FXY")


def test_every_class_loads_with_sql_procs_last():
    docker = _Docker()
    _deploy(docker)
    assert docker.loaded[-1] == "Graph/KG/SqlProcs.cls"
    assert "Graph/KG/Traversal.cls" in docker.loaded


def test_a_class_that_fails_to_load_fails_the_deploy_naming_it():
    err = ("ERROR #5083: Stored procedure name is not unique: DijkstraPath, projected from "
           "Graph.KG.SqlProcs.DijkstraPath (function) and Graph.KG.Traversal.DijkstraProc (function)")
    with pytest.raises(RuntimeError, match=r"Graph/KG/SqlProcs\.cls.*#5083"):
        _deploy(_Docker(fail={"Graph/KG/SqlProcs.cls": err}))


def test_a_load_that_reports_nothing_fails_the_deploy():
    with pytest.raises(RuntimeError, match=r"Graph/KG/Traversal\.cls"):
        _deploy(_Docker(silent={"Graph/KG/Traversal.cls"}))
