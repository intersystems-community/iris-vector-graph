"""Unit tests for spec 222 — now_ts parameter documentation and find_burst_nodes fix."""
import pytest
from unittest.mock import MagicMock, patch


class TestNowTsParams:

    def _make_engine(self):
        from iris_vector_graph.engine import IRISGraphEngine
        eng = MagicMock(spec=IRISGraphEngine)
        eng._schema_prefix = "Graph_KG"
        eng.conn = MagicMock()
        return eng

    def test_get_edge_velocity_passes_now_ts(self):
        """T001: get_edge_velocity(now_ts=T) passes T to ObjectScript."""
        from iris_vector_graph._engine.temporal import TemporalMixin

        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "3"
        mixin = MagicMock()
        mixin._iris_obj.return_value = iris_obj

        TemporalMixin.get_edge_velocity(mixin, "src-node", window_seconds=300, now_ts=12345)

        call_args = iris_obj.classMethodValue.call_args[0]
        assert call_args[0] == "Graph.KG.TemporalIndex"
        assert call_args[1] == "GetVelocity"
        assert call_args[4] == 12345

    def test_find_burst_nodes_accepts_now_ts(self):
        """T002: find_burst_nodes(now_ts=T) passes T to FindBursts ObjectScript."""
        from iris_vector_graph._engine.temporal import TemporalMixin

        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "[]"
        mixin = MagicMock()
        mixin._iris_obj.return_value = iris_obj

        TemporalMixin.find_burst_nodes(mixin, predicate="METRIC", now_ts=99999)

        call_args = iris_obj.classMethodValue.call_args[0]
        assert call_args[0] == "Graph.KG.TemporalIndex"
        assert call_args[1] == "FindBursts"
        assert 99999 in call_args, f"now_ts=99999 not passed; args={call_args}"

    def test_get_edge_velocity_docstring_documents_now_ts(self):
        """T001 extra: get_edge_velocity docstring mentions now_ts."""
        from iris_vector_graph._engine.temporal import TemporalMixin
        doc = TemporalMixin.get_edge_velocity.__doc__ or ""
        assert "now_ts" in doc, "get_edge_velocity docstring should document now_ts"

    def test_find_burst_nodes_docstring_documents_now_ts(self):
        """T002 extra: find_burst_nodes docstring mentions now_ts."""
        from iris_vector_graph._engine.temporal import TemporalMixin
        doc = TemporalMixin.find_burst_nodes.__doc__ or ""
        assert "now_ts" in doc, "find_burst_nodes docstring should document now_ts"
