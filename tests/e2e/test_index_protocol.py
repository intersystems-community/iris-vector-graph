import json
import random
import pytest
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.index_protocol import IVGIndex, IndexHandle


DIM = 16
EMBED_DIM = 768


@pytest.fixture(scope="module")
def engine(iris_connection):
    return IRISGraphEngine(iris_connection)


@pytest.fixture(scope="module")
def engine_with_embeddings(iris_connection):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=EMBED_DIM)
    return eng


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


@pytest.fixture(scope="module")
def hnsw_data(engine_with_embeddings):
    eng = engine_with_embeddings
    rng = random.Random(55)
    cur = eng.conn.cursor()
    nodes = [f"hnsw_e2e_{i}" for i in range(10)]
    for nid in nodes:
        cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id=?", [nid])
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [nid])
    eng.conn.commit()
    for nid in nodes:
        vec = [rng.gauss(0, 1) for _ in range(EMBED_DIM)]
        eng.store_embedding(nid, vec)
    if not eng._probe_native_vec():
        pytest.skip("Native HNSW (kg_KNN_VEC) not available on this IRIS tier")
    yield nodes
    cur.execute("DELETE FROM Graph_KG.kg_NodeEmbeddings WHERE id LIKE 'hnsw_e2e_%'")
    for nid in nodes:
        cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id=?", [nid])
    eng.conn.commit()


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


@pytest.mark.requires_database
@pytest.mark.e2e
class TestEngineIndexHNSW:
    def test_hnsw_in_registry_when_native_vec_available(self, engine_with_embeddings):
        eng = engine_with_embeddings
        if not eng._probe_native_vec():
            pytest.skip("Native HNSW not available on this IRIS tier")
        assert "hnsw" in eng._index_registry
        assert eng._index_registry["hnsw"] == "hnsw"

    def test_hnsw_handle_type(self, engine_with_embeddings):
        eng = engine_with_embeddings
        if not eng._probe_native_vec():
            pytest.skip("Native HNSW not available on this IRIS tier")
        handle = eng.index("hnsw")
        assert isinstance(handle, IndexHandle)
        assert isinstance(handle, IVGIndex)
        assert handle.type == "hnsw"

    def test_hnsw_info_returns_type(self, engine_with_embeddings):
        eng = engine_with_embeddings
        if not eng._probe_native_vec():
            pytest.skip("Native HNSW not available on this IRIS tier")
        info = eng.index("hnsw").info()
        assert info["type"] == "hnsw"
        assert info["available"] is True

    def test_hnsw_insert_stores_embedding(self, engine_with_embeddings, hnsw_data):
        eng = engine_with_embeddings
        rng = random.Random(99)
        new_id = "hnsw_e2e_new"
        vec = [rng.gauss(0, 1) for _ in range(EMBED_DIM)]
        cur = eng.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id=?", [new_id])
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [new_id])
            eng.conn.commit()
        eng.index("hnsw").insert(new_id, vec)
        cur.execute("SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings WHERE id=?", [new_id])
        assert cur.fetchone()[0] == 1
        cur.execute("DELETE FROM Graph_KG.kg_NodeEmbeddings WHERE id=?", [new_id])
        cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id=?", [new_id])
        eng.conn.commit()

    def test_hnsw_search_returns_results(self, engine_with_embeddings, hnsw_data):
        eng = engine_with_embeddings
        rng = random.Random(77)
        query = [rng.gauss(0, 1) for _ in range(EMBED_DIM)]
        results = eng.index("hnsw").search(query, k=5)
        assert isinstance(results, list)
        assert len(results) > 0
        assert len(results) <= 5

    def test_hnsw_search_matches_search_nodes_by_vector(self, engine_with_embeddings, hnsw_data):
        eng = engine_with_embeddings
        rng = random.Random(88)
        query = [rng.gauss(0, 1) for _ in range(EMBED_DIM)]
        via_handle = eng.index("hnsw").search(query, k=5)
        via_direct = eng.search_nodes_by_vector(query, k=5)
        assert len(via_handle) == len(via_direct)

    def test_hnsw_not_in_registry_when_native_vec_unavailable(self):
        from unittest.mock import patch, MagicMock
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.execute.side_effect = Exception("no VECTOR")
        mock_conn.cursor.return_value.fetchone.return_value = None
        from iris_vector_graph.engine import IRISGraphEngine
        eng = object.__new__(IRISGraphEngine)
        eng.conn = mock_conn
        eng._arno_available = None
        eng._arno_capabilities = {}
        eng._nkg_dirty = False
        eng._connection_params = None
        eng._table_mapping_cache = None
        eng._rel_mapping_cache = None
        eng._native_vec_available = None
        with patch.object(eng, "_probe_native_vec", return_value=False):
            registry = eng._build_index_registry()
        assert "hnsw" not in registry

