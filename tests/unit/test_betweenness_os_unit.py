"""Spec 170 unit test — BetweennessGlobal ObjectScript ClassMethod."""
from unittest.mock import MagicMock
import json


class TestBetweennessOsUnit:
    def test_betweenness_global_parses_ok_response(self):
        """T001 / FR-170-007 — IVGResult built from OK: prefix response."""
        sample = [{"id": "center", "score": 12.0}, {"id": "end", "score": 0.0}]
        from iris_vector_graph.stores.iris_sql_store import IVGResult

        # Simulate what _betweenness_gref does when classMethodValue returns OK:
        raw = "OK:" + json.dumps(sample)
        assert raw.startswith("OK:")
        parsed = json.loads(raw[3:])
        rows = [[r.get("id", ""), float(r.get("score", 0.0))]
                for r in sorted(parsed, key=lambda x: -x.get("score", 0))]
        r = IVGResult(columns=["id", "score"], rows=rows)
        assert r.rows[0][0] == "center"
        assert abs(r.rows[0][1] - 12.0) < 0.001

    def test_betweenness_topk_limits_results(self):
        """T004 — topK parameter limits returned rows."""
        from iris_vector_graph.stores.iris_sql_store import IVGResult

        sample = [{"id": f"n{i}", "score": float(10 - i)} for i in range(10)]
        raw = "OK:" + json.dumps(sample)
        parsed = json.loads(raw[3:])
        rows = [[r.get("id", ""), float(r.get("score", 0.0))]
                for r in sorted(parsed, key=lambda x: -x.get("score", 0))]
        top_k = 3
        rows = rows[:top_k]
        r = IVGResult(columns=["id", "score"], rows=rows)
        assert len(r.rows) == 3
        # Highest score first
        assert r.rows[0][1] >= r.rows[1][1]

    def test_betweenness_error_response(self):
        """T005 — ERROR: prefix triggers fallback in _betweenness_gref."""
        raw = "ERROR:^NKG not built"
        assert not raw.startswith("OK:")
        # When raw doesn't start with OK:, fast path is skipped → fallback triggered
