"""4.6.0 R299: a class that fails to deploy is in `initialize_schema()`'s reply, not only in the log.

On IVGFIX a setup logged "IVG setup: ObjectScript deploy error: IVG.Percentile: ERROR #5083: Stored procedure name is
not unique: Percentile_PCONT", a WARNING truncated to 120 characters. The reply had it only as a string in `warnings`,
the INFO summary still said "N classes deployed", and nothing keyed by class said which one failed. Two failures never
reached the reply at all: the packaged deploy raising (logged "Packaged ObjectScript deploy failed"), and the whole
deploy step raising (logged "ObjectScript auto-deploy failed").

Now: `DeployResult.failed` is `{class: server error}` for every class whose load or compile failed; the reply carries
it as `objectscript_failed`, beside `objectscript_skipped`; the summary names each failed class with its error on one
line; and both exceptions reach `warnings`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph._engine import class_deploy as cd
from iris_vector_graph._engine.class_deploy import DeployResult
from iris_vector_graph.capabilities import IRISCapabilities
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema

#: The server's text for the IVGFIX failure, as `%SYSTEM.Status.GetErrorText` returned it in a downgraded scratch
#: namespace (pre-R084 classes compiled over 4.6.0's Graph.KG.SqlProcs): two lines.
E5083 = ("ERROR #5083: Stored procedure name is not unique: Percentile_PCONT, projected from IVG.Percentile.PCONT "
         "(function) and Graph.KG.SqlProcs.PercentilePCONT (function) \r\n  > ERROR #5030: An error occurred while "
         "compiling class 'IVG.Percentile'")
PERCENTILE = "Class IVG.Percentile Extends %RegisteredObject\n{\n}\n"
OTHER = "Class Z.Code Extends %RegisteredObject\n{\n}\n"


class _Stream:
    def __init__(self):
        self.chunks = []

    def Write(self, chunk):
        self.chunks.append(chunk)

    def Rewind(self):
        pass


class _Server:
    """The Native API calls `upload_and_compile` makes. A CompileList naming a class in `bad_compile` fails (every
    pass and alone, as #5083 does); a LoadStream of a class in `bad_load` fails. Status 1 is OK, 0 is an error."""

    def __init__(self, bad_compile=(), bad_load=()):
        self.bad_compile, self.bad_load = set(bad_compile), set(bad_load)
        self.globals = {}
        self.last_error = ""

    def get(self, glo, *subs):
        return self.globals.get((glo,) + subs)

    def set(self, value, glo, *subs):
        self.globals[(glo,) + subs] = value

    def kill(self, glo, *subs):
        self.globals.pop((glo,) + subs, None)

    def isDefined(self, glo, *subs):
        return 0

    def classMethodObject(self, cls, method, *args):
        assert (cls, method) == ("%Stream.GlobalCharacter", "%New")
        return _Stream()

    def classMethodValue(self, cls, method, *args):
        if (cls, method) == ("%SYSTEM.OBJ", "LoadStream"):
            name = "".join(args[0].chunks).split()[1]
            if name in self.bad_load:
                self.last_error = f"ERROR #5373: Class '{name}' is locked by another process"
                return 0
            return 1
        if (cls, method) == ("%SYSTEM.OBJ", "CompileList"):
            named = {n[:-4] for n in args[0].split(",")}
            if named & self.bad_compile:
                self.last_error = E5083
                return 0
            return 1
        if (cls, method) == ("%SYSTEM.Status", "IsOK"):
            return 1 if args[0] == 1 else 0
        if (cls, method) == ("%SYSTEM.Status", "GetErrorText"):
            return self.last_error
        raise AssertionError(f"unexpected call {cls}.{method}{args}")


def test_a_class_that_fails_to_compile_is_in_failed_with_the_server_s_error():
    server = _Server(bad_compile={"IVG.Percentile"})
    result = cd.upload_and_compile(server, "USER", {"IVG.Percentile": PERCENTILE, "Z.Code": OTHER})
    assert result.failed == {"IVG.Percentile": E5083}
    assert result.errors == [f"IVG.Percentile: {E5083}"]
    assert cd.MARKER not in server.globals  # retried next run


def test_a_class_that_fails_to_load_is_in_failed():
    server = _Server(bad_load={"Z.Code"})
    result = cd.upload_and_compile(server, "USER", {"IVG.Percentile": PERCENTILE, "Z.Code": OTHER})
    assert result.failed == {"Z.Code": "ERROR #5373: Class 'Z.Code' is locked by another process"}


def test_a_clean_deploy_fails_nothing():
    result = cd.upload_and_compile(_Server(), "USER", {"IVG.Percentile": PERCENTILE, "Z.Code": OTHER})
    assert result.failed == {} and result.errors == []


def test_someone_elses_schema_class_is_in_failed():
    server = _Server()
    server.isDefined = lambda glo, *subs: 1 if subs == ("IVGALT.SqlProcs",) else 0
    fwd = "Class IVGALT.SqlProcs Extends %RegisteredObject\n{\nParameter IVGSCHEMACLASS = 1;\n}\n"
    result = cd.upload_and_compile(server, "USER", {"IVGALT.SqlProcs": fwd}, schema="IVGALT")
    assert list(result.failed) == ["IVGALT.SqlProcs"] and "IVG did not create" in result.failed["IVGALT.SqlProcs"]


# ----------------------------------------------------------------------------------------- the reply and the summary


class _Cursor:
    """SQL that succeeds and finds nothing."""

    description = None
    rowcount = 0

    def execute(self, sql, params=None):
        return None

    def executemany(self, sql, rows):
        return None

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def close(self):
        pass


class _Native:
    def classMethodValue(self, cls, method, *args):
        if (cls, method) == ("%SYSTEM.SYS", "NameSpace"):
            return "IVGFIX"
        raise RuntimeError(f"<CLASS DOES NOT EXIST> *{cls}")

    def classMethodVoid(self, cls, method, *args):
        self.classMethodValue(cls, method, *args)

    def get(self, *subscripts):
        return None

    def set(self, value, *subscripts):
        pass


def _setup(caplog, deploy):
    """The engine's real `initialize_schema()`, with `GraphSchema.deploy_objectscript_classes` replaced by `deploy`."""
    conn = MagicMock(spec=["cursor", "commit", "rollback", "close"])
    conn.cursor.side_effect = _Cursor
    with patch("iris.createIRIS", lambda c: _Native()), \
            patch.object(GraphSchema, "deploy_objectscript_classes", staticmethod(deploy)), \
            patch.object(GraphSchema, "ensure_adjacency_index",
                         staticmethod(lambda cur: {"idx_edges_adj": "created", "idx_labels_adj": "created"})), \
            patch.object(GraphSchema, "bootstrap_kg_global",
                         staticmethod(lambda cur, conn=None, schema="Graph_KG": False)), \
            caplog.at_level(logging.INFO):
        status = IRISGraphEngine(conn, embedding_dimension=8).initialize_schema()
    return status, [r for r in caplog.records if r.levelno >= logging.INFO]


def _percentile_failed(cursor, path, conn=None, force=None, schema=None):
    GraphSchema.last_deploy = DeployResult(deployed=True, compiled=["Graph.KG.PageRank", "IVG.Percentile"],
                                           errors=[f"IVG.Percentile: {E5083}"], failed={"IVG.Percentile": E5083})
    return IRISCapabilities(objectscript_deployed=True)


def test_the_reply_names_the_class_that_failed(caplog):
    status, _ = _setup(caplog, _percentile_failed)
    assert status["objectscript_failed"] == {"IVG.Percentile": E5083}
    assert status["objectscript_skipped"] == {}
    assert any(w.startswith("ObjectScript deploy error: IVG.Percentile: ERROR #5083") for w in status["warnings"])


def test_the_summary_names_the_failed_class_and_its_error_on_one_line(caplog):
    _, records = _setup(caplog, _percentile_failed)
    (summary,) = [r.getMessage() for r in records if r.getMessage().startswith("IVG ready")]
    assert "1 class not compiled: IVG.Percentile (ERROR #5083: Stored procedure name is not unique: " \
           "Percentile_PCONT" in summary
    assert "\r" not in summary and "\n" not in summary


def test_a_clean_deploy_replies_no_failed_class(caplog):
    def clean(cursor, path, conn=None, force=None, schema=None):
        GraphSchema.last_deploy = DeployResult(deployed=True, compiled=["Graph.KG.PageRank"])
        return IRISCapabilities(objectscript_deployed=True)

    status, records = _setup(caplog, clean)
    assert status["objectscript_failed"] == {}
    (summary,) = [r.getMessage() for r in records if r.getMessage().startswith("IVG ready")]
    assert "not compiled" not in summary


def test_a_deploy_step_that_raises_reaches_the_warnings(caplog):
    def raises(cursor, path, conn=None, force=None, schema=None):
        GraphSchema.last_deploy = None
        raise RuntimeError("<COMMUNICATION LINK ERROR>")

    status, _ = _setup(caplog, raises)
    assert [w for w in status["warnings"] if w.startswith("ObjectScript deploy failed: ")] == [
        "ObjectScript deploy failed: <COMMUNICATION LINK ERROR>"]


# ------------------------------------------------------------------------------------- the packaged deploy raising


@pytest.fixture
def _nothing_to_rescue():
    with patch.object(GraphSchema, "stage_class_owned_rdf_edges", return_value=None), \
            patch.object(GraphSchema, "restore_rescued_rdf_edges", return_value=None):
        yield


def test_a_packaged_deploy_that_raises_is_recorded_for_the_reply(_nothing_to_rescue):
    """`deploy_packaged_classes` raising used to leave `last_deploy` None: the reply had nothing to report."""
    with patch("iris_vector_graph._engine.class_deploy.deploy_packaged_classes",
               side_effect=RuntimeError("<COMMUNICATION LINK ERROR>")), \
            patch("iris_vector_graph._engine.class_deploy.native_for", side_effect=TypeError("no Native API")), \
            patch("iris_vector_graph.schema._call_classmethod"), \
            patch.object(GraphSchema, "check_objectscript_classes", return_value=IRISCapabilities()):
        GraphSchema.deploy_objectscript_classes(MagicMock(), Path("/nonexistent/iris_src"))
    deploy = GraphSchema.last_deploy
    assert deploy is not None and not deploy.deployed
    assert deploy.errors == ["packaged class deploy failed: <COMMUNICATION LINK ERROR>"]


# --------------------------------------------------------------------- the "unchanged" skip after someone else's deploy


class _Compiled(_Server):
    """A server on which the classes in `missing` are not compiled (%Dictionary.CompiledClass has no row)."""

    def __init__(self, missing=(), **kw):
        super().__init__(**kw)
        self.missing = set(missing)
        self.compiles = []

    def classMethodValue(self, cls, method, *args):
        if (cls, method) == ("%Dictionary.CompiledClass", "%ExistsId"):
            return 0 if args[0] in self.missing else 1
        if (cls, method) == ("%SYSTEM.OBJ", "CompileList"):
            self.compiles.append(args[0])
        return super().classMethodValue(cls, method, *args)


SOURCES = {"Graph.KG.PageRank": "Class Graph.KG.PageRank Extends %RegisteredObject\n{\n}\n",
           "IVG.Percentile": PERCENTILE}


def test_a_matching_marker_with_every_class_compiled_skips():
    server = _Compiled()
    server.globals[cd.MARKER] = cd.fingerprint(SOURCES)
    result = cd.upload_and_compile(server, "USER", SOURCES)
    assert result.unchanged and server.compiles == []


def test_a_matching_marker_with_a_class_not_compiled_redeploys():
    """Measured (fixes49): pre-R084 code's setup over a 4.6.0 namespace failed #5083 on IVG.Percentile and nine more,
    which left them uncompiled and 4.6.0's marker in place, so the next 4.6.0 setup skipped as unchanged and
    `Graph_KG.Percentile_PCONT` failed <CLASS DOES NOT EXIST> *IVG.Percentile."""
    server = _Compiled(missing={"IVG.Percentile"})
    server.globals[cd.MARKER] = cd.fingerprint(SOURCES)
    result = cd.upload_and_compile(server, "USER", SOURCES)
    assert not result.unchanged and result.deployed and server.compiles


def test_a_failed_deploy_leaves_no_marker_of_the_one_before():
    """A forced deploy that fails over a namespace the same source set deployed cleanly before must not leave that
    marker: the next setup would skip over what the failure left behind."""
    server = _Compiled(bad_compile={"IVG.Percentile"})
    server.globals[cd.MARKER] = cd.fingerprint(SOURCES)
    result = cd.upload_and_compile(server, "USER", SOURCES, force=True)
    assert result.failed and server.globals.get(cd.MARKER) != cd.fingerprint(SOURCES)
