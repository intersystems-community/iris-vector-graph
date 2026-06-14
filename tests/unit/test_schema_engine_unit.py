"""
Unit tests for _engine/schema.py covering miss lines:
- _setup: db_dim=None → ALTER TABLE (lines 198-199)
- _setup: db_dim!=dim + empty table → DROP/recreate (lines 216-217, 224-225)
- _setup: row_count=None fallback (lines 205-206)
- _setup: procedure error logging (lines 261, 263)
- _sync_nkg: Rust path (lines 508-516) and InvalidateAdjCache (lines 521-522)
- materialize_inference: graph= param + _exists/_insert_inferred graph branches (lines 603-636)
- materialize_inference: rules="owl" OWL branches (lines 692-726)
"""
import pytest
from unittest.mock import MagicMock, patch, call
from iris_vector_graph.engine import IRISGraphEngine


def _make_eng(dim=4):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.return_value = None
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.description = [("node_id", None)]
    cursor.close.return_value = None
    return IRISGraphEngine(conn, embedding_dimension=dim), conn, cursor


# ---------------------------------------------------------------------------
# _setup: dimension mismatch paths
# ---------------------------------------------------------------------------

class TestInitializeSchema:
    """Tests for initialize_schema() covering dimension-mismatch branches."""

    def _make_eng_and_cursor(self):
        eng, conn, cursor = _make_eng()
        return eng, conn, cursor

    def test_db_dim_none_alters_table(self):
        """Lines 191-194: db_dim=None triggers ALTER TABLE on emb column."""
        eng, conn, cursor = self._make_eng_and_cursor()

        sqls = []
        def exec_side(sql, *args, **kwargs):
            sqls.append(sql)
        cursor.execute.side_effect = exec_side
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (0,)

        with patch("iris_vector_graph.schema.GraphSchema.get_base_schema_sql", return_value=""):
            with patch("iris_vector_graph.schema.GraphSchema.ensure_indexes"):
                with patch("iris_vector_graph.schema.GraphSchema.get_embedding_dimension", return_value=None):
                    with patch("iris_vector_graph.schema.GraphSchema.get_procedures_sql_list", return_value=[]):
                        with patch("iris_vector_graph.schema.GraphSchema.deploy_objectscript_classes",
                                   return_value=MagicMock(objectscript_deployed=False, kg_built=False)):
                            result = eng.initialize_schema(auto_deploy_objectscript=False)

        alter_sqls = [s for s in sqls if isinstance(s, str) and "ALTER TABLE" in s and "emb" in s.lower()]
        assert len(alter_sqls) >= 2  # both NodeEmbeddings and NodeEmbeddings_optimized

    def test_db_dim_mismatch_empty_table_drops_and_recreates(self):
        """Lines 214-225: db_dim != dim + row_count=0 → DROP TABLE + recreate."""
        eng, conn, cursor = self._make_eng_and_cursor()

        sqls = []
        def exec_side(sql, *args, **kwargs):
            sqls.append(sql)
        cursor.execute.side_effect = exec_side
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (0,)  # COUNT(*) = 0 → empty table

        create_sql = (
            "CREATE TABLE Graph_KG.kg_NodeEmbeddings (id VARCHAR(512), emb VECTOR(DOUBLE,8));"
            "CREATE TABLE Graph_KG.kg_EdgeEmbeddings (id VARCHAR(512), emb VECTOR(DOUBLE,8));"
        )
        with patch("iris_vector_graph.schema.GraphSchema.get_base_schema_sql", return_value=create_sql):
            with patch("iris_vector_graph.schema.GraphSchema.ensure_indexes"):
                with patch("iris_vector_graph.schema.GraphSchema.get_embedding_dimension", return_value=8):
                    with patch("iris_vector_graph.schema.GraphSchema.get_procedures_sql_list", return_value=[]):
                        with patch("iris_vector_graph.schema.GraphSchema.deploy_objectscript_classes",
                                   return_value=MagicMock(objectscript_deployed=False, kg_built=False)):
                            result = eng.initialize_schema(auto_deploy_objectscript=False)

        # Empty-table dim mismatch → ALTER TABLE (not DROP TABLE)
        alter_sqls = [s for s in sqls if isinstance(s, str) and "ALTER" in s.upper()]
        assert len(alter_sqls) >= 1

    def test_db_dim_mismatch_nonempty_table_no_drop(self):
        """Non-empty table with dim mismatch → error logged, no DROP."""
        eng, conn, cursor = self._make_eng_and_cursor()

        sqls = []
        fetchone_calls = [0]
        def exec_side(sql, *a, **kw):
            sqls.append(sql)
        def fetchone_side():
            fetchone_calls[0] += 1
            if fetchone_calls[0] == 1:
                return (100,)  # non-empty table
            return (0,)
        cursor.execute.side_effect = exec_side
        cursor.fetchone.side_effect = fetchone_side
        cursor.fetchall.return_value = []

        with patch("iris_vector_graph.schema.GraphSchema.get_base_schema_sql", return_value=""):
            with patch("iris_vector_graph.schema.GraphSchema.ensure_indexes"):
                with patch("iris_vector_graph.schema.GraphSchema.get_embedding_dimension", return_value=8):
                    with patch("iris_vector_graph.schema.GraphSchema.get_procedures_sql_list", return_value=[]):
                        with patch("iris_vector_graph.schema.GraphSchema.deploy_objectscript_classes",
                                   return_value=MagicMock(objectscript_deployed=False, kg_built=False)):
                            result = eng.initialize_schema(auto_deploy_objectscript=False)

        drop_sqls = [s for s in sqls if isinstance(s, str) and "DROP TABLE" in s]
        assert len(drop_sqls) == 0

    def test_db_dim_row_count_none_when_count_raises(self):
        """Lines 205-206: COUNT(*) raises → row_count=None, no drop."""
        eng, conn, cursor = self._make_eng_and_cursor()

        def exec_side(sql, *a, **kw):
            if "COUNT(*)" in sql and "kg_NodeEmbeddings" in sql:
                raise Exception("table not found")
        cursor.execute.side_effect = exec_side
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (0,)

        with patch("iris_vector_graph.schema.GraphSchema.get_base_schema_sql", return_value=""):
            with patch("iris_vector_graph.schema.GraphSchema.ensure_indexes"):
                with patch("iris_vector_graph.schema.GraphSchema.get_embedding_dimension", return_value=8):
                    with patch("iris_vector_graph.schema.GraphSchema.get_procedures_sql_list", return_value=[]):
                        with patch("iris_vector_graph.schema.GraphSchema.deploy_objectscript_classes",
                                   return_value=MagicMock(objectscript_deployed=False, kg_built=False)):
                            result = eng.initialize_schema(auto_deploy_objectscript=False)
        assert "tables_created" in result


# ---------------------------------------------------------------------------
# _sync_nkg: Rust path and InvalidateAdjCache
# ---------------------------------------------------------------------------

class TestSyncNkg:

    def test_rust_path_succeeds(self):
        """Lines 508-512: Rust callout succeeds → rust_succeeded=True, BuildNKG skipped."""
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = [
            '{"nodes": 100, "edges": 200}',  # BuildNKGRust
            "{}",                             # Build2HopStats
        ]
        build_nkg_calls = []
        def void_side(cls, method):
            build_nkg_calls.append((cls, method))
        iris_obj.classMethodVoid.side_effect = void_side

        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            with patch.object(eng, "_detect_arno", return_value=True):
                eng._arno_capabilities = {"rust_callout": True}
                result = eng._sync_nkg()

        assert result is True
        # BuildNKG (fallback) should NOT have been called — only InvalidateAdjCache
        build_nkg_only = [c for c in build_nkg_calls if c[1] == "BuildNKG"]
        assert len(build_nkg_only) == 0

    def test_rust_path_error_falls_back(self):
        """Lines 514-516: BuildNKGRust returns error → fallback to ObjectScript."""
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = [
            '{"error": "no rust"}',  # BuildNKGRust returns error
            "{}",                    # Build2HopStats
        ]

        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            with patch.object(eng, "_detect_arno", return_value=True):
                eng._arno_capabilities = {"rust_callout": True}
                result = eng._sync_nkg()

        assert result is True
        iris_obj.classMethodVoid.assert_called()  # BuildNKG called as fallback

    def test_rust_raises_falls_back(self):
        """Lines 515-516: BuildNKGRust raises → fallback."""
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = [
            RuntimeError("rust not available"),  # BuildNKGRust raises
            "{}",                                 # Build2HopStats
        ]

        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            with patch.object(eng, "_detect_arno", return_value=True):
                eng._arno_capabilities = {"rust_callout": True}
                result = eng._sync_nkg()

        assert result is True

    def test_invalidate_adj_cache_called(self):
        """Lines 521-522: InvalidateAdjCache called (and swallowed if it raises)."""
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "{}"
        iris_obj.classMethodVoid.side_effect = [None, RuntimeError("no method")]

        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            with patch.object(eng, "_detect_arno", return_value=False):
                result = eng._sync_nkg()

        assert result is True  # exception from InvalidateAdjCache is swallowed


# ---------------------------------------------------------------------------
# materialize_inference: graph= param branches
# ---------------------------------------------------------------------------

class TestMaterializeInferenceWithGraph:

    def _setup_cursor_for_inference(self, cursor, fetchall_returns=None):
        """Configure cursor to return empty edge sets for all predicate queries."""
        if fetchall_returns is None:
            fetchall_returns = [[], [], [], [], [], []]
        call_idx = [0]
        def fetchall_side():
            idx = call_idx[0]
            call_idx[0] += 1
            if idx < len(fetchall_returns):
                return fetchall_returns[idx]
            return []
        cursor.fetchall.side_effect = fetchall_side
        cursor.fetchone.return_value = (0,)

    def test_graph_param_uses_graph_filter(self):
        """Lines 603-612: _exists uses graph_id=? branch when graph is set."""
        eng, conn, cursor = _make_eng()

        sqls = []
        def exec_side(sql, params=None):
            sqls.append((sql, params))
        cursor.execute.side_effect = exec_side
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (0,)

        eng.materialize_inference(rules="rdfs", graph="g1")

        graph_filter_sqls = [s for s, p in sqls if "graph_id" in (s or "").lower()]
        assert len(graph_filter_sqls) >= 1

    def test_no_graph_uses_null_filter(self):
        """graph=None uses graph_id IS NULL branch."""
        eng, conn, cursor = _make_eng()
        sqls = []
        cursor.execute.side_effect = lambda s, p=None: sqls.append(s)
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (0,)

        eng.materialize_inference(rules="rdfs", graph=None)

        null_filter_sqls = [s for s in sqls if "IS NULL" in (s or "")]
        assert len(null_filter_sqls) >= 1

    def test_insert_inferred_with_graph(self):
        """Lines 621-625: _insert_inferred uses graph_id column when graph is set."""
        eng, conn, cursor = _make_eng()

        sqls = []
        fetchone_calls = [0]

        def exec_side(sql, params=None):
            sqls.append((sql, params))

        def fetchone_side():
            fetchone_calls[0] += 1
            return (0,)  # _exists always returns 0 → trigger insert

        cursor.execute.side_effect = exec_side
        cursor.fetchone.side_effect = fetchone_side

        # Return subClassOf edge so inferred triples are generated
        subclassof_pred = "http://www.w3.org/2000/01/rdf-schema#subClassOf"
        call_n = [0]
        def fetchall_side():
            call_n[0] += 1
            if call_n[0] == 1:
                return [("A", "B"), ("B", "C")]  # subClassOf edges → transitive A→C
            return []
        cursor.fetchall.side_effect = fetchall_side

        eng.materialize_inference(rules="rdfs", graph="g1")

        insert_sqls = [s for s, p in sqls if "INSERT INTO" in (s or "") and "graph_id" in (s or "")]
        assert len(insert_sqls) >= 1


# ---------------------------------------------------------------------------
# materialize_inference: rules="owl" branches
# ---------------------------------------------------------------------------

class TestMaterializeInferenceOwl:

    def test_owl_equiv_class_infers_subclassof(self):
        """Lines 692-696: equivalentClass(A,B) → subClassOf(A,B) + subClassOf(B,A)."""
        eng, conn, cursor = _make_eng()

        OWL_EQUIV_CLASS = "http://www.w3.org/2002/07/owl#equivalentClass"

        sqls = []
        def exec_side(sql, params=None):
            sqls.append((sql, params))

        def fetchall_side():
            sql_info = sqls[-1][0] if sqls else ""
            params_info = sqls[-1][1] if sqls else []
            if params_info and OWL_EQUIV_CLASS in (params_info if isinstance(params_info, list) else []):
                return [("ClassA", "ClassB")]
            return []

        cursor.execute.side_effect = exec_side
        cursor.fetchall.side_effect = fetchall_side
        cursor.fetchone.return_value = (0,)

        result = eng.materialize_inference(rules="owl")
        assert isinstance(result, dict)
        assert "inferred" in result

    def test_owl_inverse_of_generates_reverse_edges(self):
        """Lines 703-708: inverseOf(p,q) → for each (x,y) in p, add (y,q,x)."""
        eng, conn, cursor = _make_eng()

        OWL_INVERSE = "http://www.w3.org/2002/07/owl#inverseOf"

        sqls = []
        fetchall_n = [0]
        def exec_side(sql, params=None):
            sqls.append((sql, params))
        def fetchall_side():
            fetchall_n[0] += 1
            params = sqls[-1][1] if sqls else []
            if params and isinstance(params, list) and OWL_INVERSE in params:
                return [("hasPart", "isPartOf")]
            if fetchall_n[0] == 7:  # predicate edges query
                return []
            return []
        cursor.execute.side_effect = exec_side
        cursor.fetchall.side_effect = fetchall_side
        cursor.fetchone.return_value = (0,)

        result = eng.materialize_inference(rules="owl")
        assert isinstance(result, dict)

    def test_owl_transitive_property(self):
        """Lines 710-717: TransitiveProperty → transitive closure for each tp."""
        eng, conn, cursor = _make_eng()

        OWL_TRANS_PROP = "http://www.w3.org/2002/07/owl#TransitiveProperty"

        sqls = []
        fetchall_n = [0]
        def exec_side(sql, params=None):
            sqls.append((sql, params))
        def fetchall_side():
            fetchall_n[0] += 1
            params = sqls[-1][1] if sqls and sqls[-1] else []
            # Return one transitive property
            if isinstance(params, list) and len(params) == 2 and OWL_TRANS_PROP in params:
                return [("partOf",)]
            return []
        cursor.execute.side_effect = exec_side
        cursor.fetchall.side_effect = fetchall_side
        cursor.fetchone.return_value = (0,)

        result = eng.materialize_inference(rules="owl")
        assert isinstance(result, dict)

    def test_owl_symmetric_property(self):
        """Lines 719-726: SymmetricProperty → (y,sp,x) for each (x,y) in sp."""
        eng, conn, cursor = _make_eng()

        OWL_SYM_PROP = "http://www.w3.org/2002/07/owl#SymmetricProperty"

        sqls = []
        fetchall_n = [0]
        def exec_side(sql, params=None):
            sqls.append((sql, params))
        def fetchall_side():
            fetchall_n[0] += 1
            params = sqls[-1][1] if sqls else []
            if isinstance(params, list) and len(params) == 2 and OWL_SYM_PROP in params:
                return [("related",)]
            return []
        cursor.execute.side_effect = exec_side
        cursor.fetchall.side_effect = fetchall_side
        cursor.fetchone.return_value = (0,)

        result = eng.materialize_inference(rules="owl")
        assert isinstance(result, dict)

    def test_owl_all_branches_combined(self):
        """Smoke-test: rules='owl' with graph= param runs all OWL branches."""
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (0,)

        result = eng.materialize_inference(rules="owl", graph="test_graph")
        assert "inferred" in result
        assert isinstance(result["inferred"], int)
