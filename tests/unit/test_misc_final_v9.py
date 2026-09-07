"""
Coverage tests for:
  - iris_vector_graph/gql/schema.py       (lines 37,41-42,46-47,51-55,64-65,79,88,92,102,107,109,111,136-139,150-151,155-156,160-161,165-166)
  - iris_vector_graph/fusion.py           (lines 79,144-145,155-156,176-180,189-190,194-195,206,280-289)
  - iris_vector_graph/embedded.py         (lines 105,125-134,146-147,150-153,157,169-170,172-173,254,259-262,276-277,320-321,326-327)
  - iris_vector_graph/cypher/parser.py    (lines 80,99,280-299,301-317,386-387,475,588,614,620,624,633,645,648,692,713,730,753,781,786,808,824,829,850,913-914,923-930,968-969,1011-1019,1029-1040,1134-1135,1201-1209,1288-1290,1294-1301,1372-1376,1393-1394,1410,1427-1429,1440,1539,1567-1569,1588-1598,1649-1650,1655,1682,1692,1721-1732,1767)

All tests use mocks — no IRIS connection required.
"""
from __future__ import annotations

import sys
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ===========================================================================
# Helpers
# ===========================================================================

def _make_conn():
    cursor = MagicMock()
    cursor.execute.return_value = None
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.description = [("col1",)]
    cursor.close.return_value = None
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.commit.return_value = None
    return conn, cursor


# ===========================================================================
# gql/schema.py tests
# ===========================================================================

class TestGqlSchemaModule:

    def test_sanitize_gql_name_plain(self):
        from iris_vector_graph.gql.schema import _sanitize_gql_name
        assert _sanitize_gql_name("Person") == "Person"

    def test_sanitize_gql_name_special_chars(self):
        from iris_vector_graph.gql.schema import _sanitize_gql_name
        result = _sanitize_gql_name("foo-bar.baz")
        assert "-" not in result and "." not in result

    def test_sanitize_gql_name_starts_with_digit(self):
        from iris_vector_graph.gql.schema import _sanitize_gql_name
        result = _sanitize_gql_name("123Type")
        assert result.startswith("T_")

    def test_sanitize_gql_name_empty(self):
        from iris_vector_graph.gql.schema import _sanitize_gql_name
        # empty string after substitution should return "Unknown"
        result = _sanitize_gql_name("")
        assert result == "Unknown"

    def test_direction_enum(self):
        from iris_vector_graph.gql.schema import Direction
        assert Direction.OUTGOING.value == "OUTGOING"
        assert Direction.INCOMING.value == "INCOMING"

    def test_property_type(self):
        from iris_vector_graph.gql.schema import Property
        p = Property(key="name", value="Alice")
        assert p.key == "name"
        assert p.value == "Alice"

    def test_property_type_none_value(self):
        from iris_vector_graph.gql.schema import Property
        p = Property(key="age", value=None)
        assert p.value is None

    def test_graph_stats_type(self):
        from iris_vector_graph.gql.schema import GraphStats
        gs = GraphStats(node_count=10, edge_count=20, label_count=5)
        assert gs.node_count == 10
        assert gs.edge_count == 20
        assert gs.label_count == 5

    def test_cypher_result_type(self):
        from iris_vector_graph.gql.schema import CypherResult
        cr = CypherResult(columns=["a", "b"], rows=[{"a": 1}])
        assert cr.columns == ["a", "b"]
        assert len(cr.rows) == 1

    def test_relationship_type(self):
        from iris_vector_graph.gql.schema import Relationship
        r = Relationship(predicate="KNOWS", target_id="node-2")
        assert r.predicate == "KNOWS"
        assert r.target_id == "node-2"

    def test_create_dynamic_node_type_basic(self):
        from iris_vector_graph.gql.schema import create_dynamic_node_type, DYNAMIC_TYPES
        DYNAMIC_TYPES.clear()
        t = create_dynamic_node_type("Person", ["name", "age"])
        assert t is not None
        assert "Person" in DYNAMIC_TYPES

    def test_create_dynamic_node_type_cached(self):
        from iris_vector_graph.gql.schema import create_dynamic_node_type, DYNAMIC_TYPES
        DYNAMIC_TYPES.clear()
        t1 = create_dynamic_node_type("Org", ["name"])
        t2 = create_dynamic_node_type("Org", ["name", "extra"])
        assert t1 is t2  # cached

    def test_create_dynamic_node_type_reserved_prop(self):
        from iris_vector_graph.gql.schema import create_dynamic_node_type, DYNAMIC_TYPES
        DYNAMIC_TYPES.clear()
        # 'id' and 'labels' are reserved keywords
        t = create_dynamic_node_type("Widget", ["id", "labels", "color"])
        assert t is not None

    def test_create_dynamic_node_type_excluded_prop(self):
        from iris_vector_graph.gql.schema import create_dynamic_node_type, DYNAMIC_TYPES
        DYNAMIC_TYPES.clear()
        # 'emb', 'vector', 'embedding' are excluded
        t = create_dynamic_node_type("NodeWithEmb", ["emb", "vector", "title"])
        assert t is not None

    def test_create_dynamic_node_type_digit_field(self):
        from iris_vector_graph.gql.schema import create_dynamic_node_type, DYNAMIC_TYPES
        DYNAMIC_TYPES.clear()
        # field starting with digit should get p_ prefix
        t = create_dynamic_node_type("Numeric", ["1stField", "normalField"])
        assert t is not None

    def test_create_dynamic_node_type_empty_field(self):
        from iris_vector_graph.gql.schema import create_dynamic_node_type, DYNAMIC_TYPES
        DYNAMIC_TYPES.clear()
        # An empty field name after substitution should be skipped
        t = create_dynamic_node_type("EmptyField", ["---", "goodField"])
        assert t is not None

    def test_node_interface_resolve_type_primary_label(self):
        from iris_vector_graph.gql.schema import Node
        obj = MagicMock()
        obj._primary_label = "Person"
        result = Node.resolve_type(obj, None, None)
        assert result == "Person"

    def test_node_interface_resolve_type_labels(self):
        from iris_vector_graph.gql.schema import Node
        obj = MagicMock(spec=[])
        obj.labels = ["Animal", "Dog"]
        result = Node.resolve_type(obj, None, None)
        assert result == "Animal"

    def test_node_interface_resolve_type_no_labels(self):
        from iris_vector_graph.gql.schema import Node
        obj = MagicMock(spec=[])
        obj.labels = []
        result = Node.resolve_type(obj, None, None)
        assert result is None

    def test_node_properties_returns_empty(self):
        from iris_vector_graph.gql.schema import Node
        # properties() returns [] by default (interface method)
        n = Node.__dict__["properties"]
        # Just verify it's callable (the interface defines it)
        assert callable(n)

    def test_build_schema_calls_engine(self):
        from iris_vector_graph.gql.schema import build_schema, DYNAMIC_TYPES
        DYNAMIC_TYPES.clear()
        engine = MagicMock()
        engine.get_schema_metadata.return_value = {"Person": {"name", "age"}, "Car": {"make"}}
        schema = build_schema(engine)
        assert schema is not None
        assert engine.get_schema_metadata.called

    def test_build_schema_empty_metadata(self):
        from iris_vector_graph.gql.schema import build_schema, DYNAMIC_TYPES
        DYNAMIC_TYPES.clear()
        engine = MagicMock()
        engine.get_schema_metadata.return_value = {}
        schema = build_schema(engine)
        assert schema is not None

    def test_build_schema_clears_dynamic_types(self):
        from iris_vector_graph.gql.schema import build_schema, DYNAMIC_TYPES
        # Pre-populate
        DYNAMIC_TYPES["OldType"] = MagicMock()
        engine = MagicMock()
        engine.get_schema_metadata.return_value = {}
        build_schema(engine)
        assert "OldType" not in DYNAMIC_TYPES

    def test_build_schema_handles_exception(self):
        """Exception in create_dynamic_node_type is swallowed."""
        from iris_vector_graph.gql.schema import build_schema, DYNAMIC_TYPES
        DYNAMIC_TYPES.clear()
        engine = MagicMock()
        # Return a label with properties that cause an error; the schema builder
        # catches all exceptions per label
        engine.get_schema_metadata.return_value = {"BadLabel": {"prop"}}
        with patch("iris_vector_graph.gql.schema.create_dynamic_node_type", side_effect=Exception("boom")):
            schema = build_schema(engine)
        assert schema is not None


# ===========================================================================
# fusion.py tests
# ===========================================================================

class TestRRFFusion:

    def test_fuse_results_single_list(self):
        from iris_vector_graph.fusion import RRFFusion
        results = RRFFusion.fuse_results([[("a", 0.9), ("b", 0.8), ("c", 0.7)]])
        assert len(results) == 3
        ids = [r[0] for r in results]
        assert "a" in ids

    def test_fuse_results_multiple_lists(self):
        from iris_vector_graph.fusion import RRFFusion
        list1 = [("a", 0.9), ("b", 0.8)]
        list2 = [("b", 0.95), ("c", 0.85)]
        results = RRFFusion.fuse_results([list1, list2])
        ids = [r[0] for r in results]
        assert "b" in ids  # appears in both

    def test_fuse_results_empty_lists(self):
        from iris_vector_graph.fusion import RRFFusion
        results = RRFFusion.fuse_results([[], []])
        assert results == []

    def test_fuse_results_custom_c(self):
        from iris_vector_graph.fusion import RRFFusion
        list1 = [("x", 1.0)]
        results = RRFFusion.fuse_results([list1], c=30)
        assert len(results) == 1
        assert abs(results[0][1] - 1.0 / (30 + 1)) < 1e-9

    def test_fuse_results_sorted_descending(self):
        from iris_vector_graph.fusion import RRFFusion
        list1 = [("a", 0.5), ("b", 0.9)]
        list2 = [("b", 0.7), ("a", 0.3)]
        results = RRFFusion.fuse_results([list1, list2])
        scores = [r[1] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_weighted_fusion_basic(self):
        from iris_vector_graph.fusion import RRFFusion
        list1 = [("a", 0.9), ("b", 0.5)]
        list2 = [("b", 0.8), ("c", 0.6)]
        results = RRFFusion.weighted_fusion([list1, list2], [0.6, 0.4])
        assert len(results) >= 2

    def test_weighted_fusion_wrong_lengths(self):
        from iris_vector_graph.fusion import RRFFusion
        with pytest.raises(ValueError, match="must match"):
            RRFFusion.weighted_fusion([[("a", 1.0)]], [0.5, 0.5])

    def test_weighted_fusion_zero_weight_sum(self):
        from iris_vector_graph.fusion import RRFFusion
        # weights all zero: weight_sum == 0, so weights stay as-is (not divided)
        list1 = [("a", 1.0)]
        results = RRFFusion.weighted_fusion([list1], [0.0])
        assert len(results) == 1

    def test_weighted_fusion_normalized(self):
        from iris_vector_graph.fusion import RRFFusion
        list1 = [("a", 1.0)]
        list2 = [("b", 1.0)]
        results = RRFFusion.weighted_fusion([list1, list2], [2.0, 2.0])
        # Weights normalized to [0.5, 0.5]
        assert len(results) == 2


class TestHybridSearchFusion:

    def _make_hybrid(self):
        from iris_vector_graph.fusion import HybridSearchFusion
        engine = MagicMock()
        engine.kg_KNN_VEC.return_value = [("n1", 0.9), ("n2", 0.8)]
        engine.kg_TXT.return_value = [("n2", 0.7), ("n3", 0.6)]
        engine.kg_NEIGHBORHOOD_EXPANSION.return_value = [
            {"target": "n4", "confidence": 700},
            {"target": "n2", "confidence": 900},
        ]
        return HybridSearchFusion(engine), engine

    def test_multi_modal_no_query_raises(self):
        from iris_vector_graph.fusion import HybridSearchFusion
        hsf = HybridSearchFusion(MagicMock())
        with pytest.raises(ValueError, match="At least one"):
            hsf.multi_modal_search()

    def test_multi_modal_vector_only(self):
        hsf, engine = self._make_hybrid()
        results = hsf.multi_modal_search(query_vector='[0.1,0.2]', k=5)
        assert isinstance(results, list)

    def test_multi_modal_text_only(self):
        hsf, engine = self._make_hybrid()
        results = hsf.multi_modal_search(query_text="find me", k=5)
        assert isinstance(results, list)

    def test_multi_modal_both(self):
        hsf, engine = self._make_hybrid()
        results = hsf.multi_modal_search(query_vector='[0.1]', query_text="find me", k=5)
        assert isinstance(results, list)

    def test_multi_modal_weighted_fusion(self):
        # Graph expansion produces a 3rd result list, so supply 3 weights.
        hsf, engine = self._make_hybrid()
        results = hsf.multi_modal_search(
            query_vector='[0.1]', query_text="test",
            k=5, fusion_method="weighted", weights=[0.5, 0.3, 0.2]
        )
        assert isinstance(results, list)

    def test_multi_modal_weighted_no_weights(self):
        hsf, engine = self._make_hybrid()
        results = hsf.multi_modal_search(
            query_vector='[0.1]', query_text="test",
            k=5, fusion_method="weighted"
        )
        assert isinstance(results, list)

    def test_multi_modal_unknown_fusion(self):
        hsf, engine = self._make_hybrid()
        with pytest.raises(ValueError, match="Unknown fusion"):
            hsf.multi_modal_search(query_text="test", k=5, fusion_method="unknown")

    def test_multi_modal_vector_failure(self):
        from iris_vector_graph.fusion import HybridSearchFusion
        engine = MagicMock()
        engine.kg_KNN_VEC.side_effect = RuntimeError("vec fail")
        engine.kg_TXT.return_value = [("n1", 0.8)]
        engine.kg_NEIGHBORHOOD_EXPANSION.return_value = []
        hsf = HybridSearchFusion(engine)
        results = hsf.multi_modal_search(query_vector='[0.1]', query_text="test", k=5)
        assert isinstance(results, list)

    def test_multi_modal_text_failure(self):
        # fusion.py re-raises kg_TXT failures so callers can detect degraded hybrid search.
        # The old behavior (silent WARNING) let SQLCODE -51 go undetected for release cycles.
        from iris_vector_graph.fusion import HybridSearchFusion
        engine = MagicMock()
        engine.kg_KNN_VEC.return_value = [("n1", 0.9)]
        engine.kg_TXT.side_effect = RuntimeError("txt fail")
        engine.kg_NEIGHBORHOOD_EXPANSION.return_value = []
        hsf = HybridSearchFusion(engine)
        with pytest.raises(RuntimeError, match="txt fail"):
            hsf.multi_modal_search(query_vector='[0.1]', query_text="test", k=5)

    def test_multi_modal_graph_expansion_failure(self):
        from iris_vector_graph.fusion import HybridSearchFusion
        engine = MagicMock()
        engine.kg_KNN_VEC.return_value = [("n1", 0.9)]
        engine.kg_TXT.return_value = [("n2", 0.8)]
        engine.kg_NEIGHBORHOOD_EXPANSION.side_effect = RuntimeError("graph fail")
        hsf = HybridSearchFusion(engine)
        results = hsf.multi_modal_search(query_vector='[0.1]', query_text="test", k=5)
        assert isinstance(results, list)

    def test_multi_modal_no_results(self):
        # Both legs failing: kg_TXT re-raises, so the caller sees the error.
        # If only vector fails (warned), but text also fails → raises on text.
        from iris_vector_graph.fusion import HybridSearchFusion
        engine = MagicMock()
        engine.kg_KNN_VEC.side_effect = RuntimeError("fail")
        engine.kg_TXT.side_effect = RuntimeError("fail")
        hsf = HybridSearchFusion(engine)
        with pytest.raises(RuntimeError, match="fail"):
            hsf.multi_modal_search(query_vector='[0.1]', query_text="test", k=5)

    def test_adaptive_search_short_query(self):
        hsf, engine = self._make_hybrid()
        # Short query: use_vector=False
        results = hsf.adaptive_search("find", k=5)
        assert isinstance(results, list)

    def test_adaptive_search_long_query(self):
        hsf, engine = self._make_hybrid()
        results = hsf.adaptive_search("find the related node entity", k=5)
        assert isinstance(results, list)

    def test_adaptive_search_relationship_query(self):
        hsf, engine = self._make_hybrid()
        results = hsf.adaptive_search("nodes connected to each other", k=5)
        assert isinstance(results, list)

    def test_adaptive_search_exception_fallback(self):
        from iris_vector_graph.fusion import HybridSearchFusion
        engine = MagicMock()
        engine.kg_TXT.return_value = [("n1", 0.9), ("n2", 0.7)]
        # multi_modal_search will call kg_TXT; make kg_NEIGHBORHOOD_EXPANSION raise
        engine.kg_NEIGHBORHOOD_EXPANSION.side_effect = RuntimeError("fail")
        hsf = HybridSearchFusion(engine)
        # Force multi_modal_search to fail to test fallback
        with patch.object(hsf, "multi_modal_search", side_effect=Exception("search failed")):
            results = hsf.adaptive_search("test query entity", k=5)
        assert isinstance(results, list)

    def test_adaptive_search_full_failure(self):
        from iris_vector_graph.fusion import HybridSearchFusion
        engine = MagicMock()
        engine.kg_TXT.side_effect = RuntimeError("txt fail")
        engine.kg_NEIGHBORHOOD_EXPANSION.side_effect = RuntimeError("fail")
        hsf = HybridSearchFusion(engine)
        with patch.object(hsf, "multi_modal_search", side_effect=Exception("fail")):
            results = hsf.adaptive_search("query", k=5)
        assert results == []


# ===========================================================================
# embedded.py tests
# ===========================================================================

class TestInlineParams:

    def test_inline_str(self):
        from iris_vector_graph.embedded import _inline_params
        result = _inline_params("SELECT * FROM t WHERE name = ?", ["Alice"])
        assert result == "SELECT * FROM t WHERE name = 'Alice'"

    def test_inline_int(self):
        from iris_vector_graph.embedded import _inline_params
        result = _inline_params("SELECT ? + 1", [42])
        assert result == "SELECT 42 + 1"

    def test_inline_float(self):
        from iris_vector_graph.embedded import _inline_params
        result = _inline_params("SELECT ?", [3.14])
        assert "3.14" in result

    def test_inline_none(self):
        from iris_vector_graph.embedded import _inline_params
        result = _inline_params("WHERE x = ?", [None])
        assert "NULL" in result

    def test_inline_bool_true(self):
        from iris_vector_graph.embedded import _inline_params
        result = _inline_params("WHERE active = ?", [True])
        assert "1" in result

    def test_inline_bool_false(self):
        from iris_vector_graph.embedded import _inline_params
        result = _inline_params("WHERE active = ?", [False])
        assert "0" in result

    def test_inline_single_quote_escape(self):
        from iris_vector_graph.embedded import _inline_params
        result = _inline_params("WHERE name = ?", ["O'Brien"])
        assert "O''Brien" in result

    def test_inline_not_enough_params(self):
        from iris_vector_graph.embedded import _inline_params
        with pytest.raises(IndexError):
            _inline_params("SELECT ? AND ?", [1])

    def test_inline_no_params(self):
        from iris_vector_graph.embedded import _inline_params
        result = _inline_params("SELECT 1", [])
        assert result == "SELECT 1"

    def test_inline_multiple_params(self):
        from iris_vector_graph.embedded import _inline_params
        result = _inline_params("? AND ?", [1, 2])
        assert result == "1 AND 2"


class TestIsDdtabError:

    def test_unimplemented(self):
        from iris_vector_graph.embedded import _is_ddtab_error
        exc = Exception("<UNIMPLEMENTED> something")
        assert _is_ddtab_error(exc) is True

    def test_ddtab(self):
        from iris_vector_graph.embedded import _is_ddtab_error
        exc = Exception("ddtab error occurred")
        assert _is_ddtab_error(exc) is True

    def test_normal_error(self):
        from iris_vector_graph.embedded import _is_ddtab_error
        exc = Exception("normal SQL error")
        assert _is_ddtab_error(exc) is False


class TestEmbeddedCursorWithMockIris:
    """Test EmbeddedCursor internals using a mock iris_sql."""

    def _make_cursor(self):
        from iris_vector_graph.embedded import EmbeddedCursor

        mock_rs = MagicMock()
        mock_rs.columnCount.return_value = 2
        mock_rs.columnName.side_effect = lambda i: f"col{i}"
        rows = [(1, "a"), (2, "b"), (3, "c")]
        mock_rs.__iter__ = lambda self: iter(rows)

        mock_stmt = MagicMock()
        mock_stmt.execute.return_value = mock_rs

        mock_iris_sql = MagicMock()
        mock_iris_sql.prepare.return_value = mock_stmt

        cursor = EmbeddedCursor(iris_sql=mock_iris_sql)
        return cursor, mock_iris_sql, mock_rs, mock_stmt

    def test_execute_select(self):
        cursor, mock_iris_sql, mock_rs, mock_stmt = self._make_cursor()
        cursor.execute("SELECT 1")
        assert mock_iris_sql.prepare.called

    def test_execute_transaction_commands(self):
        cursor, mock_iris_sql, _, _ = self._make_cursor()
        for cmd in ("START TRANSACTION", "COMMIT", "ROLLBACK", "BEGIN", "BEGIN TRANSACTION"):
            cursor.execute(cmd)
            assert cursor._rs is None

    def test_execute_with_params(self):
        cursor, mock_iris_sql, mock_rs, mock_stmt = self._make_cursor()
        cursor.execute("SELECT * WHERE x = ?", [42])
        mock_stmt.execute.assert_called_with(42)

    def test_execute_description_populated(self):
        cursor, mock_iris_sql, mock_rs, mock_stmt = self._make_cursor()
        cursor.execute("SELECT a, b FROM t")
        assert cursor.description is not None
        assert len(cursor.description) == 2

    def test_fetchall(self):
        cursor, mock_iris_sql, mock_rs, mock_stmt = self._make_cursor()
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
        assert isinstance(rows, list)

    def test_fetchone(self):
        cursor, mock_iris_sql, mock_rs, mock_stmt = self._make_cursor()
        cursor.execute("SELECT 1")
        row = cursor.fetchone()
        # may be None or a tuple depending on the mock
        assert row is None or isinstance(row, tuple)

    def test_fetchmany(self):
        cursor, mock_iris_sql, mock_rs, mock_stmt = self._make_cursor()
        cursor.execute("SELECT 1")
        rows = cursor.fetchmany(2)
        assert isinstance(rows, list)

    def test_close(self):
        cursor, mock_iris_sql, mock_rs, mock_stmt = self._make_cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        assert cursor._rs is None

    def test_execute_ddtab_fallback_to_exec(self):
        from iris_vector_graph.embedded import EmbeddedCursor

        mock_rs = MagicMock()
        mock_rs.columnCount.return_value = 0
        mock_rs.__iter__ = lambda self: iter([])

        mock_stmt = MagicMock()
        mock_stmt.execute.side_effect = Exception("<UNIMPLEMENTED> ddtab")

        mock_iris_sql = MagicMock()
        mock_iris_sql.prepare.return_value = mock_stmt
        mock_iris_sql.exec.return_value = mock_rs

        cursor = EmbeddedCursor(iris_sql=mock_iris_sql)
        cursor.execute("SELECT 1", [42])
        assert mock_iris_sql.exec.called

    def test_execute_ddtab_all_fallbacks_fail(self):
        from iris_vector_graph.embedded import EmbeddedCursor, _SqlStatementResultSet

        mock_stmt = MagicMock()
        mock_stmt.execute.side_effect = Exception("<UNIMPLEMENTED>")

        mock_iris_sql = MagicMock()
        mock_iris_sql.prepare.return_value = mock_stmt
        mock_iris_sql.exec.side_effect = Exception("<UNIMPLEMENTED>")

        with patch("iris_vector_graph.embedded._sql_statement_execute", side_effect=RuntimeError("%%SQL.Statement failed")):
            cursor = EmbeddedCursor(iris_sql=mock_iris_sql)
            with pytest.raises(RuntimeError, match="All three embedded"):
                cursor.execute("SELECT 1", [])

    def test_executemany_basic(self):
        cursor, mock_iris_sql, mock_rs, mock_stmt = self._make_cursor()
        cursor.executemany("INSERT INTO t VALUES (?)", [(1,), (2,), (3,)])
        assert cursor.rowcount == 3

    def test_executemany_ddtab_fallback(self):
        from iris_vector_graph.embedded import EmbeddedCursor

        mock_rs = MagicMock()
        mock_rs.columnCount.return_value = 0
        mock_rs.__iter__ = lambda self: iter([])

        mock_stmt = MagicMock()
        mock_stmt.execute.side_effect = Exception("<UNIMPLEMENTED>")

        mock_iris_sql = MagicMock()
        mock_iris_sql.prepare.return_value = mock_stmt
        mock_iris_sql.exec.return_value = mock_rs

        cursor = EmbeddedCursor(iris_sql=mock_iris_sql)
        cursor.executemany("INSERT INTO t VALUES (?)", [(1,), (2,)])
        assert cursor.rowcount == 2

    def test_fetchone_exhausted(self):
        cursor, mock_iris_sql, mock_rs, mock_stmt = self._make_cursor()
        cursor.execute("SELECT 1")
        # Drain everything first
        cursor.fetchall()
        row = cursor.fetchone()
        assert row is None


class TestEmbeddedConnection:

    def test_cursor_returns_embedded_cursor(self):
        from iris_vector_graph.embedded import EmbeddedConnection, EmbeddedCursor
        mock_iris_sql = MagicMock()
        conn = EmbeddedConnection(iris_sql=mock_iris_sql)
        c = conn.cursor()
        assert isinstance(c, EmbeddedCursor)

    def test_close_no_op(self):
        from iris_vector_graph.embedded import EmbeddedConnection
        conn = EmbeddedConnection(iris_sql=MagicMock())
        conn.close()  # should not raise

    def test_commit_with_mock_iris_sql(self):
        from iris_vector_graph.embedded import EmbeddedConnection
        mock_iris_sql = MagicMock()
        conn = EmbeddedConnection(iris_sql=mock_iris_sql)
        conn.commit()
        mock_iris_sql.exec.assert_called_with("COMMIT")

    def test_rollback_with_mock_iris_sql(self):
        from iris_vector_graph.embedded import EmbeddedConnection
        mock_iris_sql = MagicMock()
        conn = EmbeddedConnection(iris_sql=mock_iris_sql)
        conn.rollback()
        mock_iris_sql.exec.assert_called_with("ROLLBACK")

    def test_commit_exception_swallowed(self):
        from iris_vector_graph.embedded import EmbeddedConnection
        mock_iris_sql = MagicMock()
        mock_iris_sql.exec.side_effect = Exception("fail")
        conn = EmbeddedConnection(iris_sql=mock_iris_sql)
        conn.commit()  # should not raise

    def test_rollback_exception_swallowed(self):
        from iris_vector_graph.embedded import EmbeddedConnection
        mock_iris_sql = MagicMock()
        mock_iris_sql.exec.side_effect = Exception("fail")
        conn = EmbeddedConnection(iris_sql=mock_iris_sql)
        conn.rollback()  # should not raise

    def test_commit_no_iris_sql_no_iris_module(self):
        from iris_vector_graph.embedded import EmbeddedConnection
        conn = EmbeddedConnection(iris_sql=None)
        # Without real iris module, commit() will fail _require_iris_sql but swallows it
        conn.commit()  # should not raise

    def test_rollback_no_iris_sql_no_iris_module(self):
        from iris_vector_graph.embedded import EmbeddedConnection
        conn = EmbeddedConnection(iris_sql=None)
        conn.rollback()  # should not raise


class TestSqlStatementResultSet:

    def test_column_count(self):
        from iris_vector_graph.embedded import _SqlStatementResultSet
        rs_mock = MagicMock()
        rs_mock._GetProperty.return_value = 3
        rs = _SqlStatementResultSet(rs_mock)
        assert rs.columnCount() == 3

    def test_column_count_exception(self):
        from iris_vector_graph.embedded import _SqlStatementResultSet
        rs_mock = MagicMock()
        rs_mock._GetProperty.side_effect = Exception("error")
        rs = _SqlStatementResultSet(rs_mock)
        assert rs.columnCount() == 0

    def test_column_count_none_rs(self):
        from iris_vector_graph.embedded import _SqlStatementResultSet
        rs = _SqlStatementResultSet(None)
        assert rs.columnCount() == 0

    def test_column_name(self):
        from iris_vector_graph.embedded import _SqlStatementResultSet
        rs_mock = MagicMock()
        # chain: rs._GetProperty("MetaData")._GetProperty("columns")._GetAt(1)._GetProperty("colName")
        col_name_mock = MagicMock()
        col_name_mock.__str__ = lambda self: "myCol"
        chain = MagicMock()
        chain._GetProperty.return_value._GetProperty.return_value._GetAt.return_value._GetProperty.return_value = col_name_mock
        rs_mock._GetProperty = chain._GetProperty
        rs = _SqlStatementResultSet(rs_mock)
        name = rs.columnName(1)
        assert isinstance(name, str)

    def test_column_name_exception(self):
        from iris_vector_graph.embedded import _SqlStatementResultSet
        rs_mock = MagicMock()
        rs_mock._GetProperty.side_effect = Exception("error")
        rs = _SqlStatementResultSet(rs_mock)
        name = rs.columnName(1)
        assert name == "col1"

    def test_iter_empty(self):
        from iris_vector_graph.embedded import _SqlStatementResultSet
        rs_mock = MagicMock()
        rs_mock._Next.return_value = "0"  # Stop immediately
        rs = _SqlStatementResultSet(rs_mock)
        rows = list(rs)
        assert rows == []

    def test_iter_done(self):
        from iris_vector_graph.embedded import _SqlStatementResultSet
        rs_mock = MagicMock()
        rs = _SqlStatementResultSet(rs_mock)
        rs._done = True
        rows = list(rs)
        assert rows == []

    def test_iter_none_rs(self):
        from iris_vector_graph.embedded import _SqlStatementResultSet
        rs = _SqlStatementResultSet(None)
        rows = list(rs)
        assert rows == []


# ===========================================================================
# cypher/parser.py tests
# ===========================================================================

class TestCypherParseError:

    def test_basic_message(self):
        from iris_vector_graph.cypher.parser import CypherParseError
        err = CypherParseError("syntax error", line=1, column=5)
        assert "line 1" in str(err)
        assert "col 5" in str(err)

    def test_with_suggestion(self):
        from iris_vector_graph.cypher.parser import CypherParseError
        err = CypherParseError("bad token", suggestion="Did you mean X?")
        assert "Suggestion" in str(err)
        assert "Did you mean X?" in str(err)

    def test_no_suggestion(self):
        from iris_vector_graph.cypher.parser import CypherParseError
        err = CypherParseError("error")
        assert "Suggestion" not in str(err)


class TestParserExpectLabel:

    def test_expect_label_identifier(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n:Person) RETURN n")
        assert q is not None

    def test_expect_label_keyword_as_label(self):
        # Keywords used as node labels - END is a keyword but valid as label
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n:END) RETURN n")
        assert q is not None


class TestParserExpectIdentifierOrKeyword:

    def test_keyword_as_alias(self):
        from iris_vector_graph.cypher.parser import parse_query
        # ROWS is a keyword but valid as an alias
        q = parse_query("RETURN 1 AS rows")
        assert q is not None


class TestParserBasicQueries:

    def test_simple_match_return(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN n")
        assert q.return_clause is not None

    def test_match_with_label(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n:Person) RETURN n.name")
        assert q.return_clause is not None

    def test_match_with_props(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n {name: 'Alice'}) RETURN n")
        assert q is not None

    def test_match_with_where(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WHERE n.age > 18 RETURN n")
        assert q.query_parts[0].clauses

    def test_match_relationship(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (a)-[r]->(b) RETURN r")
        assert q is not None

    def test_match_optional(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("OPTIONAL MATCH (n) RETURN n")
        assert q is not None

    def test_return_distinct(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN DISTINCT n.name")
        assert q.return_clause.distinct is True

    def test_return_star(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN *")
        assert q.return_clause is not None

    def test_order_by(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN n ORDER BY n.name")
        assert q.order_by_clause is not None

    def test_order_by_desc(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN n ORDER BY n.name DESC")
        assert q.order_by_clause.items[0].ascending is False

    def test_order_by_descending_keyword(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN n ORDER BY n.name DESCENDING")
        assert q.order_by_clause.items[0].ascending is False

    def test_order_by_asc(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN n ORDER BY n.name ASC")
        assert q.order_by_clause.items[0].ascending is True

    def test_order_by_ascending_keyword(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN n ORDER BY n.name ASCENDING")
        assert q.order_by_clause.items[0].ascending is True

    def test_limit(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN n LIMIT 10")
        assert q.limit == 10

    def test_skip(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN n SKIP 5")
        assert q.skip == 5

    def test_skip_parameter(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN n SKIP $offset")
        assert q.skip is not None

    def test_limit_parameter(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN n LIMIT $limit")
        assert q.limit is not None

    def test_limit_expression(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN n LIMIT toInteger(3.5)")
        assert q.limit is not None

    def test_skip_expression(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN n SKIP toInteger(1.5)")
        assert q.skip is not None


class TestParserRelationships:

    def test_outgoing_arrow(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (a)-->(b) RETURN a")
        assert q is not None

    def test_incoming_arrow(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (a)<--(b) RETURN a")
        assert q is not None

    def test_bidirectional(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (a)--(b) RETURN a")
        assert q is not None

    def test_explicit_outgoing(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (a)-[r:KNOWS]->(b) RETURN r")
        assert q is not None

    def test_explicit_incoming(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (a)<-[r:KNOWS]-(b) RETURN r")
        assert q is not None

    def test_explicit_undirected(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (a)-[r:KNOWS]-(b) RETURN r")
        assert q is not None

    def test_variable_length_star(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (a)-[*]->(b) RETURN a")
        assert q is not None

    def test_variable_length_range(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (a)-[*1..3]->(b) RETURN a")
        assert q is not None

    def test_variable_length_exact(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (a)-[*2]->(b) RETURN a")
        assert q is not None

    def test_variable_length_unbounded_upper(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (a)-[*2..]->(b) RETURN a")
        assert q is not None

    def test_multi_type_relationship(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (a)-[r:KNOWS|LIKES]->(b) RETURN r")
        assert q is not None

    def test_relationship_with_props(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (a)-[r {since: 2020}]->(b) RETURN r")
        assert q is not None

    def test_shortest_path(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH p = shortestPath((a)-[*]-(b)) RETURN p")
        assert q is not None

    def test_all_shortest_paths(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH p = allShortestPaths((a)-[*]-(b)) RETURN p")
        assert q is not None

    def test_named_path(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH p = (a)-[r]->(b) RETURN p")
        assert q is not None


class TestParserExpressions:

    def test_boolean_and(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WHERE n.a = 1 AND n.b = 2 RETURN n")
        assert q is not None

    def test_boolean_or(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WHERE n.a = 1 OR n.b = 2 RETURN n")
        assert q is not None

    def test_boolean_xor(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WHERE n.a = 1 XOR n.b = 2 RETURN n")
        assert q is not None

    def test_boolean_not(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WHERE NOT n.active RETURN n")
        assert q is not None

    def test_is_null(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WHERE n.name IS NULL RETURN n")
        assert q is not None

    def test_is_not_null(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WHERE n.name IS NOT NULL RETURN n")
        assert q is not None

    def test_in_list(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WHERE n.id IN [1, 2, 3] RETURN n")
        assert q is not None

    def test_starts_with(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WHERE n.name STARTS WITH 'Al' RETURN n")
        assert q is not None

    def test_ends_with(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WHERE n.name ENDS WITH 'ice' RETURN n")
        assert q is not None

    def test_contains(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WHERE n.name CONTAINS 'lic' RETURN n")
        assert q is not None

    def test_regex_match(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WHERE n.name =~ 'Al.*' RETURN n")
        assert q is not None

    def test_case_expression(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN CASE WHEN 1 = 1 THEN 'yes' ELSE 'no' END")
        assert q is not None

    def test_case_with_test_expr(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN CASE n.type WHEN 'A' THEN 1 WHEN 'B' THEN 2 END")
        assert q is not None

    def test_arithmetic_add(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN 1 + 2")
        assert q is not None

    def test_arithmetic_subtract(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN 5 - 3")
        assert q is not None

    def test_arithmetic_multiply(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN 3 * 4")
        assert q is not None

    def test_arithmetic_divide(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN 10 / 2")
        assert q is not None

    def test_arithmetic_modulo(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN 10 % 3")
        assert q is not None

    def test_arithmetic_power(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN 2 ^ 8")
        assert q is not None

    def test_unary_minus(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN -5")
        assert q is not None

    def test_unary_minus_variable(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN -n.value")
        assert q is not None

    def test_float_literal(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN 3.14")
        assert q is not None

    def test_string_literal(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN 'hello'")
        assert q is not None

    def test_true_literal(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN true")
        assert q is not None

    def test_false_literal(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN false")
        assert q is not None

    def test_null_literal(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN null")
        assert q is not None

    def test_integer_overflow(self):
        from iris_vector_graph.cypher.parser import parse_query, CypherParseError
        with pytest.raises(CypherParseError, match="IntegerOverflow"):
            parse_query("RETURN 99999999999999999999")

    def test_min_int64(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN -9223372036854775808")
        assert q is not None

    def test_map_literal(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN {name: 'Alice', age: 30}")
        assert q is not None

    def test_list_literal(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN [1, 2, 3]")
        assert q is not None

    def test_empty_list(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN []")
        assert q is not None

    def test_subscript_expression(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN myList[0]")
        assert q is not None

    def test_slice_expression(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN myList[1..3]")
        assert q is not None

    def test_property_access(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN n.name")
        assert q is not None

    def test_function_call(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN length(n.name)")
        assert q is not None

    def test_aggregation_count(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN count(n)")
        assert q is not None

    def test_aggregation_sum(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN sum(n.val)")
        assert q is not None

    def test_aggregation_distinct(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN count(DISTINCT n.name)")
        assert q is not None

    def test_label_predicate(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WHERE n:Person RETURN n")
        assert q is not None

    def test_conjunctive_label_predicate(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WHERE n:Person:Employee RETURN n")
        assert q is not None

    def test_comparison_chained(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WHERE 1 < n.age < 100 RETURN n")
        assert q is not None

    def test_parameter_expression(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WHERE n.id = $nodeId RETURN n")
        assert q is not None


class TestParserUpdateClauses:

    def test_create_node(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("CREATE (n:Person {name: 'Alice'})")
        assert q is not None

    def test_create_relationship(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (a), (b) CREATE (a)-[:KNOWS]->(b)")
        assert q is not None

    def test_delete_node(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) DELETE n")
        assert q is not None

    def test_detach_delete(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) DETACH DELETE n")
        assert q is not None

    def test_set_property(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) SET n.name = 'Bob'")
        assert q is not None

    def test_set_label(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) SET n:Person")
        assert q is not None

    def test_set_multiple_labels(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) SET n:Person:Employee")
        assert q is not None

    def test_set_plus_equal(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) SET n += {name: 'Bob'}")
        assert q is not None

    def test_remove_property(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) REMOVE n.name")
        assert q is not None

    def test_remove_label(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) REMOVE n:Person")
        assert q is not None

    def test_remove_multiple_labels(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) REMOVE n:Person:Employee")
        assert q is not None

    def test_merge_basic(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MERGE (n:Person {name: 'Alice'})")
        assert q is not None

    def test_merge_on_create(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MERGE (n:Person {name: 'Alice'}) ON CREATE SET n.created = 1")
        assert q is not None

    def test_merge_on_match(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MERGE (n:Person {name: 'Alice'}) ON MATCH SET n.updated = 1")
        assert q is not None

    def test_foreach_basic(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("FOREACH (x IN [1,2,3] | SET x = x)")
        assert q is not None


class TestParserAdvanced:

    def test_with_clause(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WITH n RETURN n")
        assert q is not None

    def test_with_clause_star(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WITH * RETURN n")
        assert q is not None

    def test_with_distinct(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WITH DISTINCT n.name AS name RETURN name")
        assert q is not None

    def test_with_where(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WITH n WHERE n.age > 18 RETURN n")
        assert q is not None

    def test_unwind_clause(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("UNWIND [1, 2, 3] AS x RETURN x")
        assert q is not None

    def test_call_procedure(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("CALL db.labels() YIELD label RETURN label")
        assert q is not None

    def test_call_procedure_no_parens(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("CALL db.labels YIELD label RETURN label")
        assert q is not None

    def test_call_procedure_yield_star(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("CALL db.schema.visualization() YIELD *")
        assert q is not None

    def test_call_subquery(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("CALL { MATCH (n) RETURN n } RETURN n")
        assert q is not None

    def test_union_query(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n:A) RETURN n UNION MATCH (n:B) RETURN n")
        assert q is not None

    def test_union_all_query(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n:A) RETURN n UNION ALL MATCH (n:B) RETURN n")
        assert q is not None

    def test_exists_expression(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WHERE exists { (n)-[:KNOWS]->() } RETURN n")
        assert q is not None

    def test_list_comprehension(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN [x IN [1,2,3] WHERE x > 1 | x * 2]")
        assert q is not None

    def test_list_predicate_any(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN any(x IN [1,2,3] WHERE x > 1)")
        assert q is not None

    def test_list_predicate_all(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WHERE all(x IN n.vals WHERE x > 0) RETURN n")
        assert q is not None

    def test_list_predicate_none(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN none(x IN [1,2,3] WHERE x > 5)")
        assert q is not None

    def test_list_predicate_single(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN single(x IN [1,2,3] WHERE x = 2)")
        assert q is not None

    def test_reduce_expression(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN reduce(acc = 0, x IN [1,2,3] | acc + x)")
        assert q is not None

    def test_filter_expression(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN filter(x IN [1,2,3] WHERE x > 1)")
        assert q is not None

    def test_extract_expression(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN extract(x IN [1,2,3] | x * 2)")
        assert q is not None

    def test_namespace_function(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN duration.between(date('2020-01-01'), date('2021-01-01'))")
        assert q is not None

    def test_shortestpath_function_call(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN shortestPath((a)-[*]-(b))")
        assert q is not None

    def test_allshortestpaths_function(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN allShortestPaths((a)-[*]-(b))")
        assert q is not None

    def test_use_graph_context(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("USE myGraph MATCH (n) RETURN n")
        assert q.graph_context == "myGraph"

    def test_pattern_predicate_where(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WHERE (n)-[:KNOWS]->() RETURN n")
        assert q is not None

    def test_multiple_match_clauses(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (a) MATCH (b) RETURN a, b")
        assert q is not None

    def test_map_projection(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN n {.name, .age}")
        assert q is not None

    def test_call_procedure_with_options(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("CALL apoc.algo.dijkstra(s, e, 'KNOWS', 'weight') YIELD path RETURN path")
        assert q is not None

    def test_call_subquery_in_transactions(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("CALL { MATCH (n) RETURN n } IN TRANSACTIONS RETURN n")
        assert q is not None

    def test_not_exists_pattern(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) WHERE NOT exists { (n)-[:KNOWS]->() } RETURN n")
        assert q is not None

    def test_pattern_comprehension_bracket(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN [(n)-[r]->(m) | r.weight]")
        assert q is not None

    def test_multiple_return_items(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN n.name, n.age, n.id")
        assert len(q.return_clause.items) == 3

    def test_return_alias(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN n.name AS name")
        assert q.return_clause.items[0].alias == "name"

    def test_call_yield_with_alias(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("CALL db.labels() YIELD label AS l RETURN l")
        assert q is not None

    def test_node_multi_label(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n:Person:Employee) RETURN n")
        assert q is not None

    def test_float_overflow(self):
        from iris_vector_graph.cypher.parser import parse_query, CypherParseError
        with pytest.raises(CypherParseError, match="FloatingPointOverflow"):
            parse_query("RETURN 1e999")

    def test_caret_relation_type(self):
        """Test ^ operator in expression"""
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("RETURN 2 ^ 3")
        assert q is not None

    def test_contextual_keyword_variable(self):
        from iris_vector_graph.cypher.parser import parse_query
        # 'count' used as function
        q = parse_query("MATCH (n) RETURN count(*)")
        assert q is not None

    def test_collect_aggregation(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN collect(n.name)")
        assert q is not None

    def test_min_max_aggregation(self):
        from iris_vector_graph.cypher.parser import parse_query
        q = parse_query("MATCH (n) RETURN min(n.age), max(n.age)")
        assert q is not None
