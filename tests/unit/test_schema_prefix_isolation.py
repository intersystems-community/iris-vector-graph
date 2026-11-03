"""Tests for per-instance schema_prefix isolation.

Root cause of the bug: schema_prefix was a process-global (module-level variable
in cypher/translator.py). Any code that instantiated engines from two different
schemas in one process (e.g. hipporag2 sets "RAG", then LOS engine queries and
hits RAG.nodes instead of Graph_KG.nodes) got SQLCODE -30 on every read.

Fix: IRISGraphEngine stores _schema_prefix as instance state. All SQL generation
in _engine/ mixins calls self._t(name) which passes the instance prefix to
_table(name, prefix=...) rather than reading the module global.
"""
from unittest.mock import MagicMock, patch, call

import pytest

from iris_vector_graph.cypher.translator import _table, get_schema_prefix, set_schema_prefix


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(schema_prefix="Graph_KG"):
    from iris_vector_graph.engine import IRISGraphEngine

    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    conn.cursor.return_value = cursor

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = conn
    engine._schema_prefix = schema_prefix
    return engine, cursor


# ---------------------------------------------------------------------------
# _table() free function
# ---------------------------------------------------------------------------

class TestTableFreeFunction:
    def test_uses_module_global_when_no_prefix_arg(self):
        set_schema_prefix("Global_KG")
        assert _table("nodes") == "Global_KG.nodes"

    def test_prefix_arg_overrides_global(self):
        set_schema_prefix("Global_KG")
        assert _table("nodes", prefix="Override") == "Override.nodes"

    def test_empty_prefix_returns_unqualified(self):
        assert _table("nodes", prefix="") == "nodes"

    def test_none_prefix_falls_back_to_global(self):
        set_schema_prefix("Fallback")
        assert _table("nodes", prefix=None) == "Fallback.nodes"

    def teardown_method(self, _):
        set_schema_prefix("")


# ---------------------------------------------------------------------------
# IRISGraphEngine constructor
# ---------------------------------------------------------------------------

class TestEngineConstructorSchemaPrefix:
    def test_default_schema_prefix_is_graph_kg(self):
        engine, _ = _make_engine()
        assert engine._schema_prefix == "Graph_KG"

    def test_custom_schema_prefix_stored_on_instance(self):
        engine, _ = _make_engine("RAG")
        assert engine._schema_prefix == "RAG"

    def test_constructor_schema_prefix_param(self):
        from iris_vector_graph.engine import IRISGraphEngine

        conn = MagicMock()
        conn.cursor.return_value = MagicMock()
        # Patch heavy init steps
        with patch.object(IRISGraphEngine, "_build_index_registry", return_value={}), \
             patch.object(IRISGraphEngine, "_detect_stored_vector_dtype", return_value="DOUBLE"), \
             patch("iris_vector_graph.stores.iris_sql_store.IRISGraphStore", MagicMock()):
            engine = IRISGraphEngine(conn, schema_prefix="MySchema")
        assert engine._schema_prefix == "MySchema"

    def test_constructor_default_schema_prefix(self):
        from iris_vector_graph.engine import IRISGraphEngine

        conn = MagicMock()
        conn.cursor.return_value = MagicMock()
        with patch.object(IRISGraphEngine, "_build_index_registry", return_value={}), \
             patch.object(IRISGraphEngine, "_detect_stored_vector_dtype", return_value="DOUBLE"), \
             patch("iris_vector_graph.stores.iris_sql_store.IRISGraphStore", MagicMock()):
            engine = IRISGraphEngine(conn)
        assert engine._schema_prefix == "Graph_KG"


class TestSpec262SchemaResolution:
    """Spec 262 T011: `schema_prefix=None` resolves through IVG_SCHEMA_PREFIX, and a bad name raises before any
    database call (FR-003, FR-004)."""

    @staticmethod
    def _build(monkeypatch, env=None, **kwargs):
        from iris_vector_graph.engine import IRISGraphEngine

        monkeypatch.delenv("IVG_SCHEMA_PREFIX", raising=False)
        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)
        conn = MagicMock()
        with patch.object(IRISGraphEngine, "_build_index_registry", return_value={}), \
             patch.object(IRISGraphEngine, "_detect_stored_vector_dtype", return_value="DOUBLE"), \
             patch("iris_vector_graph.stores.iris_sql_store.IRISGraphStore", MagicMock()):
            return IRISGraphEngine(conn, **kwargs), conn

    def test_none_reads_the_environment(self, monkeypatch):
        engine, _ = self._build(monkeypatch, {"IVG_SCHEMA_PREFIX": "ENVNAME"})
        assert engine._schema_prefix == "ENVNAME"

    def test_the_argument_wins(self, monkeypatch):
        engine, _ = self._build(monkeypatch, {"IVG_SCHEMA_PREFIX": "ENVNAME"}, schema_prefix="ARGNAME")
        assert engine._schema_prefix == "ARGNAME"

    def test_iris_schema_prefix_is_ignored(self, monkeypatch):
        engine, _ = self._build(monkeypatch, {"IRIS_SCHEMA_PREFIX": "RAG"})
        assert engine._schema_prefix == "Graph_KG"

    @pytest.mark.parametrize("bad", ["", "1abc", "a-b", "SQLUser", "%X", "IVG"])
    def test_an_invalid_name_raises_before_any_database_call(self, monkeypatch, bad):
        from iris_vector_graph.engine import IRISGraphEngine

        conn = MagicMock()
        with patch.object(IRISGraphEngine, "_detect_stored_vector_dtype") as detect, \
             pytest.raises(ValueError, match="not a valid IVG Schema name"):
            IRISGraphEngine(conn, schema_prefix=bad)
        detect.assert_not_called()
        conn.cursor.assert_not_called()


# ---------------------------------------------------------------------------
# _t() bound method
# ---------------------------------------------------------------------------

class TestTBoundMethod:
    def test_t_uses_instance_prefix(self):
        engine, _ = _make_engine("Graph_KG")
        assert engine._t("nodes") == "Graph_KG.nodes"

    def test_t_uses_rag_prefix(self):
        engine, _ = _make_engine("RAG")
        assert engine._t("nodes") == "RAG.nodes"

    def test_t_empty_prefix_returns_unqualified(self):
        engine, _ = _make_engine("")
        assert engine._t("nodes") == "nodes"

    def test_t_validates_table_name(self):
        engine, _ = _make_engine("Schema")
        with pytest.raises(ValueError):
            engine._t("malicious_table")


# ---------------------------------------------------------------------------
# Two engines in the same process — the real bug scenario
# ---------------------------------------------------------------------------

class TestTwoEngineIsolation:
    """Two engines with different schema prefixes must not interfere."""

    def test_two_engines_produce_independent_table_names(self):
        e_kg, _ = _make_engine("Graph_KG")
        e_rag, _ = _make_engine("RAG")

        assert e_kg._t("nodes") == "Graph_KG.nodes"
        assert e_rag._t("nodes") == "RAG.nodes"

    def test_second_engine_does_not_clobber_first(self):
        e_kg, _ = _make_engine("Graph_KG")
        e_rag, _ = _make_engine("RAG")

        # Interleaved calls — each must always return its own prefix
        for _ in range(5):
            assert e_kg._t("rdf_props") == "Graph_KG.rdf_props"
            assert e_rag._t("rdf_props") == "RAG.rdf_props"

    def test_module_global_does_not_affect_instance(self):
        engine, _ = _make_engine("Graph_KG")
        set_schema_prefix("HIJACKED")
        # Instance prefix must be unaffected
        assert engine._t("nodes") == "Graph_KG.nodes"
        set_schema_prefix("")

    def test_get_node_ids_by_property_uses_instance_prefix(self):
        """SQL emitted by a mixin method must reference the engine's own schema."""
        e_kg, cursor_kg = _make_engine("Graph_KG")
        cursor_kg.fetchall.return_value = []
        e_kg.get_node_ids_by_property("status")
        sql_kg = cursor_kg.execute.call_args[0][0]
        assert "Graph_KG.rdf_props" in sql_kg

        e_rag, cursor_rag = _make_engine("RAG")
        cursor_rag.fetchall.return_value = []
        e_rag.get_node_ids_by_property("status")
        sql_rag = cursor_rag.execute.call_args[0][0]
        assert "RAG.rdf_props" in sql_rag

        # Cross-check: neither bled into the other
        assert "RAG" not in sql_kg
        assert "Graph_KG" not in sql_rag

    def test_count_subjects_uses_instance_prefix(self):
        e_kg, cursor_kg = _make_engine("Graph_KG")
        cursor_kg.fetchone.return_value = (0,)
        e_kg.count_subjects_with_property("key")
        sql = cursor_kg.execute.call_args[0][0]
        assert "Graph_KG.rdf_props" in sql

        e_rag, cursor_rag = _make_engine("RAG")
        cursor_rag.fetchone.return_value = (0,)
        e_rag.count_subjects_with_property("key")
        sql = cursor_rag.execute.call_args[0][0]
        assert "RAG.rdf_props" in sql
