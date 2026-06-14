"""
Unit tests for _engine/vector.py covering:
- _validate_k
- _kg_KNN_VEC_client_side (numpy cosine similarity)
- kg_TXT (stored procedure call)
- kg_NEIGHBORHOOD_EXPANSION (JSON_TABLE query)
- edge_vector_search (VECTOR_COSINE on edges)
- validate_vector_table (INFORMATION_SCHEMA query)
- vector_search (VECTOR_COSINE on custom table)
- multi_vector_search (fusion across sources)
- kg_KNN_VEC (server-side; falls back to python-optimized)
- _kg_KNN_VEC_python_optimized (SQL fallback)

No IRIS connection needed — mocks conn and cursor.
"""
import json
import pytest
from unittest.mock import MagicMock, patch, call
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult


def _make_eng(dim=4):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.return_value = None
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    cursor.description = []
    return IRISGraphEngine(conn, embedding_dimension=dim), conn, cursor


# ---------------------------------------------------------------------------
# _validate_k
# ---------------------------------------------------------------------------

class TestValidateK:

    def test_normal_value(self):
        eng, _, _ = _make_eng()
        assert eng._validate_k(10) == 10

    def test_zero_defaults_to_50(self):
        # int(0 or 50) == 50 because 0 is falsy
        eng, _, _ = _make_eng()
        assert eng._validate_k(0) == 50

    def test_negative_clamps_to_one(self):
        eng, _, _ = _make_eng()
        assert eng._validate_k(-5) == 1

    def test_over_max_clamps_to_1000(self):
        eng, _, _ = _make_eng()
        assert eng._validate_k(9999) == 1000

    def test_none_defaults_to_50(self):
        eng, _, _ = _make_eng()
        assert eng._validate_k(None) == 50

    def test_non_numeric_string_defaults_to_50(self):
        eng, _, _ = _make_eng()
        assert eng._validate_k("abc") == 50

    def test_numeric_string(self):
        eng, _, _ = _make_eng()
        assert eng._validate_k("25") == 25


# ---------------------------------------------------------------------------
# _kg_KNN_VEC_client_side  (numpy cosine similarity fallback)
# ---------------------------------------------------------------------------

class TestKgKNNVECClientSide:

    def _make_cursor_with_batches(self, batches):
        """Return a fresh cursor mock whose fetchmany returns batches in sequence."""
        cursor = MagicMock()
        batch_iter = iter(batches + [[]])  # last [] terminates the loop
        cursor.fetchmany.side_effect = lambda size=1000: next(batch_iter)
        cursor.execute.return_value = None
        cursor.close.return_value = None
        return cursor

    def test_client_side_no_label_filter(self):
        eng, conn, _ = _make_eng()
        cursor = self._make_cursor_with_batches([
            [("n1", "1.0,0.0,0.0,0.0"), ("n2", "0.0,1.0,0.0,0.0")],
        ])
        conn.cursor.return_value = cursor
        query_vector = json.dumps([1.0, 0.0, 0.0, 0.0])
        result = eng._kg_KNN_VEC_client_side(query_vector, k=2)
        assert len(result) == 2
        # n1 should rank first (cosine = 1.0)
        assert result[0][0] == "n1"
        assert abs(result[0][1] - 1.0) < 1e-6

    def test_client_side_with_label_filter(self):
        eng, conn, _ = _make_eng()
        cursor = self._make_cursor_with_batches([
            [("n1", "1.0,0.0,0.0,0.0")],
        ])
        conn.cursor.return_value = cursor
        query_vector = json.dumps([1.0, 0.0, 0.0, 0.0])
        result = eng._kg_KNN_VEC_client_side(query_vector, k=5, label_filter="Disease")
        assert len(result) == 1
        # Verify label_filter was passed in the SQL query
        call_args = cursor.execute.call_args
        assert "Disease" in str(call_args) or "?" in str(call_args[0][0])

    def test_client_side_skips_bad_emb_rows(self):
        eng, conn, _ = _make_eng()
        cursor = self._make_cursor_with_batches([
            [("n1", "1.0,0.0,0.0,0.0"), ("bad", "not_a_vector"), ("n3", "0.5,0.5,0.0,0.0")],
        ])
        conn.cursor.return_value = cursor
        query_vector = json.dumps([1.0, 0.0, 0.0, 0.0])
        result = eng._kg_KNN_VEC_client_side(query_vector, k=10)
        ids = [r[0] for r in result]
        assert "bad" not in ids
        assert "n1" in ids

    def test_client_side_respects_k_limit(self):
        eng, conn, _ = _make_eng()
        batch = [(f"n{i}", f"{float(i/10)},0.0,0.0,0.0") for i in range(20)]
        cursor = self._make_cursor_with_batches([batch])
        conn.cursor.return_value = cursor
        query_vector = json.dumps([1.0, 0.0, 0.0, 0.0])
        result = eng._kg_KNN_VEC_client_side(query_vector, k=3)
        assert len(result) == 3

    def test_client_side_empty_table(self):
        eng, conn, _ = _make_eng()
        cursor = self._make_cursor_with_batches([[]])
        conn.cursor.return_value = cursor
        query_vector = json.dumps([1.0, 0.0, 0.0, 0.0])
        result = eng._kg_KNN_VEC_client_side(query_vector, k=10)
        assert result == []

    def test_client_side_raises_on_numpy_failure(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("DB error")
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        query_vector = json.dumps([1.0, 0.0, 0.0, 0.0])
        with pytest.raises(Exception):
            eng._kg_KNN_VEC_client_side(query_vector, k=10)


# ---------------------------------------------------------------------------
# kg_TXT  (stored procedure call)
# ---------------------------------------------------------------------------

class TestKgTXT:

    def test_kg_txt_returns_results(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.return_value = None
        cursor.fetchall.return_value = [("entity_a", 850), ("entity_b", 700)]
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        result = eng.kg_TXT("cancer treatment", k=5)
        assert len(result) == 2
        assert result[0][0] == "entity_a"
        assert result[0][1] == 850.0

    def test_kg_txt_empty_result(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.return_value = None
        cursor.fetchall.return_value = []
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        result = eng.kg_TXT("nothing here", k=5)
        assert result == []

    def test_kg_txt_propagates_exception(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("procedure not found")
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        with pytest.raises(RuntimeError, match="procedure not found"):
            eng.kg_TXT("query", k=5)

    def test_kg_txt_passes_min_confidence(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.return_value = None
        cursor.fetchall.return_value = []
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        eng.kg_TXT("query", k=10, min_confidence=500)
        call_args = cursor.execute.call_args[0]
        assert 500 in call_args[1]


# ---------------------------------------------------------------------------
# kg_NEIGHBORHOOD_EXPANSION
# ---------------------------------------------------------------------------

class TestKgNeighborhoodExpansion:

    def test_empty_entity_list_returns_empty(self):
        eng, conn, _ = _make_eng()
        result = eng.kg_NEIGHBORHOOD_EXPANSION([])
        assert result == []

    def test_returns_rows_as_dicts(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.return_value = None
        cursor.fetchall.return_value = [
            ("seed_a", "TREATS", "target_b", 900),
            ("seed_a", "CAUSES", "target_c", 750),
        ]
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        result = eng.kg_NEIGHBORHOOD_EXPANSION(["seed_a"], expansion_depth=1)
        assert len(result) == 2
        assert result[0]["source"] == "seed_a"
        assert result[0]["predicate"] == "TREATS"
        assert result[0]["target"] == "target_b"
        assert result[0]["confidence"] == 900

    def test_confidence_threshold_passed_to_query(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.return_value = None
        cursor.fetchall.return_value = []
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        eng.kg_NEIGHBORHOOD_EXPANSION(["a", "b"], confidence_threshold=800)
        call_args = cursor.execute.call_args[0]
        assert 800 in call_args[1]

    def test_multiple_entities_included_in_query(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.return_value = None
        cursor.fetchall.return_value = []
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        eng.kg_NEIGHBORHOOD_EXPANSION(["a", "b", "c"])
        sql = cursor.execute.call_args[0][0]
        # 3 entities → 3 placeholders
        assert sql.count("?") >= 3

    def test_propagates_exception(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("SQL fail")
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        with pytest.raises(RuntimeError):
            eng.kg_NEIGHBORHOOD_EXPANSION(["seed"])


# ---------------------------------------------------------------------------
# edge_vector_search
# ---------------------------------------------------------------------------

class TestEdgeVectorSearch:

    def test_returns_rows_as_dicts(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.return_value = None
        cursor.fetchall.return_value = [
            ("s1", "TREATS", "t1", 0.95),
            ("s2", "CAUSES", "t2", 0.80),
        ]
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        result = eng.edge_vector_search([1.0, 0.0, 0.0, 0.0], top_k=5)
        assert len(result) == 2
        assert result[0]["s"] == "s1"
        assert result[0]["p"] == "TREATS"
        assert abs(result[0]["score"] - 0.95) < 1e-6

    def test_empty_table_returns_empty_list(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.return_value = None
        cursor.fetchall.return_value = []
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        result = eng.edge_vector_search([1.0, 0.0, 0.0, 0.0])
        assert result == []

    def test_missing_table_error_returns_empty(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("table not found")
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        result = eng.edge_vector_search([1.0, 0.0, 0.0, 0.0])
        assert result == []

    def test_other_exception_propagates(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("connection lost")
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        with pytest.raises(RuntimeError, match="connection lost"):
            eng.edge_vector_search([1.0, 0.0, 0.0, 0.0])

    def test_string_embedding_input(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.return_value = None
        cursor.fetchall.return_value = []
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        result = eng.edge_vector_search("1.0,0.0,0.0,0.0")
        assert result == []

    def test_score_threshold_added_to_sql(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.return_value = None
        cursor.fetchall.return_value = []
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        eng.edge_vector_search([1.0, 0.0, 0.0, 0.0], score_threshold=0.5)
        sql = cursor.execute.call_args[0][0]
        assert "0.5" in sql


# ---------------------------------------------------------------------------
# validate_vector_table
# ---------------------------------------------------------------------------

class TestValidateVectorTable:

    def test_valid_table_returns_dict(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        # fetchone calls: column check (1), row count (5), sample row ("[1.0,0.0]")
        cursor.fetchone.side_effect = [(1,), (5,), ("[1.0, 0.0]",)]
        cursor.execute.return_value = None
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        result = eng.validate_vector_table("MySchema.MyTable", "emb")
        assert result["table"] == "MySchema.MyTable"
        assert result["vector_col"] == "emb"
        assert result["row_count"] == 5

    def test_missing_column_raises_value_error(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.fetchone.return_value = (0,)
        cursor.execute.return_value = None
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        with pytest.raises(ValueError, match="Column 'emb' not found"):
            eng.validate_vector_table("MySchema.MyTable", "emb")

    def test_no_sample_row_dimension_is_none(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(1,), (3,), None]
        cursor.execute.return_value = None
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        result = eng.validate_vector_table("Schema.Table", "vec")
        assert result["dimension"] is None

    def test_table_without_schema(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(1,), (0,), None]
        cursor.execute.return_value = None
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        result = eng.validate_vector_table("MyTable", "emb")
        assert result["table"] == "MyTable"


# ---------------------------------------------------------------------------
# vector_search
# ---------------------------------------------------------------------------

class TestVectorSearch:

    def test_basic_search_returns_rows(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.return_value = None
        cursor.description = [("id",), ("score",)]
        cursor.fetchall.return_value = [("node_1", 0.95), ("node_2", 0.80)]
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        result = eng.vector_search(
            table="MySchema.MyTable",
            vector_col="emb",
            query_embedding=[1.0, 0.0, 0.0, 0.0],
            top_k=5,
        )
        assert len(result) == 2
        assert result[0]["id"] == "node_1"
        assert result[0]["score"] == 0.95

    def test_string_embedding_input(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.return_value = None
        cursor.description = [("id",), ("score",)]
        cursor.fetchall.return_value = []
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        result = eng.vector_search(
            "Schema.Tbl", "emb", "1.0,0.0,0.0,0.0"
        )
        assert result == []

    def test_sql_error_raises_value_error(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("wrong dimension")
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        with pytest.raises(ValueError, match="vector_search failed"):
            eng.vector_search("Schema.Tbl", "emb", [1.0, 0.0])

    def test_score_threshold_applied(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.return_value = None
        cursor.description = [("id",), ("score",)]
        cursor.fetchall.return_value = []
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        eng.vector_search("S.T", "emb", [1.0, 0.0], score_threshold=0.7)
        sql = cursor.execute.call_args[0][0]
        assert "0.7" in sql

    def test_return_cols_included_in_select(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.return_value = None
        cursor.description = [("id",), ("score",), ("label",)]
        cursor.fetchall.return_value = [("n1", 0.9, "Disease")]
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        result = eng.vector_search("S.T", "emb", [1.0, 0.0, 0.0, 0.0],
                                   return_cols=["label"])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# multi_vector_search
# ---------------------------------------------------------------------------

class TestMultiVectorSearch:

    def test_empty_sources_returns_empty(self):
        eng, _, _ = _make_eng()
        result = eng.multi_vector_search([], [1.0, 0.0, 0.0, 0.0])
        assert result == []

    def test_single_source_returns_top_k(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.return_value = None
        cursor.description = [("id",), ("score",)]
        cursor.fetchall.return_value = [("n1", 0.9), ("n2", 0.8)]
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        sources = [{"table": "S.T", "col": "emb", "id_col": "id"}]
        result = eng.multi_vector_search(sources, [1.0, 0.0, 0.0, 0.0], top_k=2)
        assert len(result) <= 2

    def test_skips_failed_source(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("bad table")
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        sources = [{"table": "Bad.Table", "col": "emb", "id_col": "id"}]
        # Should not raise — skips failed source
        result = eng.multi_vector_search(sources, [1.0, 0.0, 0.0, 0.0])
        assert result == []

    def test_string_query_embedding(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.return_value = None
        cursor.description = [("id",), ("score",)]
        cursor.fetchall.return_value = []
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        sources = [{"table": "S.T", "col": "emb"}]
        result = eng.multi_vector_search(sources, "1.0,0.0,0.0,0.0")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# kg_KNN_VEC  (server-side with fallback)
# ---------------------------------------------------------------------------

class TestKgKNNVEC:

    def test_basic_vector_search(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.return_value = None
        cursor.fetchall.return_value = [("n1", 0.9), ("n2", 0.7)]
        cursor.fetchone.return_value = None
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        result = eng.kg_KNN_VEC("[1.0, 0.0, 0.0, 0.0]", k=5)
        assert len(result) == 2
        assert result[0] == ("n1", 0.9)

    def test_with_label_filter(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.return_value = None
        cursor.fetchall.return_value = [("disease_node", 0.95)]
        cursor.fetchone.return_value = None
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        result = eng.kg_KNN_VEC("[1.0, 0.0, 0.0, 0.0]", k=5, label_filter="Disease")
        assert len(result) == 1
        assert result[0][0] == "disease_node"

    def test_entity_id_lookup_path(self):
        """When query_vector is a plain entity ID (not []), looks up the embedding first."""
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.return_value = None
        # First fetchone: embedding lookup; subsequent fetchall: results
        cursor.fetchone.return_value = ("1.0,0.0,0.0,0.0",)
        cursor.fetchall.return_value = [("other_node", 0.8)]
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        result = eng.kg_KNN_VEC("entity_abc", k=5)
        assert len(result) == 1

    def test_entity_id_not_found_returns_empty(self):
        """If entity embedding lookup returns None, returns []."""
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.return_value = None
        cursor.fetchone.return_value = None
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        result = eng.kg_KNN_VEC("unknown_entity", k=5)
        assert result == []

    def test_falls_back_on_sql_exception(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        # Query starts with "[" so only one execute call (the main query).
        # Make it raise immediately to trigger the fallback.
        cursor.execute.side_effect = RuntimeError("VECTOR_COSINE not supported")
        cursor.close.return_value = None
        conn.cursor.return_value = cursor
        with patch.object(eng, "_kg_KNN_VEC_python_optimized", return_value=[]) as mock_fb:
            result = eng.kg_KNN_VEC("[1.0,0.0,0.0,0.0]", k=5)
        mock_fb.assert_called_once()


# ---------------------------------------------------------------------------
# _kg_KNN_VEC_python_optimized
# ---------------------------------------------------------------------------

class TestKgKNNVECPythonOptimized:

    def test_no_label_filter(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.return_value = None
        cursor.fetchall.return_value = [("n1", 0.88), ("n2", 0.72)]
        cursor.close.return_value = None
        conn.cursor.return_value = cursor

        with patch("iris_vector_graph._engine.vector.logger"):
            with patch("iris_vector_graph.embedded._sql_statement_execute", side_effect=RuntimeError("no embedded")):
                conn.cursor.return_value = cursor
                result = eng._kg_KNN_VEC_python_optimized("[1.0,0.0,0.0,0.0]", k=5)
        assert isinstance(result, list)

    def test_with_label_filter(self):
        eng, conn, _ = _make_eng()
        cursor = MagicMock()
        cursor.execute.return_value = None
        cursor.fetchall.return_value = [("disease_1", 0.91)]
        cursor.close.return_value = None
        conn.cursor.return_value = cursor

        with patch("iris_vector_graph.embedded._sql_statement_execute", side_effect=RuntimeError("no embedded")):
            result = eng._kg_KNN_VEC_python_optimized("[1.0,0.0,0.0,0.0]", k=5, label_filter="Disease")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _build_index_registry: gref path (lines 49, 62-64), SQL fallback (85-90)
# ---------------------------------------------------------------------------

class TestBuildIndexRegistry:

    def test_gref_path_covered(self):
        """Lines 49-64: gref available — iterated to build registry."""
        eng, conn, cursor = _make_eng()
        mock_iris = MagicMock()
        gref_obj = MagicMock()
        gref_obj.order.side_effect = [["idx1"], [""]]  # one name then stop
        mock_iris.gref.return_value = gref_obj
        mock_iris.gref.__bool__ = lambda s: True

        with patch.dict("sys.modules", {"iris": mock_iris}):
            # IVF global: return idx1 then stop; others stop immediately
            gref_obj.order.side_effect = [
                "idx1", "",  # ^IVF: returns name, then empty
                "",           # ^VecIdx
                "",           # ^BM25Idx
                "",           # ^PLAID
            ]
            with patch.object(eng, "_probe_native_vec", return_value=False):
                registry = eng._build_index_registry()
        assert isinstance(registry, dict)

    def test_gref_exception_falls_to_classmethod(self):
        """Lines 62-64: gref raises → falls through to _call_classmethod path."""
        eng, conn, cursor = _make_eng()
        mock_iris = MagicMock()
        mock_iris.gref.side_effect = AttributeError("gref not callable")

        with patch.dict("sys.modules", {"iris": mock_iris}):
            with patch("iris_vector_graph.schema._call_classmethod", return_value="idx1,idx2"):
                with patch.object(eng, "_probe_native_vec", return_value=False):
                    registry = eng._build_index_registry()
        assert isinstance(registry, dict)


# ---------------------------------------------------------------------------
# Index routing methods (lines 119-165)
# ---------------------------------------------------------------------------

class TestIndexRoutingMethods:

    def _make_eng_with_vec_config(self):
        eng, conn, cursor = _make_eng()
        cfg = MagicMock()
        cfg.method = "vec"
        cfg.dim = 4
        cfg.metric = "cosine"
        cfg.name = "myidx"
        eng._pending_index_config["myidx"] = cfg
        eng._index_registry["myidx"] = "vec"
        return eng, conn, cursor, cfg

    def test_build_vector_index_vec_method(self):
        eng, conn, cursor, cfg = self._make_eng_with_vec_config()
        with patch.object(eng, "vec_create_index", return_value={}):
            with patch.object(eng, "vec_build", return_value={"built": True}) as mock_build:
                result = eng._build_vector_index("myidx")
        mock_build.assert_called_once()

    def test_search_vector_index_vec_method(self):
        eng, conn, cursor, cfg = self._make_eng_with_vec_config()
        with patch.object(eng, "vec_search", return_value=[("n1", 0.9)]) as mock_vs:
            result = eng._search_vector_index("myidx", [1.0, 0.0, 0.0, 0.0])
        mock_vs.assert_called_once()

    def test_vector_index_insert_vec(self):
        eng, conn, cursor, cfg = self._make_eng_with_vec_config()
        with patch.object(eng, "vec_insert") as mock_vi:
            eng._vector_index_insert("myidx", "n1", [1.0, 0.0, 0.0, 0.0])
        mock_vi.assert_called_once()

    def test_vector_index_drop_vec(self):
        eng, conn, cursor, cfg = self._make_eng_with_vec_config()
        with patch.object(eng, "vec_drop") as mock_vd:
            eng._vector_index_drop("myidx")
        mock_vd.assert_called_once()

    def test_vector_index_info_vec(self):
        eng, conn, cursor, cfg = self._make_eng_with_vec_config()
        with patch.object(eng, "vec_info", return_value={"type": "vec"}) as mock_vi:
            result = eng._vector_index_info("myidx")
        mock_vi.assert_called_once()

    def test_build_fulltext_index(self):
        eng, conn, cursor = _make_eng()
        with patch.object(eng, "bm25_build", return_value={"rows": 5}) as mock_bm:
            with patch("iris_vector_graph.index_protocol._rows_of", return_value=5):
                result = eng._build_fulltext_index("myidx", properties=["name"])
        mock_bm.assert_called_once()

    def test_build_multivector_index(self):
        eng, conn, cursor = _make_eng()
        docs = [{"id": "n1", "vec": [1.0]}]
        with patch.object(eng, "plaid_build", return_value={"built": True}) as mock_pb:
            result = eng._build_multivector_index("myidx", docs=docs)
        mock_pb.assert_called_once()


# ---------------------------------------------------------------------------
# kg_KNN_VEC: label_filter+exclude_id branch (line 256)
# ---------------------------------------------------------------------------

class TestKgKNNVECBranches:

    def test_label_filter_and_exclude_id(self):
        """Line 256: both label_filter and exclude_id active — pass node-id string (not JSON array)."""
        eng, conn, cursor = _make_eng()
        # When query_vector doesn't start with '[', it's treated as a node-id → exclude_id is set
        cursor.fetchone.return_value = ("1,0,0,0",)  # emb for the node
        cursor.fetchall.return_value = [("n2", 0.85)]
        cursor.description = [("id", None), ("score", None)]

        result = eng.kg_KNN_VEC("node_id_n1", k=5, label_filter="Gene")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# search_nodes_by_vector: ivf_name path (lines 311-312)
# ---------------------------------------------------------------------------

class TestSearchNodesByVectorBranches:

    def test_ivf_name_path(self):
        """Lines 311-312: ivf_name explicitly provided → ivf_search."""
        eng, conn, cursor = _make_eng()
        with patch.object(eng, "_probe_native_vec", return_value=False):
            with patch.object(eng, "ivf_search", return_value=[("n1", 0.9)]) as mock_ivf:
                result = eng.search_nodes_by_vector(
                    [1.0, 0.0, 0.0, 0.0], ivf_name="myivf"
                )
        mock_ivf.assert_called_once()

    def test_no_ivf_name_default_path(self):
        """Line 312: no ivf_name → ivf_search with 'default'."""
        eng, conn, cursor = _make_eng()
        with patch.object(eng, "_probe_native_vec", return_value=False):
            with patch.object(eng, "ivf_search", return_value=[]) as mock_ivf:
                result = eng.search_nodes_by_vector([1.0, 0.0, 0.0, 0.0])
        mock_ivf.assert_called_with("default", [1.0, 0.0, 0.0, 0.0], k=10, nprobe=8)


# ---------------------------------------------------------------------------
# multi_vector_search: non-rrf fusion path (lines 666-676)
# ---------------------------------------------------------------------------

class TestMultiVectorSearchNonRRF:

    def test_non_rrf_fusion_deduplication(self):
        """Lines 666-676: fusion != rrf → score-sorted dedup merge."""
        eng, conn, cursor = _make_eng()
        sources = [
            {"table": "Graph_KG.nodes", "col": "emb"},
        ]
        with patch.object(eng, "vector_search", return_value=[
            {"id": "n1", "score": 0.9},
            {"id": "n2", "score": 0.8},
            {"id": "n1", "score": 0.7},  # dup
        ]):
            result = eng.multi_vector_search(sources, [1.0, 0.0, 0.0, 0.0], fusion="max")

        ids = [r["id"] for r in result]
        assert ids.count("n1") == 1


# ---------------------------------------------------------------------------
# kg_RRF_FUSE: ivf + bm25 dispatch (lines 691-710)
# ---------------------------------------------------------------------------

class TestKgRRFFuse:

    def test_fuse_dispatches_to_ivf_and_bm25(self):
        """Lines 691-710: looks up ivf and bm25 indexes, computes RRF scores."""
        eng, conn, cursor = _make_eng()
        eng._index_registry = {"ivf1": "ivf", "bm1": "bm25"}

        with patch.object(eng, "ivf_search", return_value=[{"id": "n1", "score": 0.9}]):
            with patch.object(eng, "bm25_search", return_value=[("n1", 0.8), ("n2", 0.6)]):
                result = eng.kg_RRF_FUSE(5, 10, 10, 60, "[1,0,0,0]", "query")

        assert isinstance(result, list)
        assert all(len(r) == 4 for r in result)


# ---------------------------------------------------------------------------
# kg_VECTOR_GRAPH_SEARCH: expansion + text path (lines 750, 754-760)
# ---------------------------------------------------------------------------

class TestKgVectorGraphSearch:

    def test_with_text_and_expansion(self):
        """Lines 750, 754-760: text query + expansion paths."""
        eng, conn, cursor = _make_eng()
        with patch.object(eng, "kg_KNN_VEC", return_value=[("n1", 0.9), ("n2", 0.8)]):
            with patch.object(eng, "kg_NEIGHBORHOOD_EXPANSION", return_value=[
                {"target": "n3", "confidence": 900}
            ]):
                with patch.object(eng, "kg_TXT", return_value=[("n1", 5), ("n4", 3)]):
                    result = eng.kg_VECTOR_GRAPH_SEARCH(
                        "[1,0,0,0]", query_text="cancer", k=5
                    )
        assert isinstance(result, list)
        assert all("entity_id" in r for r in result)

    def test_no_text_query(self):
        """Lines 763-764: no text query → only vector + expansion."""
        eng, conn, cursor = _make_eng()
        with patch.object(eng, "kg_KNN_VEC", return_value=[("n1", 0.9)]):
            with patch.object(eng, "kg_NEIGHBORHOOD_EXPANSION", return_value=[]):
                result = eng.kg_VECTOR_GRAPH_SEARCH("[1,0,0,0]", k=5)
        assert isinstance(result, list)

    def test_exception_reraises(self):
        """Lines 792-794: exception is logged and re-raised."""
        eng, conn, cursor = _make_eng()
        with patch.object(eng, "kg_KNN_VEC", side_effect=RuntimeError("vector fail")):
            with pytest.raises(RuntimeError, match="vector fail"):
                eng.kg_VECTOR_GRAPH_SEARCH("[1,0,0,0]")


# ---------------------------------------------------------------------------
# vec_search_multi (lines 846-850), vec_drop (lines 871-872)
# ---------------------------------------------------------------------------

class TestVecMethods:

    def test_vec_search_multi(self):
        """Lines 846-850: SearchMultiJSON call."""
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = json.dumps([{"id": "n1", "score": 0.9}])
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng.vec_search_multi("idx", [[1.0, 0.0, 0.0, 0.0]], k=5)
        assert isinstance(result, list)

    def test_vec_drop(self):
        """Lines 871-872: Drop classmethod call."""
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            eng.vec_drop("idx")
        iris_obj.classMethodVoid.assert_called_with("Graph.KG.VecIndex", "Drop", "idx")


# ---------------------------------------------------------------------------
# ivf_build: empty node_ids (line 1002), no rows (line 1012)
# ---------------------------------------------------------------------------

class TestIvfBuild:

    def test_empty_node_ids_raises(self):
        """Line 1002: empty node_ids list raises ValueError."""
        eng, conn, cursor = _make_eng()
        with pytest.raises(ValueError, match="node_ids list is empty"):
            eng.ivf_build("myidx", node_ids=[])

    def test_no_rows_raises(self):
        """Line 1012: no vectors found raises ValueError."""
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = []
        with pytest.raises(ValueError, match="no vectors found"):
            eng.ivf_build("myidx")
