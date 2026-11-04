"""4.6.0 R173: every entry point that deploys IVG's classes or creates its SQL objects refuses a namespace recorded
under another IVG Schema before it does anything (FR-006).

fixes17 saw IVG460FXD, recorded as `RAG`, get the unrewritten `Graph_KG` schema classes (`Graph.KG.SqlProcs`,
`LedgerRevision`, `LedgerStats`). `initialize_schema()` compared the record first, except when the engine had no
`embedding_dimension` (a ValueError, not the refusal). The other entry points never read the record:

* `class_deploy.deploy_packaged_classes(conn)` (schema defaults to `Graph_KG`) loaded and compiled every class;
* `GraphSchema.deploy_objectscript_classes(...)` staged a class-owned `rdf_edges` first, and when the packaged deploy
  failed it fell back to `$system.OBJ.LoadDir` on a server directory, which would have turned a refusal into a load;
* the test session's `_deploy_objectscript` (tests/conftest.py) loads `iris_src/src` as written, naming `Graph_KG`.

The fakes record every call, so "nothing ran" is what the call list says.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import iris
import pytest

from iris_vector_graph import IRISGraphEngine, IVGSchemaError

RECORD = ("^IVG.Deploy", "schema")


class RecordedNative:
    """The record and `%SYSTEM.SYS.NameSpace`; any other call is recorded and refused."""

    def __init__(self, record="RAG", namespace="IVG460FXE"):
        self.data = {RECORD: record} if record is not None else {}
        self.namespace = namespace
        self.calls: list = []

    def get(self, gname, *subs):
        self.calls.append(("get", gname, *subs))
        return self.data.get((gname, *subs))

    def classMethodValue(self, cls, method, *args):
        self.calls.append((cls, method, *args))
        if (cls, method) == ("%SYSTEM.SYS", "NameSpace"):
            return self.namespace
        raise AssertionError(f"ran {cls}.{method}{args} on a namespace recorded as {self.data.get(RECORD)}")

    def classMethodObject(self, cls, method, *args):
        self.calls.append((cls, method, *args))
        raise AssertionError(f"ran {cls}.{method}{args} on a namespace recorded as {self.data.get(RECORD)}")

    def set(self, value, gname, *subs):
        self.calls.append(("set", gname, *subs))
        raise AssertionError(f"set {gname}{subs}")

    def isDefined(self, gname, *subs):
        self.calls.append(("isDefined", gname, *subs))
        return 0

    def ran(self) -> list:
        """Everything but reading the record and the namespace name."""
        return [c for c in self.calls if c[:3] != ("get", *RECORD) and c[:2] != ("%SYSTEM.SYS", "NameSpace")]


# --------------------------------------------------------------------------- deploy_packaged_classes


def test_deploy_packaged_classes_refuses_another_schemas_namespace_before_loading(monkeypatch):
    from iris_vector_graph._engine import class_deploy

    native = RecordedNative(record="RAG")
    monkeypatch.setattr(iris, "createIRIS", lambda conn: native)
    with pytest.raises(IVGSchemaError) as raised:
        class_deploy.deploy_packaged_classes(MagicMock(spec=iris.IRISConnection))  # schema: the Graph_KG default
    assert (raised.value.recorded, raised.value.requested) == ("RAG", "Graph_KG")
    assert native.ran() == []


def test_deploy_packaged_classes_refuses_a_named_schema_that_is_not_the_record(monkeypatch):
    from iris_vector_graph._engine import class_deploy

    native = RecordedNative(record="Graph_KG")
    monkeypatch.setattr(iris, "createIRIS", lambda conn: native)
    with pytest.raises(IVGSchemaError):
        class_deploy.deploy_packaged_classes(MagicMock(spec=iris.IRISConnection), schema="RAG")
    assert native.ran() == []


@pytest.mark.parametrize("record", [None, "rag", "RAG"])
def test_deploy_packaged_classes_goes_on_when_the_record_is_absent_or_the_same_schema(monkeypatch, record):
    """No record (a first setup records after the deploy) or the same name in any case: the deploy proceeds, to the
    first step after the check (which this fake then refuses)."""
    from iris_vector_graph._engine import class_deploy

    native = RecordedNative(record=record)
    monkeypatch.setattr(iris, "createIRIS", lambda conn: native)
    monkeypatch.setattr(class_deploy, "embedded_python_available", lambda obj: (_ for _ in ()).throw(
        RuntimeError("reached the deploy")))
    with pytest.raises(RuntimeError, match="reached the deploy"):
        class_deploy.deploy_packaged_classes(MagicMock(spec=iris.IRISConnection), schema="RAG")


# --------------------------------------------------------------------------- GraphSchema.deploy_objectscript_classes


def test_deploy_objectscript_classes_refuses_before_staging_or_loading(monkeypatch):
    """The packaged deploy's refusal is not swallowed into the server-directory LoadDir fallback, and the rdf_edges
    rescue probe before it does not run."""
    from iris_vector_graph.schema import GraphSchema

    native = RecordedNative(record="RAG")
    monkeypatch.setattr(iris, "createIRIS", lambda conn: native)
    cursor = MagicMock()
    with pytest.raises(IVGSchemaError):
        GraphSchema.deploy_objectscript_classes(cursor, Path("/nonexistent"), conn=MagicMock(spec=iris.IRISConnection))
    assert cursor.execute.call_count == 0
    assert native.ran() == []


# --------------------------------------------------------------------------- initialize_schema


def _engine(native, dim):
    """A Graph_KG engine on a connection that is not the driver's (so construction skips the first-access check, as
    a stand-in), with the Native API patched to the recorded namespace."""
    conn = MagicMock()
    eng = IRISGraphEngine(conn, embedding_dimension=dim)
    conn.reset_mock()
    eng._iris_obj = lambda: native
    return eng, conn


def test_initialize_schema_without_a_dimension_refuses_before_it_asks_for_one():
    native = RecordedNative(record="RAG")
    eng, conn = _engine(native, dim=None)
    with pytest.raises(IVGSchemaError) as raised:
        eng.initialize_schema()
    assert (raised.value.recorded, raised.value.requested) == ("RAG", "Graph_KG")
    assert conn.cursor.call_count == 0 and native.ran() == []


@pytest.mark.parametrize("dry_run", [False, True])
def test_initialize_schema_refuses_before_any_sql(dry_run):
    native = RecordedNative(record="RAG")
    eng, conn = _engine(native, dim=8)
    with patch("iris_vector_graph._engine.class_deploy.deploy_packaged_classes") as deploy:
        with pytest.raises(IVGSchemaError):
            eng.initialize_schema(dry_run=dry_run)
    deploy.assert_not_called()
    assert conn.cursor.call_count == 0 and native.ran() == []


def test_without_a_record_a_missing_dimension_is_still_the_value_error():
    eng, conn = _engine(RecordedNative(record=None), dim=None)
    with pytest.raises(ValueError, match="embedding_dimension"):
        eng.initialize_schema()


# --------------------------------------------------------------------------- the test session's raw class load


def test_the_session_class_load_refuses_a_namespace_recorded_under_another_schema(monkeypatch):
    """tests/conftest.py loads iris_src/src as written (it names Graph_KG); on a namespace recorded as RAG that put
    Graph_KG schema classes beside RAG's. It reads the record first and loads nothing."""
    import tests.conftest as session

    ran: list = []
    monkeypatch.setattr(session, "_recorded_schema", lambda container, ns: "RAG")
    monkeypatch.setattr(session.subprocess, "run", lambda *a, **k: ran.append(a) or MagicMock(stdout=""))
    with pytest.raises(IVGSchemaError) as raised:
        session._deploy_objectscript("ivg-iris-enterprise", "IVG460FXE")
    assert "RAG" in str(raised.value) and "IVG460FXE" in str(raised.value)
    assert ran == []


@pytest.mark.parametrize("record", [None, "Graph_KG", "GRAPH_KG"])
def test_the_session_class_load_goes_on_for_a_graph_kg_namespace(monkeypatch, record):
    import tests.conftest as session

    ran: list = []
    monkeypatch.setattr(session, "_recorded_schema", lambda container, ns: record)
    monkeypatch.setattr(session, "_recorded_deploy_hashes", lambda container, sources, ns=None: {})
    monkeypatch.setattr(session.subprocess, "run", lambda *a, **k: ran.append(a[0]) or MagicMock(stdout=""))
    session._deploy_objectscript("ivg-iris-enterprise", "IVG460FXE")
    assert any(cmd[:2] == ["docker", "cp"] for cmd in ran)


def test_tcp_deploy_reads_the_record_before_it_writes_a_file():
    """scripts/enterprise-container.sh tcp-deploy loads iris_src/src as written, too: its Python reads
    ^IVG.Deploy("schema") and exits before the load loop when it names another schema (the live run is
    tests/e2e/test_460_r173_refusal_snapshot_e2e.py)."""
    text = (Path(__file__).resolve().parents[2] / "scripts" / "enterprise-container.sh").read_text()
    body = text.split("  tcp-deploy)", 1)[1].split("PYEOF\n    ;;", 1)[0]
    check = body.find('"^IVG.Deploy", "schema"')
    assert check != -1, "tcp-deploy does not read the IVG Schema record"
    assert check < body.find("for cls_file in cls_files")
