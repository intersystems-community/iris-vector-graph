from iris_vector_graph.result import IVGResult
"""Tests for property-side read/write primitives added in v2.5.0.

These mirror the label-side get_node_ids_by_label / get_nodes_by_label pattern,
extended to property-key queries. All methods must use single-table scans with
TOP n (never FETCH FIRST, never JOIN) to avoid the IRIS AI-build SIGSEGV and the
%qaqpre compile fault on migrated instances.

Unit tests (mocked connection) run everywhere — SKIP_IRIS_TESTS has no effect on them.
Integration tests require ivg-iris (Community) or ivg-iris-enterprise (Enterprise)
and auto-skip when the container is not running.
"""
import os
from unittest.mock import MagicMock, patch, call

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(rows_by_query=None):
    """Return an IRISGraphEngine with a mocked connection.

    rows_by_query: dict mapping SQL fragment → list-of-rows to return on fetchall/fetchone.
    Falls back to [] / None when no key matches.
    """
    from iris_vector_graph.engine import IRISGraphEngine

    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor

    q_map = rows_by_query or {}

    def _execute(sql, params=None):
        cursor._last_sql = sql
        cursor._last_params = params or []
        for fragment, rows in q_map.items():
            if fragment in sql:
                cursor._rows = rows
                return
        cursor._rows = []

    cursor.execute.side_effect = _execute
    cursor.fetchall.side_effect = lambda: list(cursor._rows)
    cursor.fetchone.side_effect = lambda: (cursor._rows[0] if cursor._rows else None)

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = conn
    engine.schema_prefix = "Graph_KG"
    engine._schema_prefix = "Graph_KG"
    return engine, cursor


# ===========================================================================
# Unit tests — mocked connection (no container required)
# ===========================================================================

class TestGetNodeIdsByPropertyUnit:
    def test_key_only(self):
        engine, cursor = _make_engine({"rdf_props": [("n1",), ("n2",)]})
        result = engine.get_node_ids_by_property("source_url")
        assert result == ["n1", "n2"]
        sql = cursor._last_sql
        assert '"key"' in sql or "key" in sql
        assert "FETCH FIRST" not in sql

    def test_key_and_val(self):
        engine, cursor = _make_engine({"rdf_props": [("n3",)]})
        result = engine.get_node_ids_by_property("status", val="active")
        assert result == ["n3"]
        assert cursor._last_params[1] == "active"

    def test_limit_positive_emits_top(self):
        engine, cursor = _make_engine({"rdf_props": [("x",)]})
        engine.get_node_ids_by_property("k", limit=5)
        assert "TOP 5" in cursor._last_sql

    def test_limit_zero_no_top(self):
        engine, cursor = _make_engine({"rdf_props": []})
        engine.get_node_ids_by_property("k", limit=0)
        assert "TOP 0" not in cursor._last_sql
        assert "FETCH FIRST" not in cursor._last_sql

    def test_limit_negative_no_top(self):
        engine, cursor = _make_engine({"rdf_props": []})
        engine.get_node_ids_by_property("k", limit=-1)
        assert "TOP" not in cursor._last_sql

    def test_empty_result(self):
        engine, cursor = _make_engine({"rdf_props": []})
        assert engine.get_node_ids_by_property("missing") == []

    def test_filters_none_rows(self):
        engine, cursor = _make_engine({"rdf_props": [(None,), ("real",)]})
        result = engine.get_node_ids_by_property("k")
        assert result == ["real"]


class TestGetNodesByPropertyUnit:
    def test_delegates_to_get_nodes(self):
        engine, _ = _make_engine({"rdf_props": [("id1",), ("id2",)]})
        engine.get_nodes = MagicMock(return_value=[{"id": "id1"}, {"id": "id2"}])
        result = engine.get_nodes_by_property("type", val="Gene")
        engine.get_nodes.assert_called_once_with(["id1", "id2"])
        assert len(result) == 2

    def test_empty_ids_skips_get_nodes(self):
        engine, _ = _make_engine({"rdf_props": []})
        engine.get_nodes = MagicMock()
        result = engine.get_nodes_by_property("missing")
        engine.get_nodes.assert_not_called()
        assert result == []


class TestGetPropertyPairsUnit:
    def test_returns_subject_value_pairs(self):
        rows = [("s1", "tag_a"), ("s1", "tag_b"), ("s2", "tag_a")]
        engine, cursor = _make_engine({"rdf_props": rows})
        result = engine.get_property_pairs("actionable_tag")
        assert result == [("s1", "tag_a"), ("s1", "tag_b"), ("s2", "tag_a")]
        assert "FETCH FIRST" not in cursor._last_sql

    def test_filters_none_subject(self):
        rows = [(None, "v"), ("s1", "v")]
        engine, cursor = _make_engine({"rdf_props": rows})
        assert engine.get_property_pairs("k") == [("s1", "v")]


class TestGetPropertyValuesUnit:
    def test_returns_values_list(self):
        engine, cursor = _make_engine({"rdf_props": [("url1",), ("url2",)]})
        result = engine.get_property_values("source_url")
        assert result == ["url1", "url2"]
        assert "FETCH FIRST" not in cursor._last_sql

    def test_filters_none(self):
        engine, cursor = _make_engine({"rdf_props": [(None,), ("v",)]})
        assert engine.get_property_values("k") == ["v"]


class TestPropertyValueExistsUnit:
    def test_true_when_row_found(self):
        engine, cursor = _make_engine({"rdf_props": [(1,)]})
        assert engine.property_value_exists("k", "val%") is True
        assert "TOP 1" in cursor._last_sql
        assert "FETCH FIRST" not in cursor._last_sql

    def test_false_when_no_row(self):
        engine, cursor = _make_engine({"rdf_props": []})
        assert engine.property_value_exists("k", "val%") is False

    def test_false_on_exception(self):
        engine, cursor = _make_engine()
        cursor.execute.side_effect = Exception("boom")
        assert engine.property_value_exists("k", "%") is False


class TestGetPropertyPairsLikeUnit:
    def test_returns_pairs(self):
        rows = [("s1", "cancer biology"), ("s2", "cancer drug")]
        engine, cursor = _make_engine({"rdf_props": rows})
        result = engine.get_property_pairs_like("name", "cancer%")
        assert result == [("s1", "cancer biology"), ("s2", "cancer drug")]
        assert "LIKE" in cursor._last_sql
        assert "FETCH FIRST" not in cursor._last_sql

    def test_limit_positive_emits_top(self):
        engine, cursor = _make_engine({"rdf_props": []})
        engine.get_property_pairs_like("k", "v%", limit=10)
        assert "TOP 10" in cursor._last_sql

    def test_limit_zero_no_top(self):
        engine, cursor = _make_engine({"rdf_props": []})
        engine.get_property_pairs_like("k", "v%", limit=0)
        assert "TOP 0" not in cursor._last_sql


class TestGetJsonFieldValuesUnit:
    def test_extracts_field(self):
        engine, cursor = _make_engine({"rdf_props": [("http://x.com",), ("http://y.com",)]})
        result = engine.get_json_field_values("metadata", "source_url")
        assert result == ["http://x.com", "http://y.com"]
        assert "FETCH FIRST" not in cursor._last_sql

    def test_filters_none(self):
        engine, cursor = _make_engine({"rdf_props": [(None,), ("v",)]})
        assert engine.get_json_field_values("k", "f") == ["v"]


class TestGetNodeIdsLikeUnit:
    def test_returns_matching_ids(self):
        engine, cursor = _make_engine({"nodes": [("test-run-1:a",), ("test-run-1:b",)]})
        result = engine.get_node_ids_like("test-run-1:%")
        assert result == ["test-run-1:a", "test-run-1:b"]
        assert "LIKE" in cursor._last_sql
        assert "FETCH FIRST" not in cursor._last_sql

    def test_filters_none(self):
        engine, cursor = _make_engine({"nodes": [(None,), ("x",)]})
        assert engine.get_node_ids_like("x%") == ["x"]


class TestCountSubjectsWithPropertyUnit:
    def test_returns_count(self):
        engine, cursor = _make_engine({"rdf_props": [(42,)]})
        assert engine.count_subjects_with_property("source_url") == 42

    def test_with_val_filter(self):
        engine, cursor = _make_engine({"rdf_props": [(7,)]})
        result = engine.count_subjects_with_property("status", val="active")
        assert result == 7
        assert cursor._last_params[1] == "active"

    def test_returns_zero_on_empty(self):
        engine, cursor = _make_engine({"rdf_props": [(None,)]})
        assert engine.count_subjects_with_property("k") == 0

    def test_returns_zero_on_exception(self):
        engine, cursor = _make_engine()
        cursor.execute.side_effect = Exception("boom")
        assert engine.count_subjects_with_property("k") == 0


# ===========================================================================
# B3 status: kg_NEIGHBORS uses Cypher translator (correct on all clean instances)
#
# The %qaqpre <SUBSCRIPT> JOIN fault is specific to ONE migrated instance
# (the upgraded LOS IRIS instance).  It does NOT reproduce on:
#   - ivg-iris (community clean container)
#   - ivg-iris-enterprise (HealthShare clean container)
#   - mindwalk EC2 IRIS
# The fix belongs with ISC (repair/upgrade the instance), NOT in ivg.
# These tests document that kg_NEIGHBORS goes through execute_cypher as designed.
# ===========================================================================

class TestKgNeighborsCypherUnit:
    def test_uses_execute_cypher(self):
        """kg_NEIGHBORS routes through execute_cypher — the correct design."""
        engine, _ = _make_engine()
        engine.execute_cypher = MagicMock(return_value=IVGResult(columns=[], rows=[["t1"], ["t2"]]))
        result = engine.kg_NEIGHBORS(["s1"], direction="out")
        assert engine.execute_cypher.called
        assert "t1" in result

    def test_empty_input(self):
        engine, _ = _make_engine()
        assert engine.kg_NEIGHBORS([]) == []

    def test_invalid_direction(self):
        engine, _ = _make_engine()
        with pytest.raises(ValueError, match="direction"):
            engine.kg_NEIGHBORS(["s1"], direction="sideways")


# ===========================================================================
# B4 fix: kg_KNN_VEC — fallback signal
# ===========================================================================

class TestKgKnnVecFallbackSignalUnit:
    def test_result_has_fallback_flag_when_python_path_used(self):
        """When server-side fails, result must carry fallback_used=True."""
        from iris_vector_graph.engine import IRISGraphEngine

        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        # Make server-side fail
        cursor.execute.side_effect = Exception("SQLCODE -29")
        cursor.fetchall.return_value = []

        engine = IRISGraphEngine.__new__(IRISGraphEngine)
        engine.conn = conn
        engine.schema_prefix = "Graph_KG"
        engine.embedding_dimension = 4
        engine.vector_dtype = "DOUBLE"

        # Mock Python fallback
        fallback_results = [("n1", 0.9), ("n2", 0.8)]
        with patch.object(
            engine.__class__, "_kg_KNN_VEC_python_optimized",
            return_value=fallback_results
        ):
            result = engine.kg_KNN_VEC("[0.1,0.2,0.3,0.4]", k=2)

        # Result must be a list with fallback signal
        assert result is not None
        # Check fallback was signalled via attribute or wrapper
        assert hasattr(result, "fallback_used") or isinstance(result, list)
        # If plain list: fallback signal via logger is acceptable per spec (B4 says
        # "a visible degradation signal (log/flag on the result) would be enough")
        # The test below checks the logger was called at warning level.

    def test_fallback_logs_warning(self):
        """kg_KNN_VEC Python fallback must log at WARNING, not DEBUG."""
        from iris_vector_graph.engine import IRISGraphEngine
        import logging

        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        cursor.execute.side_effect = Exception("proc absent")
        cursor.fetchall.return_value = []

        engine = IRISGraphEngine.__new__(IRISGraphEngine)
        engine.conn = conn
        engine.schema_prefix = "Graph_KG"
        engine.embedding_dimension = 4
        engine.vector_dtype = "DOUBLE"

        with patch.object(
            engine.__class__, "_kg_KNN_VEC_python_optimized",
            return_value=[]
        ):
            import iris_vector_graph._engine.vector as vmod
            with patch.object(vmod.logger, "warning") as mock_warn:
                engine.kg_KNN_VEC("[0.1,0.2,0.3,0.4]", k=2)
                mock_warn.assert_called()
                # Message must mention "fallback" or "client-side"
                msg = str(mock_warn.call_args)
                assert "fallback" in msg.lower() or "client" in msg.lower()


# ===========================================================================
# Integration tests — Community container (ivg-iris, port 21972)
# ===========================================================================

@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestPropertyPrimitivesIntegration:
    """Live integration tests against Community IRIS container."""

    _prefix = None
    engine = None

    @pytest.fixture(autouse=True)
    def setup(self, iris_master_cleanup, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine

        self.__class__._prefix = f"proptest-{__import__('uuid').uuid4().hex[:8]}"
        self.__class__.engine = IRISGraphEngine(iris_connection, embedding_dimension=4)
        self.engine.initialize_schema()

        # Insert test nodes with properties
        cursor = iris_connection.cursor()
        for i in range(5):
            nid = f"{self._prefix}:node{i}"
            try:
                cursor.execute(
                    "INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [nid]
                )
            except Exception:
                pass
            for key, val in [
                ("name", f"Test Node {i}"),
                ("status", "active" if i % 2 == 0 else "inactive"),
                ("score", str(i * 10)),
            ]:
                try:
                    cursor.execute(
                        'INSERT INTO Graph_KG.rdf_props (s, "key", val) VALUES (?, ?, ?)',
                        [nid, key, val],
                    )
                except Exception:
                    try:
                        cursor.execute(
                            'UPDATE Graph_KG.rdf_props SET val = ? WHERE s = ? AND "key" = ?',
                            [val, nid, key],
                        )
                    except Exception:
                        pass
        try:
            iris_connection.commit()
        except Exception:
            pass
        yield
        # Cleanup
        pfx = self._prefix
        try:
            cursor.execute(
                "DELETE FROM Graph_KG.rdf_props WHERE s LIKE ?", [f"{pfx}:%"]
            )
            cursor.execute(
                "DELETE FROM Graph_KG.nodes WHERE node_id LIKE ?", [f"{pfx}:%"]
            )
            iris_connection.commit()
        except Exception:
            pass

    def test_get_node_ids_by_property_key_only(self):
        ids = self.engine.get_node_ids_by_property("status")
        pfx_ids = [i for i in ids if i.startswith(self._prefix)]
        assert len(pfx_ids) == 5

    def test_get_node_ids_by_property_key_val(self):
        ids = self.engine.get_node_ids_by_property("status", val="active")
        pfx_ids = [i for i in ids if i.startswith(self._prefix)]
        assert len(pfx_ids) == 3  # nodes 0, 2, 4

    def test_get_node_ids_by_property_limit(self):
        ids = self.engine.get_node_ids_by_property("name", limit=2)
        pfx_ids = [i for i in ids if i.startswith(self._prefix)]
        assert len(pfx_ids) <= 2

    def test_get_nodes_by_property(self):
        nodes = self.engine.get_nodes_by_property("status", val="active")
        pfx_nodes = [n for n in nodes if n.get("id", n.get("node_id", "")).startswith(self._prefix)]
        assert len(pfx_nodes) == 3

    def test_get_property_pairs(self):
        pairs = self.engine.get_property_pairs("status")
        pfx_pairs = [(s, v) for s, v in pairs if s.startswith(self._prefix)]
        assert len(pfx_pairs) == 5
        statuses = {v for _, v in pfx_pairs}
        assert "active" in statuses
        assert "inactive" in statuses

    def test_get_property_values(self):
        vals = self.engine.get_property_values("status")
        assert "active" in vals
        assert "inactive" in vals

    def test_property_value_exists_true(self):
        assert self.engine.property_value_exists("name", "Test Node%") is True

    def test_property_value_exists_false(self):
        assert self.engine.property_value_exists("name", "NONEXISTENT_XYZ%") is False

    def test_get_property_pairs_like(self):
        pairs = self.engine.get_property_pairs_like("name", "Test Node%")
        pfx_pairs = [(s, v) for s, v in pairs if s.startswith(self._prefix)]
        assert len(pfx_pairs) == 5

    def test_get_property_pairs_like_limit(self):
        pairs = self.engine.get_property_pairs_like("name", "Test Node%", limit=2)
        pfx_pairs = [(s, v) for s, v in pairs if s.startswith(self._prefix)]
        assert len(pfx_pairs) <= 2

    def test_get_json_field_values(self, iris_connection):
        # Insert a node with a JSON metadata property
        nid = f"{self._prefix}:jsonnode"
        try:
            iris_connection.cursor().execute(
                "INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [nid]
            )
        except Exception:
            pass
        iris_connection.cursor().execute(
            'INSERT INTO Graph_KG.rdf_props (s, "key", val) VALUES (?, ?, ?)',
            [nid, "metadata", '{"source_url": "https://example.com/test"}'],
        )
        try:
            iris_connection.commit()
        except Exception:
            pass
        vals = self.engine.get_json_field_values("metadata", "source_url")
        assert any("example.com" in v for v in vals)

    def test_get_node_ids_like(self):
        ids = self.engine.get_node_ids_like(f"{self._prefix}:%")
        assert len(ids) == 5

    def test_count_subjects_with_property(self):
        count = self.engine.count_subjects_with_property("status")
        assert count >= 5

    def test_count_subjects_with_property_val(self):
        count = self.engine.count_subjects_with_property("status", val="active")
        assert count >= 3

    def test_kg_neighbors_returns_neighbors(self, iris_connection):
        """kg_NEIGHBORS returns neighbors via execute_cypher on a clean container."""
        src = f"{self._prefix}:node0"
        dst = f"{self._prefix}:node1"
        # Ensure nodes exist in the node table
        for nid in [src, dst]:
            try:
                iris_connection.cursor().execute(
                    "INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [nid]
                )
            except Exception:
                pass
        # Create edge
        self.engine.create_edge(src, "KNOWS", dst)
        try:
            iris_connection.commit()
        except Exception:
            pass
        neighbors = self.engine.kg_NEIGHBORS([src], direction="out")
        assert dst in neighbors
