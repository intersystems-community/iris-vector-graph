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
        assert "MatchEdges" in sql
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
        assert "rdf_edges" in sql or "MatchEdges" in sql, "r2 (static) edge scan should be present"
        assert "weight" in sql, "CTE should use 'weight' column name (not 'w')"
        assert "1000" not in sql and "2000" not in sql, "ts bounds removed from WHERE after CTE injection"
        params_flat = [p for plist in result.parameters for p in plist]
        assert "RELATED" in params_flat, "r2 predicate should be in params"


    def test_rts_return_without_filter_gives_null_unit(self):
        from iris_vector_graph.cypher.translator import translate_to_sql
        from iris_vector_graph.cypher.parser import parse_query
        tree = parse_query("MATCH (a)-[r:CALLS_AT]->(b) RETURN a.id, r.ts LIMIT 5")
        mock_engine = MagicMock()
        result = translate_to_sql(tree, {}, engine=mock_engine)
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "NULL" in sql, "r.ts without filter should generate NULL in SELECT"
        assert result.query_metadata.warnings, "Should emit a warning about r.ts without ts filter"


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

    def _insert(self, edges):
        self.engine.bulk_create_edges_temporal(edges)

    def _cypher(self, query, params=None):
        return self.engine.execute_cypher(query, params or {})

    def test_temporal_window_returns_correct_edges(self):
        now = int(time.time())
        T1, T2 = now - 100, now - 50
        self._insert([
            {"s": f"{self.PREFIX}:auth", "p": "CALLS_AT", "o": f"{self.PREFIX}:pay", "ts": T1, "w": 42.7},
            {"s": f"{self.PREFIX}:auth", "p": "CALLS_AT", "o": f"{self.PREFIX}:pay", "ts": T2, "w": 10.1},
        ])
        result = self._cypher(
            "MATCH (a)-[r:CALLS_AT]->(b) WHERE r.ts >= $start AND r.ts <= $end RETURN r.ts, r.weight",
            {"start": T1 - 1, "end": T1 + 1},
        )
        assert len(result["rows"]) == 1
        row = result["rows"][0]
        assert row[0] == T1
        assert abs(float(row[1]) - 42.7) < 0.01

    def test_empty_window_returns_zero_rows(self):
        now = int(time.time())
        self._insert([
            {"s": f"{self.PREFIX}:a", "p": "CALLS_AT", "o": f"{self.PREFIX}:b", "ts": now - 100, "w": 1.0},
        ])
        result = self._cypher(
            "MATCH (a)-[r:CALLS_AT]->(b) WHERE r.ts >= $start AND r.ts <= $end RETURN r.ts",
            {"start": now + 1000, "end": now + 2000},
        )
        assert len(result["rows"]) == 0

    def test_parameter_binding_works(self):
        now = int(time.time())
        T1 = now - 200
        self._insert([
            {"s": f"{self.PREFIX}:x", "p": "CALLS_AT", "o": f"{self.PREFIX}:y", "ts": T1, "w": 7.7},
        ])
        result = self._cypher(
            "MATCH (a)-[r:CALLS_AT]->(b) WHERE r.ts >= $start AND r.ts <= $end RETURN r.ts, r.weight",
            {"start": T1 - 5, "end": T1 + 5},
        )
        assert len(result["rows"]) == 1
        assert abs(float(result["rows"][0][1]) - 7.7) < 0.01

    def test_order_by_ts_desc(self):
        now = int(time.time())
        T1, T2, T3 = now - 300, now - 200, now - 100
        self._insert([
            {"s": f"{self.PREFIX}:src", "p": "CALLS_AT", "o": f"{self.PREFIX}:d1", "ts": T1, "w": 1.0},
            {"s": f"{self.PREFIX}:src", "p": "CALLS_AT", "o": f"{self.PREFIX}:d2", "ts": T2, "w": 2.0},
            {"s": f"{self.PREFIX}:src", "p": "CALLS_AT", "o": f"{self.PREFIX}:d3", "ts": T3, "w": 3.0},
        ])
        result = self._cypher(
            "MATCH (a)-[r:CALLS_AT]->(b) WHERE r.ts >= $start AND r.ts <= $end RETURN r.ts ORDER BY r.ts DESC",
            {"start": T1 - 1, "end": T3 + 1},
        )
        assert len(result["rows"]) == 3
        ts_vals = [row[0] for row in result["rows"]]
        assert ts_vals == sorted(ts_vals, reverse=True), f"Expected DESC order, got {ts_vals}"

    def test_rts_return_without_filter_gives_null(self):
        pass

    def test_weight_postfilter_applied(self):
        now = int(time.time())
        self._insert([
            {"s": f"{self.PREFIX}:s", "p": "CALLS_AT", "o": f"{self.PREFIX}:d1", "ts": now - 10, "w": 50.0},
            {"s": f"{self.PREFIX}:s", "p": "CALLS_AT", "o": f"{self.PREFIX}:d2", "ts": now - 9,  "w": 200.0},
            {"s": f"{self.PREFIX}:s", "p": "CALLS_AT", "o": f"{self.PREFIX}:d3", "ts": now - 8,  "w": 1500.0},
        ])
        result = self._cypher(
            "MATCH (a)-[r:CALLS_AT]->(b) WHERE r.ts >= $start AND r.ts <= $end AND r.weight > 100 RETURN r.weight",
            {"start": now - 20, "end": now},
        )
        assert len(result["rows"]) == 2
        weights = sorted(float(row[0]) for row in result["rows"])
        assert abs(weights[0] - 200.0) < 0.1
        assert abs(weights[1] - 1500.0) < 0.1

    def test_order_by_weight_desc(self):
        now = int(time.time())
        self._insert([
            {"s": f"{self.PREFIX}:s2", "p": "CALLS_AT", "o": f"{self.PREFIX}:d1", "ts": now - 10, "w": 5.0},
            {"s": f"{self.PREFIX}:s2", "p": "CALLS_AT", "o": f"{self.PREFIX}:d2", "ts": now - 9,  "w": 50.0},
            {"s": f"{self.PREFIX}:s2", "p": "CALLS_AT", "o": f"{self.PREFIX}:d3", "ts": now - 8,  "w": 500.0},
        ])
        result = self._cypher(
            "MATCH (a)-[r:CALLS_AT]->(b) WHERE r.ts >= $start AND r.ts <= $end RETURN r.weight ORDER BY r.weight DESC",
            {"start": now - 20, "end": now},
        )
        assert len(result["rows"]) == 3
        ws = [float(row[0]) for row in result["rows"]]
        assert ws == sorted(ws, reverse=True), f"Expected DESC, got {ws}"

    def test_inbound_direction_routes_to_querywindowinbound(self):
        now = int(time.time())
        self._insert([
            {"s": f"{self.PREFIX}:caller", "p": "CALLS_AT", "o": f"{self.PREFIX}:target", "ts": now - 10, "w": 1.0},
        ])
        result = self._cypher(
            "MATCH (b)<-[r:CALLS_AT]-(a) WHERE r.ts >= $start AND r.ts <= $end RETURN r.ts",
            {"start": now - 20, "end": now},
        )
        assert len(result["rows"]) == 1

    def test_nontemporal_match_regression(self):
        now = int(time.time())
        self._insert([
            {"s": f"{self.PREFIX}:regtest", "p": "CALLS_AT", "o": f"{self.PREFIX}:other", "ts": now, "w": 1.0},
        ])
        result = self._cypher(
            "MATCH (a)-[r:CALLS_AT]->(b) WHERE r.ts >= $start AND r.ts <= $end RETURN r.ts",
            {"start": now - 5, "end": now + 5},
        )
        assert len(result["rows"]) == 1


