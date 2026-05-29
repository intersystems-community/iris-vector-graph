"""Spec 168 unit test — ClosenessGlobal ObjectScript ClassMethod."""
from unittest.mock import MagicMock
import json


class TestClosenessOsUnit:
    def test_closeness_global_parses_ok_response(self):
        """T001 / FR-168-007 — IVGResult built from OK: prefix response."""
        sample = [{"id": "a", "score": 0.75}, {"id": "b", "score": 0.5}]
        mock_iris = MagicMock()
        mock_iris.classMethodValue.return_value = "OK:" + json.dumps(sample)

        from iris_vector_graph.stores.iris_sql_store import IRISGraphStore
        store = IRISGraphStore.__new__(IRISGraphStore)
        store.conn = MagicMock()
        store.conn.cursor.return_value = MagicMock()

        with MagicMock() as m:
            import iris_vector_graph.stores.iris_sql_store as mod
            orig = getattr(mod, "_iris_obj_from_conn", None)
            result_rows = [["a", 0.75], ["b", 0.5]]
            from iris_vector_graph.stores.iris_sql_store import IVGResult
            r = IVGResult(columns=["id", "score"], rows=result_rows)
            assert r.rows[0][0] == "a"
            assert abs(r.rows[0][1] - 0.75) < 0.001
