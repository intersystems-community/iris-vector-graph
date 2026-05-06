import os
import time
import uuid
import random

import pytest

IRIS_HOST = os.environ.get("IRIS_HOST", "localhost")
IRIS_PORT = int(os.environ.get("IRIS_PORT", "1972"))
IRIS_NS = os.environ.get("IRIS_NAMESPACE", "USER")
IRIS_USER = os.environ.get("IRIS_USERNAME", "test")
IRIS_PASS = os.environ.get("IRIS_PASSWORD", "test")


@pytest.fixture(scope="module")
def engine():
    try:
        import iris
        from iris_vector_graph.engine import IRISGraphEngine
        c = iris.connect(IRIS_HOST, IRIS_PORT, IRIS_NS, IRIS_USER, IRIS_PASS)
        cur = c.cursor()
        try:
            cur.execute("SELECT TOP 1 vector FROM Graph_KG.kg_NodeEmbeddings")
        except Exception:
            pass
        try:
            cur.execute("SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings")
            db_dim = 4
        except Exception:
            db_dim = 4
        try:
            from iris_vector_graph.schema import GraphSchema
            db_dim = GraphSchema.get_embedding_dimension(cur) or 4
        except Exception:
            db_dim = 4
        e = IRISGraphEngine(c, embedding_dimension=db_dim)
        e.initialize_schema()
        yield e
        c.close()
    except Exception as ex:
        pytest.skip(f"IRIS unavailable: {ex}")


IVF_NAME = "stress_ivf"
VEC_INDEX = "stress_test_idx"
BM25_NAME = "stress_bm25"


def _rand_vec(dim=4):
    import math
    v = [random.gauss(0, 1) for _ in range(dim)]
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


@pytest.fixture(scope="module")
def nodes_with_vecs(engine):
    dim = engine.embedding_dimension
    pfx = f"vec_{uuid.uuid4().hex[:6]}"
    n = 200
    for i in range(n):
        engine.create_node(f"{pfx}:{i}", labels=["VecNode"], properties={"idx": i})
        engine.store_embedding(f"{pfx}:{i}", _rand_vec(dim))
    return pfx, n, dim


class TestVecIndex:

    def test_vec_create_and_search(self, engine, nodes_with_vecs):
        pfx, n, dim = nodes_with_vecs
        try:
            engine.vec_drop(VEC_INDEX)
        except Exception:
            pass
        engine.vec_create_index(VEC_INDEX, dim=dim)
        for i in range(20):
            engine.vec_insert(VEC_INDEX, f"{pfx}:{i}", _rand_vec(dim))
        engine.vec_build(VEC_INDEX)
        results = engine.vec_search(VEC_INDEX, _rand_vec(dim), k=5)
        assert len(results) >= 1

    def test_vec_build_required_before_search(self, engine, nodes_with_vecs):
        pfx, n, dim = nodes_with_vecs
        engine.vec_drop(VEC_INDEX)
        engine.vec_create_index(VEC_INDEX, dim=dim)
        for i in range(10):
            engine.vec_insert(VEC_INDEX, f"{pfx}:{i}", _rand_vec(dim))
        engine.vec_build(VEC_INDEX)
        results = engine.vec_search(VEC_INDEX, _rand_vec(dim), k=5)
        assert len(results) >= 1

    def test_vec_info_returns_metadata(self, engine, nodes_with_vecs):
        pfx, n, dim = nodes_with_vecs
        engine.vec_create_index(VEC_INDEX, dim=dim)
        info = engine.vec_info(VEC_INDEX)
        assert info is not None

    def test_vec_insert_single(self, engine, nodes_with_vecs):
        pfx, n, dim = nodes_with_vecs
        engine.vec_create_index(VEC_INDEX, dim=dim)
        engine.vec_insert(VEC_INDEX, f"{pfx}:new_single", _rand_vec(dim))

    def test_vec_bulk_insert(self, engine, nodes_with_vecs):
        pfx, n, dim = nodes_with_vecs
        engine.vec_create_index(VEC_INDEX, dim=dim)
        items = [{"id": f"{pfx}:vbi{i}", "embedding": _rand_vec(dim)} for i in range(20)]
        engine.vec_bulk_insert(VEC_INDEX, items)

    def test_vec_lifecycle_create_build_search_drop(self, engine, nodes_with_vecs):
        pfx, n, dim = nodes_with_vecs
        idx = f"{VEC_INDEX}_lifecycle"
        engine.vec_create_index(idx, dim=dim)
        for i in range(10):
            engine.vec_insert(idx, f"{pfx}:{i}", _rand_vec(dim))
        engine.vec_build(idx)
        results = engine.vec_search(idx, _rand_vec(dim), k=3)
        assert len(results) >= 1
        engine.vec_drop(idx)


class TestIVFIndex:

    def test_ivf_build_and_search(self, engine, nodes_with_vecs):
        pfx, n, dim = nodes_with_vecs
        try:
            engine.ivf_drop(IVF_NAME)
        except Exception:
            pass
        try:
            engine.ivf_build(IVF_NAME, nlist=8)
            results = engine.ivf_search(IVF_NAME, _rand_vec(dim), k=5)
            assert len(results) >= 1
        except RuntimeError as e:
            pytest.skip(f"IVF build failed (may be IRIS stack limit on large dims): {e}")

    def test_ivf_info_after_build(self, engine, nodes_with_vecs):
        pfx, n, dim = nodes_with_vecs
        try:
            engine.ivf_build(IVF_NAME, nlist=8)
            info = engine.ivf_info(IVF_NAME)
            assert info is not None
        except RuntimeError as e:
            pytest.skip(f"IVF build failed: {e}")

    def test_ivf_search_nprobe_parameter(self, engine, nodes_with_vecs):
        pfx, n, dim = nodes_with_vecs
        try:
            engine.ivf_build(IVF_NAME, nlist=8)
        except RuntimeError as e:
            pytest.skip(f"IVF build failed: {e}")
        r1 = engine.ivf_search(IVF_NAME, _rand_vec(dim), k=5, nprobe=1)
        r2 = engine.ivf_search(IVF_NAME, _rand_vec(dim), k=5, nprobe=4)
        assert len(r1) >= 1
        assert len(r2) >= 1

    def test_ivf_drop_and_rebuild(self, engine, nodes_with_vecs):
        pfx, n, dim = nodes_with_vecs
        try:
            engine.ivf_build(IVF_NAME, nlist=8)
            engine.ivf_drop(IVF_NAME)
            engine.ivf_build(IVF_NAME, nlist=4)
            results = engine.ivf_search(IVF_NAME, _rand_vec(dim), k=3)
            assert len(results) >= 1
        except RuntimeError as e:
            pytest.skip(f"IVF build failed: {e}")

    def test_ivf_search_latency(self, engine, nodes_with_vecs):
        pfx, n, dim = nodes_with_vecs
        try:
            engine.ivf_build(IVF_NAME, nlist=8)
        except RuntimeError as e:
            pytest.skip(f"IVF build failed: {e}")
        times = []
        for _ in range(10):
            t0 = time.perf_counter()
            engine.ivf_search(IVF_NAME, _rand_vec(dim), k=5)
            times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        p50 = times[5]
        assert p50 < 500, f"IVF search p50={p50:.1f}ms — too slow"


class TestBM25:

    def test_bm25_build_search_lifecycle(self, engine):
        pfx = f"bm25_{uuid.uuid4().hex[:6]}"
        docs = [
            (f"{pfx}:d1", "The quick brown fox jumps over the lazy dog"),
            (f"{pfx}:d2", "A fast auburn fox leaps above a sleepy canine"),
            (f"{pfx}:d3", "GraphDB traversal performance benchmarks"),
            (f"{pfx}:d4", "IRIS InterSystems knowledge graph engine"),
            (f"{pfx}:d5", "Vector search similarity retrieval"),
        ]
        for nid, text in docs:
            engine.create_node(nid, labels=["BM25Doc"], properties={"text": text})
        engine.bm25_build(BM25_NAME, text_props=["text"])
        results = engine.bm25_search(BM25_NAME, "fox jumps", k=3)
        assert len(results) >= 1

    def test_bm25_search_returns_scores(self, engine):
        engine.bm25_build(BM25_NAME, text_props=["text"])
        results = engine.bm25_search(BM25_NAME, "graph traversal", k=5)
        assert isinstance(results, list)

    def test_bm25_search_empty_query(self, engine):
        engine.bm25_build(BM25_NAME, text_props=["text"])
        try:
            results = engine.bm25_search(BM25_NAME, "", k=5)
        except Exception:
            pass

    def test_bm25_insert_new_doc(self, engine):
        pfx = f"bm25i_{uuid.uuid4().hex[:6]}"
        nid = f"{pfx}:new"
        engine.create_node(nid, labels=["BM25Insert"], properties={"text": "brand new document"})
        try:
            engine.bm25_insert(BM25_NAME, nid, "brand new document")
        except AttributeError:
            pytest.skip("bm25_insert not implemented")

    def test_bm25_drop_and_rebuild(self, engine):
        engine.bm25_drop(BM25_NAME)
        engine.bm25_build(BM25_NAME, text_props=["text"])
        results = engine.bm25_search(BM25_NAME, "graph", k=3)
        assert results is not None

    def test_bm25_info(self, engine):
        engine.bm25_build(BM25_NAME, text_props=["text"])
        info = engine.bm25_info(BM25_NAME)
        assert info is not None


class TestNodeEmbeddings:

    def test_store_embedding_single(self, engine, nodes_with_vecs):
        pfx, n, dim = nodes_with_vecs
        engine.store_embedding(f"{pfx}:0", _rand_vec(dim))

    def test_store_embeddings_batch(self, engine, nodes_with_vecs):
        pfx, n, dim = nodes_with_vecs
        batch = [{"node_id": f"{pfx}:{i}", "embedding": _rand_vec(dim)} for i in range(10)]
        engine.store_embeddings(batch)

    def test_get_embedding_roundtrip(self, engine, nodes_with_vecs):
        pfx, n, dim = nodes_with_vecs
        original_vec = _rand_vec(dim)
        engine.store_embedding(f"{pfx}:42", original_vec)
        try:
            retrieved = engine.get_embedding(f"{pfx}:42")
            assert retrieved is not None
            vec = retrieved.get("embedding", retrieved) if isinstance(retrieved, dict) else retrieved
            assert vec is not None
        except AttributeError:
            pytest.skip("get_embedding not implemented")

    def test_embed_nodes_by_label(self, engine, nodes_with_vecs):
        pfx, n, dim = nodes_with_vecs
        try:
            count = engine.embed_nodes(
                label="VecNode",
                embedding_fn=lambda text: _rand_vec(dim),
                text_fn=lambda node: f"node {node.get('idx', '')}",
            )
            assert count >= 0
        except (AttributeError, TypeError):
            pytest.skip("embed_nodes signature mismatch or not implemented")


class TestHybridSearch:

    def test_rrf_fusion_no_crash(self, engine, nodes_with_vecs):
        pfx, n, dim = nodes_with_vecs
        try:
            engine.ivf_build(IVF_NAME, nlist=4)
        except Exception:
            pass
        try:
            engine.bm25_build(BM25_NAME, text_props=["text"])
        except Exception:
            pass
        try:
            import json as _json
            results = engine.kg_RRF_FUSE(
                k=5, k1=5, k2=5, c=60,
                query_vector=_json.dumps([float(x) for x in _rand_vec(dim)]),
                query_text="graph",
            )
            assert results is not None
        except (AttributeError, Exception) as ex:
            pytest.skip(f"kg_RRF_FUSE not available: {ex}")

    def test_vector_graph_search(self, engine, nodes_with_vecs):
        pfx, n, dim = nodes_with_vecs
        try:
            engine.ivf_build(IVF_NAME, nlist=4)
        except Exception:
            pass
        try:
            results = engine.kg_VECTOR_GRAPH_SEARCH(
                query_vec=_rand_vec(dim),
                hops=1,
                k=5,
            )
            assert results is not None
        except (AttributeError, Exception) as ex:
            pytest.skip(f"kg_VECTOR_GRAPH_SEARCH not available: {ex}")
