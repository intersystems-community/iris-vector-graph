"""Spec 262: each engine's SQL names its own IVG Schema, whatever other engines the process has built.

Until this test the IVG Schema the Python stores and the Cypher translator wrote into their SQL was one process-wide
value, set by the last engine built (``set_schema_prefix`` from the constructor). Spec 2.5.1 made ``schema_prefix`` per
engine (``test_schema_prefix_isolation.py``), and hipporag2-pipeline runs two engines on different schemas in one
process: the first engine's queries went to the second engine's tables. Two engines, built one after the other, their
calls interleaved; each statement may name only its own engine's schema.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest

A, B = "IVGSCHA", "IVGSCHB"


def _engine(schema):
    from iris_vector_graph.engine import IRISGraphEngine

    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    with patch.object(IRISGraphEngine, "_build_index_registry", return_value={}), \
         patch.object(IRISGraphEngine, "_detect_stored_vector_dtype", return_value="DOUBLE"):
        engine = IRISGraphEngine(conn, schema_prefix=schema, native_cypher=False)
    cursor.execute.reset_mock()
    cursor.executemany.reset_mock()
    return engine, cursor


@pytest.fixture
def two():
    """Engine A built first, then B: under the old process-wide schema B's name was everybody's."""
    from iris_vector_graph.cypher.translator import get_schema_prefix

    before = get_schema_prefix()
    a = _engine(A)
    b = _engine(B)
    yield a, b
    assert get_schema_prefix() == before, "building an engine changed the process-wide default"


def _statements(cursor) -> list:
    out = [c.args[0] for c in cursor.execute.call_args_list if c.args]
    out += [c.args[0] for c in cursor.executemany.call_args_list if c.args]
    cursor.execute.reset_mock()
    cursor.executemany.reset_mock()
    return [str(s) for s in out]


_QUALIFIER = re.compile(r"(?<![\w.^%])([A-Za-z][A-Za-z0-9_]*)\.(?=[A-Za-z_])")


def _schemas_named(sql: str) -> set:
    """Every IVG Schema candidate a statement qualifies a name with (A, B, Graph_KG, SQLUser)."""
    return {q for q in _QUALIFIER.findall(sql) if q.upper() in {A, B, "GRAPH_KG", "SQLUSER"}}


def _only(statements, schema):
    """At least one statement names `schema`, and none names another (a catalog probe may name none)."""
    assert statements, "the call ran no SQL"
    seen = False
    for sql in statements:
        named = _schemas_named(sql)
        assert named <= {schema}, f"expected only {schema}, got {sorted(named)} in: {sql[:300]}"
        seen = seen or bool(named)
    assert seen, f"no statement named {schema}: {[s[:120] for s in statements]}"


_CYPHER = [
    "MATCH (a:P)-[:R]->(b) WHERE a.name =~ 'x.*' RETURN b.name, split(a.name, ',')",
    "MATCH (a:P {name: 'x'}) SET a.k = 1",
    "CREATE (:P {name: 'n'})-[:R]->(:Q)",
    "MATCH (a:P) DETACH DELETE a",
]


def _translate(engine, q):
    from iris_vector_graph.cypher.parser import parse_query
    from iris_vector_graph.cypher.translator import translate_to_sql

    sql = translate_to_sql(parse_query(q), {}, engine=engine)
    texts = [sql.sql] if isinstance(sql.sql, str) else list(sql.sql)
    texts += [s for s, _ in getattr(sql, "dml_statements", None) or []]
    return [t for t in texts if t]


class TestTranslator:
    @pytest.mark.parametrize("q", _CYPHER)
    def test_interleaved_translations_name_only_their_engines_schema(self, two, q):
        (ea, _), (eb, _) = two
        for _ in range(2):
            _only(_translate(ea, q), A)
            _only(_translate(eb, q), B)

    def test_the_edge_scan_names_the_engines_schema(self, two, monkeypatch):
        """`MatchEdges` is a procedure of the IVG Schema's package, not a `Graph_KG` constant (IVG_EDGESCAN=1)."""
        monkeypatch.setenv("IVG_EDGESCAN", "1")
        (ea, _), (eb, _) = two
        q = "MATCH (a {id: 'x'})-[:R]->(b) RETURN b.id"
        sa, sb = _translate(ea, q), _translate(eb, q)
        assert any("MatchEdges" in s for s in sa), sa
        _only(sa, A)
        _only(sb, B)

    def test_a_translation_without_an_engine_keeps_the_module_default(self, two):
        from iris_vector_graph.cypher.parser import parse_query
        from iris_vector_graph.cypher.translator import get_schema_prefix, set_schema_prefix, translate_to_sql

        previous = get_schema_prefix()
        set_schema_prefix("Graph_KG")
        try:
            sql = translate_to_sql(parse_query("MATCH (a:P) RETURN a.name"), {})
            _only([sql.sql], "Graph_KG")
        finally:
            set_schema_prefix(previous)


class TestStores:
    """The engine's mixins and its store qualify their SQL with the engine's schema."""

    CALLS = [
        ("get_labels", lambda e: e.get_labels()),
        ("is_ready", lambda e: e.is_ready()),
        ("list_namespaces", lambda e: e.list_namespaces()),
        ("list_table_mappings", lambda e: e.list_table_mappings()),
        ("store.get_nodes", lambda e: e._store.get_nodes(["n1"])),
        ("store.query_nodes", lambda e: e._store.query_nodes(label_filter="P")),
        ("store.delete_nodes", lambda e: e._store.delete_nodes(["n1"])),
        # the native-read privilege probe: checked on Graph_KG, an IVGALT user was kept off the native path
        ("native_privileges", lambda e: (setattr(e, "_native_privileges_cached", None), e._native_privileges())),
    ]

    @pytest.mark.parametrize("name,call", CALLS, ids=[c[0] for c in CALLS])
    def test_interleaved_calls_name_only_their_engines_schema(self, two, name, call):
        (ea, ca), (eb, cb) = two
        for _ in range(2):
            try:
                call(ea)
            except Exception:  # a mock row may not decode; the SQL was sent, which is what is checked
                pass
            _only(_statements(ca), A)
            try:
                call(eb)
            except Exception:
                pass
            _only(_statements(cb), B)

    def test_a_store_built_alone_defaults_to_graph_kg(self):
        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

        conn = MagicMock()
        conn.cursor.return_value.fetchall.return_value = []
        IRISGraphStore(conn).query_nodes(label_filter="P")
        _only(_statements(conn.cursor.return_value), "Graph_KG")


class TestArnoBridge:
    def test_arno_sql_functions_are_named_in_the_schema_asked_for(self):
        from iris_vector_graph.stores import arno_bridge

        arno_bridge.clear_probe_cache()
        try:
            for schema in (A, B, A):
                conn = MagicMock()
                cursor = conn.cursor.return_value
                cursor.fetchone.return_value = ('{"t": 1}',)
                # available (probed through another schema's wrappers): the call installs its own schema's first
                arno_bridge._probe_cache[arno_bridge._conn_key(conn)] = {"available": True, "lib_path": "/x.so",
                                                                         "ddl_schemas": {"Graph_KG"}}
                arno_bridge.arno_call(conn, "kg_scc_global", "^KG", 10, schema=schema)
                _only(_statements(cursor), schema)
        finally:
            arno_bridge.clear_probe_cache()

    def test_the_ddl_is_installed_once_per_schema(self):
        from iris_vector_graph.stores import arno_bridge

        arno_bridge.clear_probe_cache()
        try:
            conn = MagicMock()
            cursor = conn.cursor.return_value
            arno_bridge._ensure_zf_call_function(conn, schema=A)
            _only(_statements(cursor), A)
            arno_bridge._ensure_zf_call_function(conn, schema=A)
            assert _statements(cursor) == []
            arno_bridge._ensure_zf_call_function(conn, schema=B)
            _only(_statements(cursor), B)
        finally:
            arno_bridge.clear_probe_cache()


def test_no_module_holds_a_current_schema():
    """The process-wide value is gone: nothing to set, nothing to read."""
    from iris_vector_graph import schema_name

    assert not hasattr(schema_name, "set_current_schema")
    assert not hasattr(schema_name, "current_schema")
