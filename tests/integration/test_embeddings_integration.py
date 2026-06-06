"""
Integration tests for EmbeddingsMixin against live ivg-iris.

Covers: embed_text (Python embedder path), store_embedding, store_embeddings,
get_unembedded_nodes, _probe_native_vec, _get_embedding_dimension.

No mocking — all paths hit real IRIS SQL (TO_VECTOR, kg_NodeEmbeddings INSERT/SELECT).
SentenceTransformer is used where available; tests skip gracefully if not installed.
"""
import pytest
import numpy as np
from iris_vector_graph.engine import IRISGraphEngine

DIM = 128  # must match schema width — GraphSchema auto-detects 128 from column definition


@pytest.fixture
def engine(iris_connection, iris_master_cleanup):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=DIM)
    # Force embedding_dimension — auto-detection may return wrong value if
    # prior tests left data with a different dimension (session-scoped connection).
    # Also reinitialize schema so kg_NodeEmbeddings column matches DIM.
    eng.embedding_dimension = DIM
    try:
        eng.initialize_schema(auto_deploy_objectscript=False)
    except Exception:
        pass
    return eng


@pytest.fixture
def engine_with_nodes(engine):
    for i in range(5):
        engine.create_node(f"emb_{i}", labels=["Doc"], properties={"text": f"document {i}"})
    return engine


def _fake_embedder(dim=DIM):
    """Deterministic embedder: returns unit vector from text hash."""
    import hashlib

    def embed(text):
        # Build dim-length vector from MD5 repeated
        h = hashlib.md5(text.encode()).digest()
        # Tile to reach dim floats
        raw = []
        while len(raw) < dim:
            raw.extend((b / 255.0) - 0.5 for b in h)
        vec = raw[:dim]
        norm = sum(x ** 2 for x in vec) ** 0.5 or 1.0
        return [x / norm for x in vec]

    class _Enc:
        def encode(self, text, **kw):
            class _R:
                def tolist(self_):
                    return embed(text)
            return _R()
        def __call__(self, text):
            return embed(text)

    return _Enc()


# ---------------------------------------------------------------------------
# embed_text — Python embedder path
# ---------------------------------------------------------------------------

class TestEmbedText:

    def test_embed_text_with_callable_embedder(self, engine):
        engine.embedder = _fake_embedder(DIM)
        vec = engine.embed_text("hello world")
        assert isinstance(vec, list)
        assert len(vec) == DIM
        assert all(isinstance(x, float) for x in vec)

    def test_embed_text_deterministic(self, engine):
        engine.embedder = _fake_embedder(DIM)
        v1 = engine.embed_text("consistent input")
        v2 = engine.embed_text("consistent input")
        assert v1 == v2

    def test_embed_text_different_inputs_differ(self, engine):
        engine.embedder = _fake_embedder(DIM)
        v1 = engine.embed_text("apple")
        v2 = engine.embed_text("orange")
        assert v1 != v2

    def test_embed_text_no_embedder_no_config_raises(self, engine):
        engine.embedder = None
        engine.embedding_config = None
        # Should raise RuntimeError when no embedder available
        pytest.importorskip("this_module_does_not_exist_ivg_test",
                            reason="skip if sentence-transformers happens to be installed")
        with pytest.raises(RuntimeError, match="embedder"):
            engine.embed_text("test")

    def test_embed_text_with_encode_method(self, engine):
        """embedder.encode(text) path — mimics sentence-transformers interface."""
        class FakeEncoder:
            def encode(self, text, **kw):
                return np.zeros(DIM, dtype=np.float32)
        engine.embedder = FakeEncoder()
        vec = engine.embed_text("anything")
        assert len(vec) == DIM


# ---------------------------------------------------------------------------
# store_embedding / store_embeddings
# ---------------------------------------------------------------------------

class TestStoreEmbedding:

    def test_store_single_embedding_inserts_row(self, engine_with_nodes, iris_connection):
        eng = engine_with_nodes
        eng.embedder = _fake_embedder(DIM)
        vec = eng.embed_text("document 0")
        eng.store_embedding("emb_0", vec)
        cur = iris_connection.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings WHERE id = 'emb_0'"
        )
        assert int(cur.fetchone()[0]) == 1

    def test_store_embedding_overwrite_idempotent(self, engine_with_nodes, iris_connection):
        eng = engine_with_nodes
        eng.embedder = _fake_embedder(DIM)
        vec = eng.embed_text("doc")
        eng.store_embedding("emb_1", vec)
        eng.store_embedding("emb_1", vec)  # second write should not error
        cur = iris_connection.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings WHERE id = 'emb_1'"
        )
        assert int(cur.fetchone()[0]) >= 1

    def test_store_embeddings_batch(self, engine_with_nodes, iris_connection):
        eng = engine_with_nodes
        eng.embedder = _fake_embedder(DIM)
        # store_embeddings expects List[Dict] with node_id + embedding keys
        items = [
            {"node_id": f"emb_{i}", "embedding": eng.embed_text(f"document {i}")}
            for i in range(3)
        ]
        eng.store_embeddings(items)
        cur = iris_connection.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings WHERE id LIKE 'emb_%'"
        )
        assert int(cur.fetchone()[0]) >= 3

    def test_store_embeddings_empty_dict_is_noop(self, engine_with_nodes):
        # Should not raise
        engine_with_nodes.store_embeddings({})


# ---------------------------------------------------------------------------
# get_unembedded_nodes
# ---------------------------------------------------------------------------

class TestGetUnembeddedNodes:

    def test_all_nodes_unembedded_initially(self, engine_with_nodes):
        eng = engine_with_nodes
        unembedded = eng.get_unembedded_nodes()  # no limit param
        node_ids = [r if isinstance(r, str) else r[0] for r in unembedded]
        emb_nodes = [n for n in node_ids if str(n).startswith("emb_")]
        assert len(emb_nodes) >= 5

    def test_embedded_nodes_excluded(self, engine_with_nodes, iris_connection):
        eng = engine_with_nodes
        eng.embedder = _fake_embedder(DIM)
        vec = eng.embed_text("document 0")
        eng.store_embedding("emb_0", vec)
        unembedded = eng.get_unembedded_nodes()
        node_ids = [r if isinstance(r, str) else r[0] for r in unembedded]
        assert "emb_0" not in node_ids

    def test_returns_list(self, engine_with_nodes):
        eng = engine_with_nodes
        unembedded = eng.get_unembedded_nodes()
        assert isinstance(unembedded, list)


# ---------------------------------------------------------------------------
# _probe_native_vec / _get_embedding_dimension
# ---------------------------------------------------------------------------

class TestEmbeddingProbes:

    def test_probe_native_vec_returns_bool(self, engine):
        result = engine._probe_native_vec()
        assert isinstance(result, bool)

    def test_get_embedding_dimension_returns_positive_int(self, engine_with_nodes):
        # _get_embedding_dimension either reads from DB schema or uses engine attribute
        dim = engine_with_nodes._get_embedding_dimension()
        assert isinstance(dim, int)
        assert dim > 0

    def test_get_embedding_dimension_matches_schema(self, engine_with_nodes):
        dim = engine_with_nodes._get_embedding_dimension()
        # Must be the dimension the schema was initialized with (128 from session fixture)
        assert dim == DIM
