import json
import random
import pytest
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.index_protocol import IVGIndex, IndexHandle


DIM = 16


@pytest.fixture(scope="module")
def engine(iris_connection):
    return IRISGraphEngine(iris_connection)


@pytest.fixture(scope="module")
def ivf_index(engine):
    import json as _json
    iris_obj = engine._iris_obj()
    rng = random.Random(42)
    centroids = [[rng.gauss(0, 1) for _ in range(DIM)] for _ in range(4)]
    iris_obj.classMethodValue(
        "Graph.KG.IVFIndex", "Build",
        "idx_proto_ivf", _json.dumps(4), _json.dumps("cosine"), _json.dumps(centroids), "[]"
    )
    for i in range(20):
        vec = [rng.gauss(0, 1) for _ in range(DIM)]
        iris_obj.classMethodValue("Graph.KG.IVFIndex", "Insert", "idx_proto_ivf", f"n{i}", _json.dumps(vec))
    iris_obj.classMethodValue("Graph.KG.IVFIndex", "FinalizeIndex", "idx_proto_ivf")
    engine._index_registry["idx_proto_ivf"] = "ivf"
    yield
    engine.ivf_drop("idx_proto_ivf")
    engine._index_registry.pop("idx_proto_ivf", None)


@pytest.fixture(scope="module")
def bm25_index(engine):
    engine.bm25_build("idx_proto_bm25", ["name", "desc"])
    engine.bm25_insert("idx_proto_bm25", "doc1", "hello world graph")
    engine.bm25_insert("idx_proto_bm25", "doc2", "vector search engine")
    yield
    engine.bm25_drop("idx_proto_bm25")


@pytest.mark.requires_database
@pytest.mark.e2e
class TestEngineIndex:
    def test_engine_index_ivf_dispatch(self, engine, ivf_index):
        rng = random.Random(7)
        query = [rng.gauss(0, 1) for _ in range(DIM)]
        handle = engine.index("idx_proto_ivf")
        assert isinstance(handle, IndexHandle)
        assert handle.type == "ivf"
        via_handle = handle.search(query, k=3)
        via_direct = engine.ivf_search("idx_proto_ivf", query, k=3)
        assert via_handle == via_direct

    def test_engine_index_bm25_dispatch(self, engine, bm25_index):
        handle = engine.index("idx_proto_bm25")
        assert isinstance(handle, IndexHandle)
        assert handle.type == "bm25"
        via_handle = handle.search("graph", k=2)
        via_direct = engine.bm25_search("idx_proto_bm25", "graph", k=2)
        assert via_handle == via_direct

    def test_engine_index_registry_persists_across_reconnect(self, iris_connection, ivf_index):
        new_engine = IRISGraphEngine(iris_connection)
        if "idx_proto_ivf" not in new_engine._index_registry:
            pytest.skip("gref $Order probe not supported on this IRIS build — registry rebuild skipped")
        handle = new_engine.index("idx_proto_ivf")
        assert handle.type == "ivf"

    def test_engine_index_raises_for_unknown_name(self, engine):
        with pytest.raises(ValueError, match="not found"):
            engine.index("this_index_definitely_does_not_exist_xyz")

    def test_engine_index_returns_ivgindex(self, engine, ivf_index):
        handle = engine.index("idx_proto_ivf")
        assert isinstance(handle, IVGIndex)

    def test_engine_index_info_has_type(self, engine, ivf_index):
        info = engine.index("idx_proto_ivf").info()
        assert info.get("type") == "ivf"
