import math
import pytest
from pydantic import ValidationError

from iris_vector_graph._validate import (
    NodeIdInput, EdgeInput, CypherInput,
    IVFBuildInput, VectorSearchInput,
    BM25BuildInput, BM25SearchInput,
    KHop2Input, TemporalEdgeInput, VecSearchInput,
)


class TestNodeIdInput:
    def test_valid(self):
        assert NodeIdInput(node_id="gene:BRCA1").node_id == "gene:BRCA1"

    def test_empty_raises(self):
        with pytest.raises(ValidationError):
            NodeIdInput(node_id="")

    def test_whitespace_raises(self):
        with pytest.raises(ValidationError):
            NodeIdInput(node_id="   ")


class TestEdgeInput:
    def test_valid(self):
        EdgeInput(source_id="a", predicate="R", target_id="b")

    @pytest.mark.parametrize("field,val", [
        ("source_id", ""),
        ("predicate", ""),
        ("target_id", ""),
    ])
    def test_empty_raises(self, field, val):
        kwargs = {"source_id": "a", "predicate": "R", "target_id": "b"}
        kwargs[field] = val
        with pytest.raises(ValidationError):
            EdgeInput(**kwargs)


class TestCypherInput:
    def test_valid(self):
        CypherInput(cypher_query="MATCH (n) RETURN n")

    def test_empty_raises(self):
        with pytest.raises(ValidationError):
            CypherInput(cypher_query="")


class TestIVFBuildInput:
    def test_valid(self):
        IVFBuildInput(name="idx", nlist=8, metric="cosine")

    def test_nlist_zero_raises(self):
        with pytest.raises(ValidationError):
            IVFBuildInput(name="idx", nlist=0)

    def test_nlist_negative_raises(self):
        with pytest.raises(ValidationError):
            IVFBuildInput(name="idx", nlist=-1)

    def test_bad_metric_raises(self):
        with pytest.raises(ValidationError):
            IVFBuildInput(name="idx", nlist=8, metric="manhattan")

    def test_all_valid_metrics(self):
        for m in ("cosine", "dot", "euclidean", "l2"):
            IVFBuildInput(name="idx", nlist=8, metric=m)

    def test_empty_name_raises(self):
        with pytest.raises(ValidationError):
            IVFBuildInput(name="", nlist=8)

    def test_batch_size_zero_raises(self):
        with pytest.raises(ValidationError):
            IVFBuildInput(name="idx", nlist=8, batch_size=0)

    def test_build_batch_size_zero_raises(self):
        with pytest.raises(ValidationError):
            IVFBuildInput(name="idx", nlist=8, build_batch_size=0)


class TestVectorSearchInput:
    def test_valid(self):
        VectorSearchInput(name="idx", query=[0.1, 0.2, 0.3], k=5)

    def test_empty_query_raises(self):
        with pytest.raises(ValidationError):
            VectorSearchInput(name="idx", query=[], k=5)

    def test_nan_raises(self):
        with pytest.raises(ValidationError):
            VectorSearchInput(name="idx", query=[float("nan"), 0.1], k=5)

    def test_inf_raises(self):
        with pytest.raises(ValidationError):
            VectorSearchInput(name="idx", query=[float("inf"), 0.1], k=5)

    def test_k_zero_raises(self):
        with pytest.raises(ValidationError):
            VectorSearchInput(name="idx", query=[0.1], k=0)

    def test_k_negative_raises(self):
        with pytest.raises(ValidationError):
            VectorSearchInput(name="idx", query=[0.1], k=-1)

    def test_nprobe_zero_raises(self):
        with pytest.raises(ValidationError):
            VectorSearchInput(name="idx", query=[0.1], k=5, nprobe=0)


class TestBM25BuildInput:
    def test_valid(self):
        BM25BuildInput(name="idx", text_props=["name", "desc"])

    def test_empty_name_raises(self):
        with pytest.raises(ValidationError):
            BM25BuildInput(name="", text_props=["name"])

    def test_empty_props_raises(self):
        with pytest.raises(ValidationError):
            BM25BuildInput(name="idx", text_props=[])

    def test_b_out_of_range_raises(self):
        with pytest.raises(ValidationError):
            BM25BuildInput(name="idx", text_props=["name"], b=1.5)

    def test_k1_negative_raises(self):
        with pytest.raises(ValidationError):
            BM25BuildInput(name="idx", text_props=["name"], k1=-0.1)


class TestBM25SearchInput:
    def test_valid(self):
        BM25SearchInput(name="idx", query="cancer gene", k=10)

    def test_empty_name_raises(self):
        with pytest.raises(ValidationError):
            BM25SearchInput(name="", query="cancer", k=5)

    def test_empty_query_raises(self):
        with pytest.raises(ValidationError):
            BM25SearchInput(name="idx", query="", k=5)

    def test_k_zero_raises(self):
        with pytest.raises(ValidationError):
            BM25SearchInput(name="idx", query="cancer", k=0)


class TestKHop2Input:
    def test_valid(self):
        KHop2Input(node_id="p_123")

    def test_empty_raises(self):
        with pytest.raises(ValidationError):
            KHop2Input(node_id="")


class TestTemporalEdgeInput:
    def test_valid(self):
        TemporalEdgeInput(source="a", predicate="R", target="b", timestamp=1000)

    def test_negative_timestamp_raises(self):
        with pytest.raises(ValidationError):
            TemporalEdgeInput(source="a", predicate="R", target="b", timestamp=-1)

    def test_empty_source_raises(self):
        with pytest.raises(ValidationError):
            TemporalEdgeInput(source="", predicate="R", target="b", timestamp=100)

    def test_negative_weight_raises(self):
        with pytest.raises(ValidationError):
            TemporalEdgeInput(source="a", predicate="R", target="b",
                              timestamp=100, weight=-0.1)

    def test_zero_weight_ok(self):
        TemporalEdgeInput(source="a", predicate="R", target="b",
                          timestamp=100, weight=0.0)


class TestEngineValidationIntegration:
    def test_execute_cypher_empty_raises(self):
        from unittest.mock import MagicMock
        from iris_vector_graph.engine import IRISGraphEngine
        from pydantic import ValidationError

        engine = object.__new__(IRISGraphEngine)
        engine.conn = MagicMock()
        engine._arno_available = None
        engine._arno_capabilities = {}
        engine._nkg_dirty = False
        engine._index_registry = {}

        with pytest.raises(ValidationError, match="cypher_query"):
            engine.execute_cypher("")

    def test_create_node_empty_raises(self):
        from unittest.mock import MagicMock
        from iris_vector_graph.engine import IRISGraphEngine

        engine = object.__new__(IRISGraphEngine)
        engine.conn = MagicMock()

        with pytest.raises(ValidationError, match="node_id"):
            engine.create_node("")

    def test_create_edge_empty_predicate_raises(self):
        from unittest.mock import MagicMock
        from iris_vector_graph.engine import IRISGraphEngine

        engine = object.__new__(IRISGraphEngine)
        engine.conn = MagicMock()

        with pytest.raises(ValidationError, match="predicate"):
            engine.create_edge("a", "", "b")

    def test_khop2_count_fast_empty_raises(self):
        from unittest.mock import MagicMock
        from iris_vector_graph.engine import IRISGraphEngine

        engine = object.__new__(IRISGraphEngine)
        engine.conn = MagicMock()
        engine._arno_available = None
        engine._arno_capabilities = {}

        with pytest.raises(ValidationError, match="node_id"):
            engine.khop2_count_fast("")
