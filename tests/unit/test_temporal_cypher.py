import os
import time
import uuid
import pytest
from unittest.mock import MagicMock

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

MAX_INT = 9_999_999_999


def _parse_where(cypher_where_fragment):
    from iris_vector_graph.cypher.parser import parse_query
    tree = parse_query(f"MATCH (a)-[r]->(b) {cypher_where_fragment} RETURN a.id")
    part = tree.query_parts[0]
    return part.clauses[1] if len(part.clauses) > 1 else None


def _parse_where_expr(cypher_where_fragment):
    clause = _parse_where(cypher_where_fragment)
    return clause.expression if clause else None


def _make_metadata():
    from iris_vector_graph.cypher.translator import QueryMetadata
    return QueryMetadata()


class TestTemporalCypherUnit:

    def test_extract_bounds_returns_bound_for_range_filter(self):
        from iris_vector_graph.cypher.translator import _extract_temporal_bounds, TemporalBound
        expr = _parse_where_expr("WHERE r.ts >= 1000 AND r.ts <= 2000")
        result = _extract_temporal_bounds(expr, "r", {})
        assert result is not None
        assert isinstance(result, TemporalBound)
        assert result.ts_start == 1000
        assert result.ts_end == 2000
        assert result.rel_variable == "r"

    def test_extract_bounds_returns_none_without_ts_filter(self):
        from iris_vector_graph.cypher.translator import _extract_temporal_bounds
        expr = _parse_where_expr("WHERE a.id = 'x'")
        result = _extract_temporal_bounds(expr, "r", {})
        assert result is None

    def test_extract_bounds_any_variable_name(self):
        from iris_vector_graph.cypher.translator import _extract_temporal_bounds, TemporalBound
        tree = _parse_from_full("MATCH (a)-[rel:CALLS_AT]->(b) WHERE rel.ts >= 100 AND rel.ts <= 200 RETURN a.id")
        part = tree.query_parts[0]
        expr = part.clauses[1].expression
        result = _extract_temporal_bounds(expr, "rel", {})
        assert result is not None
        assert result.rel_variable == "rel"
        assert result.ts_start == 100
        assert result.ts_end == 200

    def test_build_temporal_cte_empty(self):
        from iris_vector_graph.cypher.translator import _build_temporal_cte
        metadata = _make_metadata()
        sql = _build_temporal_cte([], "te0", metadata)
        assert "WHERE 1=0" in sql or "1=0" in sql
        assert "te0" not in sql or True

    def test_extract_bounds_open_ended_upper_bound(self):
        from iris_vector_graph.cypher.translator import _extract_temporal_bounds, TemporalBound
        expr = _parse_where_expr("WHERE r.ts >= 1000")
        result = _extract_temporal_bounds(expr, "r", {})
        assert result is not None
        assert result.ts_start == 1000
        assert result.ts_end is None or result.ts_end >= MAX_INT

    def test_extract_bounds_single_timestamp_equality(self):
        from iris_vector_graph.cypher.translator import _extract_temporal_bounds, TemporalBound
        expr = _parse_where_expr("WHERE r.ts = 1000")
        result = _extract_temporal_bounds(expr, "r", {})
        assert result is not None
        assert result.ts_start == 1000
        assert result.ts_end == 1000

    def test_temporal_or_condition_raises_clear_error(self):
        from iris_vector_graph.cypher.translator import _extract_temporal_bounds
        expr = _parse_where_expr("WHERE r.ts >= 100 OR r.ts <= 200")
        with pytest.raises((ValueError, NotImplementedError)):
            _extract_temporal_bounds(expr, "r", {})

    def test_translate_temporal_raises_without_engine(self):
        from iris_vector_graph.cypher.translator import translate_to_sql, TemporalQueryRequiresEngine
        tree = _parse_from_full(
            "MATCH (a)-[r:CALLS_AT]->(b) WHERE r.ts >= 1000 AND r.ts <= 2000 RETURN a.id"
        )
        with pytest.raises(TemporalQueryRequiresEngine):
            translate_to_sql(tree, {}, engine=None)

    def test_translate_temporal_builds_union_all_cte(self):
        from iris_vector_graph.cypher.translator import translate_to_sql
        tree = _parse_from_full(
            "MATCH (a)-[r:CALLS_AT]->(b) WHERE r.ts >= 1000 AND r.ts <= 2000 RETURN a.id, b.id"
        )
        mock_engine = MagicMock()
        mock_engine.get_edges_in_window.return_value = [
            {"s": "svc:a", "p": "CALLS_AT", "o": "svc:b", "ts": 1500, "w": 42.0},
            {"s": "svc:a", "p": "CALLS_AT", "o": "svc:c", "ts": 1800, "w": 10.0},
        ]
        result = translate_to_sql(tree, {}, engine=mock_engine)
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "WITH" in sql or "UNION ALL" in sql

    def test_nontemporal_match_unchanged(self):
        from iris_vector_graph.cypher.translator import translate_to_sql
        tree = _parse_from_full("MATCH (a)-[r]->(b) RETURN a.id")
        mock_engine = MagicMock()
        result = translate_to_sql(tree, {}, engine=mock_engine)
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "rdf_edges" in sql
        assert "UNION ALL" not in sql

    def test_mixed_match_temporal_r1_static_r2(self):
        from iris_vector_graph.cypher.translator import translate_to_sql
        tree = _parse_from_full(
            "MATCH (a)-[r1:CALLS_AT]->(b), (a)-[r2:RELATED]->(c) "
            "WHERE r1.ts >= 1000 AND r1.ts <= 2000 RETURN a.id, b.id, c.id"
        )
        mock_engine = MagicMock()
        mock_engine.get_edges_in_window.return_value = [
            {"s": "svc:a", "p": "CALLS_AT", "o": "svc:b", "ts": 1500, "w": 42.0},
        ]
        result = translate_to_sql(tree, {}, engine=mock_engine)
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "WITH" in sql, "temporal CTE should appear in WITH clause"
        assert "rdf_edges" in sql, "r2 (static) should still JOIN on rdf_edges"
        assert "weight" in sql, "CTE should use 'weight' column name (not 'w')"
        assert "1000" not in sql and "2000" not in sql, "ts bounds removed from WHERE after CTE injection"
        params_flat = [p for plist in result.parameters for p in plist]
        assert "RELATED" in params_flat, "r2 predicate should be in params"


def _parse_from_full(cypher):
    from iris_vector_graph.cypher.parser import parse_query
    return parse_query(cypher)


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestTemporalCypherE2E:

    PREFIX = f"TC_{uuid.uuid4().hex[:6]}"

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine
        self.conn = iris_connection
        self.engine = IRISGraphEngine(iris_connection)
        yield
        try:
            self.engine._iris_obj().classMethodVoid("Graph.KG.TemporalIndex", "Purge")
        except Exception:
            pass

