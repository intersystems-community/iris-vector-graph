"""4.6.0 (NR6, NR11, NR18): `initialize_schema()` on a Community-like server says one thing, at INFO.

The walkthrough's first `initialize_schema()` (IRIS Community 2026.1: no %AI, no Arno library, an empty namespace)
printed about 15 lines: "Arno class not found in namespace USER" before the deploy created the class, each MCP class
twice (INFO, then WARNING), "^KG adjacency index not built ... from iris_vector_graph.schema import
_call_classmethod" right after the adjacency index that keeps ^KG current was declared, and, in another namespace,
"Namespace mismatch: connected to 'IVG2', expected 'USER'". None of them was something to act on.

Here the engine runs its real `initialize_schema()` over a connection to namespace IVG2 whose SQL succeeds and finds
nothing; the deploy reports what a Community server's does. The log has one INFO summary (namespace, IVG Schema,
classes deployed, classes skipped and why) and no WARNING.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph._engine.class_deploy import DeployResult
from iris_vector_graph.capabilities import IRISCapabilities
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema

SKIPPED = {
    "Graph.KG.MCPService": "needs %AI.MCP.Service, not installed on this server",
    "Graph.KG.MCPToolSet": "needs %AI.Policy.ConsoleAudit, %AI.ToolSet, not installed on this server",
    "Graph.KG.MCPTools": "needs %AI.Tool, not installed on this server",
}
COMPILED = [f"Graph.KG.C{i:02d}" for i in range(65)]


class _Cursor:
    """SQL that succeeds and finds nothing: an empty namespace with no Arno class."""

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
    """The Native API calls setup makes, answered as Community IRIS without the Arno library does."""

    def classMethodValue(self, cls, method, *args):
        if (cls, method) == ("%SYSTEM.SYS", "NameSpace"):
            return "IVG2"
        raise RuntimeError(f"<CLASS DOES NOT EXIST> *{cls}")

    def classMethodVoid(self, cls, method, *args):
        self.classMethodValue(cls, method, *args)

    def get(self, *subscripts):
        """An empty namespace: no global holds anything (spec 262 reads ^IVG.Deploy("schema"))."""
        return None

    def set(self, value, *subscripts):
        pass


def _deploy(cursor, path, conn=None, force=None, schema=None):
    GraphSchema.last_deploy = DeployResult(deployed=True, compiled=list(COMPILED), skipped=dict(SKIPPED))
    return IRISCapabilities(objectscript_deployed=True)


@pytest.fixture
def run(caplog):
    conn = MagicMock(spec=["cursor", "commit", "rollback", "close"])
    conn.cursor.side_effect = _Cursor
    with patch("iris.createIRIS", lambda c: _Native(), create=False), \
            patch.object(GraphSchema, "deploy_objectscript_classes", staticmethod(_deploy)), \
            patch.object(GraphSchema, "ensure_adjacency_index",
                         staticmethod(lambda cur: {"idx_edges_adj": "created", "idx_labels_adj": "created"})), \
            patch.object(GraphSchema, "bootstrap_kg_global", staticmethod(lambda cur, conn=None: False)), \
            caplog.at_level(logging.DEBUG):
        engine = IRISGraphEngine(conn, embedding_dimension=768)
        status = engine.initialize_schema()
    return engine, status, [r for r in caplog.records if r.levelno >= logging.INFO]


def _summary(records):
    return [r for r in records if r.levelno == logging.INFO and r.getMessage().startswith("IVG ready")]


def test_one_info_summary_names_the_namespace_schema_and_classes(run):
    _, _, records = run
    summary = _summary(records)
    assert len(summary) == 1, [r.getMessage() for r in records]
    text = summary[0].getMessage()
    assert "namespace IVG2" in text
    assert "IVG Schema Graph_KG" in text
    assert "65 classes deployed" in text
    for name, reason in SKIPPED.items():
        assert f"{name} ({reason})" in text


def test_no_warning(run):
    _, _, records = run
    assert [r.getMessage() for r in records if r.levelno >= logging.WARNING] == []


def test_nothing_names_a_private_function_or_a_namespace_mismatch(run):
    _, status, records = run
    lines = [r.getMessage() for r in records] + list(status["warnings"])
    assert not [m for m in lines if "_call_classmethod" in m]
    assert not [m for m in lines if "Namespace mismatch" in m]


def test_the_namespace_is_the_connection_s(run):
    engine, _, _ = run
    assert engine.namespace == "IVG2"


def test_the_skipped_classes_are_still_in_the_status(run):
    _, status, _ = run
    assert status["objectscript_skipped"] == SKIPPED


def test_the_driver_error_class_is_read_without_importing_intersystems_iris(monkeypatch):
    # importing intersystems_iris asks `iris` for IRISNative, IRISError, context, context_info: outside IRIS the
    # embedded-Python wrapper answered each with a MagicMock and a WARNING (NR18)
    import sys
    import types

    from iris_vector_graph._engine import schema as schema_mod

    class DriverError(Exception):
        pass

    dbapi = types.ModuleType("iris.dbapi")
    dbapi.Error = DriverError
    monkeypatch.setitem(sys.modules, "iris.dbapi", dbapi)
    monkeypatch.delitem(sys.modules, "intersystems_iris", raising=False)
    monkeypatch.delitem(sys.modules, "intersystems_iris.dbapi", raising=False)
    assert schema_mod._alter_tolerated_errors() == (DriverError,)
    assert "intersystems_iris" not in sys.modules


def test_ivg_s_own_import_of_iris_keeps_the_wrapper_quiet(monkeypatch, caplog):
    import sys
    import types

    from iris_vector_graph import _iris_compat

    class Finder:
        """An `iris` whose import logs what the wrapper logs outside IRIS."""

        def find_spec(self, name, path=None, target=None):
            if name != "iris":
                return None
            import importlib.machinery

            return importlib.machinery.ModuleSpec(name, self)

        def create_module(self, spec):
            return types.ModuleType(spec.name)

        def exec_module(self, module):
            logging.getLogger("_iris_ep._runtime_facade").warning(
                "IRISINSTALLDIR or ISC_PACKAGE_INSTALLDIR environment variable is not set"
            )

    monkeypatch.delenv("IRISINSTALLDIR", raising=False)
    monkeypatch.delenv("ISC_PACKAGE_INSTALLDIR", raising=False)
    monkeypatch.delitem(sys.modules, "iris")
    monkeypatch.setattr(sys, "meta_path", [Finder()] + sys.meta_path)
    with caplog.at_level(logging.INFO):
        module = _iris_compat._import_iris_quietly()
    assert module is sys.modules["iris"]
    assert not [r for r in caplog.records if r.name.startswith("_iris_ep")]
    assert logging.getLogger("_iris_ep").level == logging.NOTSET  # put back


def test_deployed_classes_without_the_library_are_not_arno_and_are_said_once_at_info(monkeypatch, caplog):
    from iris_vector_graph.stores import iris_sql_store as store_mod

    class Native:
        def classMethodValue(self, cls, method, *args):
            if (cls, method) == ("%SYSTEM.SYS", "NameSpace"):
                return "IVG2"
            if (cls, method) == ("Graph.KG.NKGAccel", "Capabilities"):
                return '{"rust_callout": false, "nkg_data": false}'
            if (cls, method) == ("Graph.KG.NKGAccel", "IsLoaded"):
                return 0
            return 0  # Load: 0, the library is not there

    monkeypatch.setattr(store_mod, "compiled_class_exists", lambda conn, name: True)
    monkeypatch.setattr(store_mod, "_ARNO_ABSENT_LOGGED", [False])
    with caplog.at_level(logging.INFO):
        for _ in range(2):
            store = store_mod.IRISGraphStore(MagicMock())
            store._iris_obj = lambda: Native()
            store._reload_arno_if_needed = lambda obj: False
            assert store._detect_arno() is False
    said = [r for r in caplog.records if "Arno" in r.getMessage()]
    assert [r.levelno for r in said] == [logging.INFO]
