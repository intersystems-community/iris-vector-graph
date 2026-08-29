"""
Tests targeting uncovered code paths in:
  - iris_vector_graph/cypher/translator.py  (CALL/YIELD validation, stage-bound
    relationship reuse, SET with variable/complex expressions)
  - iris_vector_graph/_engine/query.py      (GDS shim methods)
  - iris_vector_graph/engine.py             (IRISGraphEngine.__init__ alternate paths)
"""

import pytest
from unittest.mock import MagicMock, patch

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def tr(q, params=None, procedures=None):
    """Translate a Cypher query to SQL using the pure-Python translator."""
    ast_tree = parse_query(q)
    result = translate_to_sql(ast_tree, params or {}, procedures=procedures)
    return result.sql, result.parameters


# ============================================================================
# Target 1: Lines 829-848 — CALL procedure YIELD validation
# (Only fires for test.* procedures via _translate_test_procedure)
# ============================================================================

# Minimal procedure definition reused across tests
_LABELS_PROC = {
    "test.labels": {
        "args": [],
        "outputs": [{"name": "label", "type": "STRING"}],
        "rows": [{"label": "Person"}, {"label": "Movie"}],
    }
}


class TestCallYieldValidation:
    """Lines 830-848: VariableAlreadyBound checks inside _translate_test_procedure."""

    def test_yield_duplicate_alias_raises_syntax_error(self):
        """YIELD with the same alias twice must raise SyntaxError with VariableAlreadyBound."""
        with pytest.raises(SyntaxError, match="VariableAlreadyBound"):
            tr(
                "CALL test.labels() YIELD label AS x, label AS x RETURN x",
                procedures=_LABELS_PROC,
            )

    def test_yield_duplicate_alias_message_includes_alias(self):
        """Error message must include the duplicated alias name."""
        with pytest.raises(SyntaxError, match="'x'"):
            tr(
                "CALL test.labels() YIELD label AS x, label AS x RETURN x",
                procedures=_LABELS_PROC,
            )

    def test_yield_shadows_outer_variable_raises_syntax_error(self):
        """YIELD alias that matches a WITH-bound variable must raise SyntaxError."""
        with pytest.raises(SyntaxError, match="VariableAlreadyBound"):
            tr(
                "WITH 1 AS n CALL test.labels() YIELD label AS n RETURN n",
                procedures=_LABELS_PROC,
            )

    def test_yield_shadows_outer_variable_message_includes_alias(self):
        """Error message includes the offending alias when shadowing an outer variable."""
        with pytest.raises(SyntaxError, match="'n'"):
            tr(
                "WITH 1 AS n CALL test.labels() YIELD label AS n RETURN n",
                procedures=_LABELS_PROC,
            )

    def test_yield_unique_aliases_succeeds(self):
        """Non-duplicate YIELD aliases should not raise."""
        sql, _ = tr(
            "CALL test.labels() YIELD label AS lbl RETURN lbl",
            procedures=_LABELS_PROC,
        )
        assert sql is not None

    def test_yield_no_alias_succeeds(self):
        """YIELD with bare column names (no alias) should not raise."""
        sql, _ = tr(
            "CALL test.labels() YIELD label RETURN label",
            procedures=_LABELS_PROC,
        )
        assert sql is not None

    def test_tck_proc_cte_registered_after_successful_yield(self):
        """After successful YIELD the translator registers _tck_proc_cte on context."""
        from iris_vector_graph.cypher.translator import (
            translate_procedure_call,
            TranslationContext,
        )

        ast_tree = parse_query("CALL test.labels() YIELD label RETURN label")
        ctx = TranslationContext()
        ctx.input_params = {}
        ctx._tck_procedures = _LABELS_PROC

        translate_procedure_call(ast_tree.procedure_call, ctx)

        assert hasattr(ctx, "_tck_proc_cte")
        assert ctx._tck_proc_cte is not None


# ============================================================================
# Target 2: Lines 6331-6397 — Stage-bound relationship reuse
# ============================================================================

class TestStageBoundRelationshipReuse:
    """Lines 6331-6397: edge variable already promoted to a Stage CTE is reused
    directly rather than re-joining rdf_edges."""

    def test_with_r_match_reuses_stage_columns(self):
        """MATCH..WITH r..MATCH reuses __edge_r_s/__edge_r_o columns from Stage CTE."""
        sql, _ = tr("MATCH (a)-[r]->(b) WITH r MATCH (x)-[r]->(y) RETURN type(r)")
        assert "Stage1" in sql
        assert "__edge_r_s" in sql
        assert "__edge_r_o" in sql

    def test_with_r_match_no_second_rdf_edges_join(self):
        """Second MATCH should NOT add another bare rdf_edges join for the same variable."""
        sql, _ = tr("MATCH (a)-[r]->(b) WITH r MATCH (x)-[r]->(y) RETURN type(r)")
        # There should be exactly one CTE definition for Stage1
        assert sql.count("AS (") == 1

    def test_with_r_return_property_uses_stage(self):
        """Accessing r.weight after WITH r should use Stage CTE columns."""
        sql, _ = tr("MATCH (a)-[r:KNOWS]->(b) WITH r, a, b RETURN r.weight")
        assert "Stage1" in sql
        assert "__edge_r_s" in sql or "r_weight" in sql  # r.weight resolves via stage

    def test_stage_bound_type_filter_added_to_where(self):
        """When second MATCH constrains [r:KNOWS], a WHERE filter on __edge_r_p is added."""
        sql, params = tr(
            "MATCH (a)-[r]->(b) WITH r MATCH (x)-[r:KNOWS]->(y) RETURN type(r)"
        )
        assert "__edge_r_p" in sql
        assert "?" in sql
        # The param list should contain 'KNOWS'
        all_params = params[0] if isinstance(params, list) and params else params
        assert "KNOWS" in all_params

    def test_with_r_sql_is_string(self):
        """Translator returns a SQL string (not a list) for a pure-read stage query."""
        sql, _ = tr("MATCH (a)-[r]->(b) WITH r MATCH (x)-[r]->(y) RETURN type(r)")
        assert isinstance(sql, str)

    def test_with_typed_r_stage_includes_type_param(self):
        """First MATCH with [r:KNOWS] stores the type as a parameter in Stage1 CTE."""
        sql, params = tr("MATCH (a)-[r:KNOWS]->(b) WITH r, a, b RETURN r.weight")
        # Stage CTE should filter e2.p = ?  (one param = 'KNOWS')
        all_params = params[0] if isinstance(params, list) and params else params
        assert "KNOWS" in all_params


# ============================================================================
# Target 3: Lines 4660-4769 — SET/update with variable/complex expressions
# ============================================================================

class TestSetUpdateExpressions:
    """Lines 4660-4769: SET clause handling for $params, arithmetic, and list literals."""

    def test_set_with_string_param_produces_transactional_sql(self):
        """SET n.name = $name should produce a list of UPDATE+INSERT+SELECT statements."""
        sql, params = tr(
            "MATCH (n) WHERE n.id = $id SET n.name = $name RETURN n",
            params={"id": "1", "name": "Alice"},
        )
        assert isinstance(sql, list)
        assert len(sql) == 3  # UPDATE, INSERT, SELECT

    def test_set_with_string_param_update_uses_placeholder(self):
        """The UPDATE statement binds 'Alice' via positional parameter."""
        sql, params = tr(
            "MATCH (n) WHERE n.id = $id SET n.name = $name RETURN n",
            params={"id": "1", "name": "Alice"},
        )
        update_sql = sql[0]
        assert "UPDATE" in update_sql.upper()
        assert "?" in update_sql
        assert "Alice" in params[0]

    def test_set_arithmetic_update_uses_numeric_cast(self):
        """SET n.score = n.score + 1 translates to CAST(val AS NUMERIC) + 1 in UPDATE."""
        sql, params = tr(
            "MATCH (n) WHERE n.id = $id SET n.score = n.score + 1 RETURN n",
            params={"id": "1"},
        )
        assert isinstance(sql, list)
        update_sql = sql[0]
        assert "NUMERIC" in update_sql.upper()
        assert "+ 1" in update_sql

    def test_set_arithmetic_insert_uses_correlated_subquery(self):
        """INSERT branch of n.score + 1 uses a correlated subquery to fetch existing val."""
        sql, params = tr(
            "MATCH (n) WHERE n.id = $id SET n.score = n.score + 1 RETURN n",
            params={"id": "1"},
        )
        insert_sql = sql[1]
        assert "SELECT _iv" in insert_sql or "_upd" in insert_sql

    def test_set_list_literal_stores_json(self):
        """SET n.tags = ['a','b'] should store JSON array representation."""
        sql, params = tr("MATCH (n) SET n.tags = ['a','b'] RETURN n")
        assert isinstance(sql, list)
        # UPDATE should pass a value containing the JSON array
        update_sql = sql[0]
        assert "UPDATE" in update_sql.upper()
        update_params = params[0]
        assert any('["a"' in str(v) or "['a'" in str(v) for v in update_params)

    def test_set_param_value_passed_correctly(self):
        """Parameters are passed in the correct order: value, filter keys, column name."""
        sql, params = tr(
            "MATCH (n) WHERE n.id = $id SET n.name = $name RETURN n",
            params={"id": "1", "name": "Alice"},
        )
        # UPDATE params: [new_val, ...filter..., col_name]
        update_params = params[0]
        assert update_params[0] == "Alice"
        assert "name" in update_params


# ============================================================================
# Target 4: query.py lines ~53-98 — GDS shim (_handle_gds_shim)
# ============================================================================

from iris_vector_graph._engine.query import _handle_gds_shim, GDS_SHIM_MAP


class _FakeProc:
    """Minimal procedure-call object for shim tests."""

    def __init__(self, name, arguments=None, yield_items=None):
        self.procedure_name = name
        self.arguments = arguments or []
        self.yield_items = yield_items or []


class TestGdsShim:
    """Lines 68-98: _handle_gds_shim maps gds.* calls to ivg.* equivalents."""

    def test_non_gds_proc_returns_none(self):
        """Non-gds.* procedures must return None (caller continues normal dispatch)."""
        assert _handle_gds_shim(_FakeProc("ivg.ppr")) is None

    def test_non_gds_proc_ivg_vector_returns_none(self):
        assert _handle_gds_shim(_FakeProc("ivg.vector.search")) is None

    def test_unknown_gds_proc_returns_error_result(self):
        """An unmapped gds.* procedure returns an IVGResult with an error."""
        result = _handle_gds_shim(_FakeProc("gds.unknown.algo"))
        assert result is not None
        assert result.error is not None
        assert "not shimmed" in result.error

    def test_unknown_gds_proc_suggestions_in_error(self):
        """Error message lists available ivg equivalents."""
        result = _handle_gds_shim(_FakeProc("gds.unknown.algo"))
        for ivg_name in set(GDS_SHIM_MAP.values()):
            assert ivg_name in result.error

    def test_pagerank_shimmed_to_ivg_ppr(self):
        """gds.pagerank.stream → ivg.ppr."""
        result = _handle_gds_shim(_FakeProc("gds.pagerank.stream"))
        assert isinstance(result, tuple)
        assert result[0].procedure_name == "ivg.ppr"

    def test_dijkstra_stream_shimmed(self):
        """gds.shortestpath.dijkstra.stream → ivg.shortestPath.weighted."""
        result = _handle_gds_shim(_FakeProc("gds.shortestpath.dijkstra.stream"))
        assert isinstance(result, tuple)
        assert result[0].procedure_name == "ivg.shortestPath.weighted"

    def test_dijkstra_no_stream_shimmed(self):
        """gds.shortestpath.dijkstra (without .stream) is also shimmed."""
        result = _handle_gds_shim(_FakeProc("gds.shortestpath.dijkstra"))
        assert isinstance(result, tuple)
        assert result[0].procedure_name == "ivg.shortestPath.weighted"

    def test_betweenness_shimmed(self):
        """gds.betweenness.stream → ivg.betweenness."""
        result = _handle_gds_shim(_FakeProc("gds.betweenness.stream"))
        assert isinstance(result, tuple)
        assert result[0].procedure_name == "ivg.betweenness"

    def test_louvain_shimmed_to_leiden(self):
        """gds.louvain.stream → ivg.leiden."""
        result = _handle_gds_shim(_FakeProc("gds.louvain.stream"))
        assert isinstance(result, tuple)
        assert result[0].procedure_name == "ivg.leiden"

    def test_node_similarity_shimmed(self):
        """gds.nodesimilarity.stream → ivg.vector.search."""
        result = _handle_gds_shim(_FakeProc("gds.nodesimilarity.stream"))
        assert isinstance(result, tuple)
        assert result[0].procedure_name == "ivg.vector.search"

    def test_shimmed_proc_preserves_arguments(self):
        """Shimmed proc object carries the original arguments unchanged."""
        fake = _FakeProc("gds.pagerank.stream", arguments=["arg1", "arg2"])
        result = _handle_gds_shim(fake)
        assert result[0].arguments == ["arg1", "arg2"]

    def test_shimmed_proc_preserves_yield_items(self):
        """Shimmed proc object carries the original yield_items unchanged."""
        fake = _FakeProc("gds.pagerank.stream", yield_items=[("score", "score")])
        result = _handle_gds_shim(fake)
        assert result[0].yield_items == [("score", "score")]

    def test_gds_case_insensitive_detection(self):
        """gds.* detection uses lowercase comparison — uppercase input is still matched."""
        result = _handle_gds_shim(_FakeProc("GDS.PAGERANK.STREAM"))
        # The name starts with gds. after lowercase — should shim
        assert isinstance(result, tuple)
        assert result[0].procedure_name == "ivg.ppr"

    def test_unknown_gds_proc_rows_empty(self):
        """Error result for unknown gds procedure has empty rows."""
        result = _handle_gds_shim(_FakeProc("gds.missing"))
        assert result.rows == [] or result.rows == []


# ============================================================================
# Target 5: engine.py lines 65-68, 94-103, 200-219 — IRISGraphEngine.__init__
# ============================================================================

from iris_vector_graph.engine import IRISGraphEngine


def _make_conn():
    """Standard mock DB-API connection."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (None,)
    return conn, cursor


def _make_store():
    mock_store = MagicMock()
    mock_store.capabilities.return_value = {"native_sql": True}
    return mock_store


def _engine_ctx(conn, store=None, **kwargs):
    """Context manager that patches away expensive init steps."""
    import contextlib
    mock_store = store or _make_store()

    @contextlib.contextmanager
    def _ctx():
        with patch.object(IRISGraphEngine, "_build_index_registry", return_value={}), \
             patch.object(IRISGraphEngine, "_detect_stored_vector_dtype", return_value="DOUBLE"), \
             patch("iris_vector_graph.stores.iris_sql_store.IRISGraphStore", return_value=mock_store):
            yield IRISGraphEngine(conn, **kwargs)

    return _ctx()


class TestIRISGraphEngineInit:
    """Lines 114-163: alternate constructor paths."""

    def test_default_schema_prefix(self):
        """Default schema_prefix is 'Graph_KG'."""
        conn, _ = _make_conn()
        with _engine_ctx(conn) as eng:
            assert eng._schema_prefix == "Graph_KG"

    def test_custom_schema_prefix(self):
        """schema_prefix kwarg is stored on the instance."""
        conn, _ = _make_conn()
        with _engine_ctx(conn, schema_prefix="MySchema") as eng:
            assert eng._schema_prefix == "MySchema"

    def test_embedding_dimension_kwarg(self):
        """embedding_dimension kwarg is stored on the instance."""
        conn, _ = _make_conn()
        with _engine_ctx(conn, embedding_dimension=256) as eng:
            assert eng.embedding_dimension == 256

    def test_embedding_dimension_default_none(self):
        """embedding_dimension defaults to None when not provided."""
        conn, _ = _make_conn()
        with _engine_ctx(conn) as eng:
            assert eng.embedding_dimension is None

    def test_arno_available_starts_none(self):
        """_arno_available is initialised to None (not yet probed)."""
        conn, _ = _make_conn()
        with _engine_ctx(conn) as eng:
            assert eng._arno_available is None

    def test_native_vec_starts_none(self):
        """_native_vec_available is initialised to None (not yet probed)."""
        conn, _ = _make_conn()
        with _engine_ctx(conn) as eng:
            assert eng._native_vec_available is None

    def test_probe_native_vec_returns_true_when_no_exception(self):
        """_probe_native_vec returns True when cursor.execute does not raise."""
        conn, cursor = _make_conn()
        cursor.execute.return_value = None  # No exception
        with _engine_ctx(conn) as eng:
            result = eng._probe_native_vec()
        assert result is True
        assert eng._native_vec_available is True

    def test_probe_native_vec_returns_false_on_unknown_function(self):
        """_probe_native_vec returns False when cursor raises 'unknown function'."""
        conn, cursor = _make_conn()
        cursor.execute.side_effect = Exception("unknown function VECTOR_COSINE")
        with _engine_ctx(conn) as eng:
            result = eng._probe_native_vec()
        assert result is False
        assert eng._native_vec_available is False

    def test_probe_native_vec_cached(self):
        """Subsequent calls to _probe_native_vec return the cached value."""
        conn, cursor = _make_conn()
        cursor.execute.return_value = None
        with _engine_ctx(conn) as eng:
            first = eng._probe_native_vec()
            cursor.execute.side_effect = Exception("should not be called again")
            second = eng._probe_native_vec()
        assert first == second

    def test_embedded_connection_detected_by_prepare(self):
        """A connection with .prepare but no .cursor triggers EmbeddedConnection wrapping."""
        prepare_only_conn = MagicMock(spec=["prepare"])
        import iris_vector_graph.embedded as emb_mod

        mock_ec = MagicMock()
        mock_ec.cursor.return_value = MagicMock()
        mock_ec.cursor.return_value.fetchall.return_value = []
        mock_ec.cursor.return_value.fetchone.return_value = (None,)
        mock_ec_cls = MagicMock(return_value=mock_ec)

        with patch.object(IRISGraphEngine, "_build_index_registry", return_value={}), \
             patch.object(IRISGraphEngine, "_detect_stored_vector_dtype", return_value="DOUBLE"), \
             patch.object(emb_mod, "EmbeddedConnection", mock_ec_cls), \
             patch("iris_vector_graph.stores.iris_sql_store.IRISGraphStore", return_value=_make_store()):
            eng = IRISGraphEngine(prepare_only_conn)

        assert eng.conn is not prepare_only_conn
        mock_ec_cls.assert_called_once()

    def test_store_capabilities_stored(self):
        """_store_capabilities reflects what store.capabilities() returns."""
        conn, _ = _make_conn()
        mock_store = _make_store()
        mock_store.capabilities.return_value = {"native_sql": False, "custom": True}
        with patch.object(IRISGraphEngine, "_build_index_registry", return_value={}), \
             patch.object(IRISGraphEngine, "_detect_stored_vector_dtype", return_value="DOUBLE"), \
             patch("iris_vector_graph.stores.iris_sql_store.IRISGraphStore", return_value=mock_store):
            eng = IRISGraphEngine(conn)
        assert eng._store_capabilities == {"native_sql": False, "custom": True}
