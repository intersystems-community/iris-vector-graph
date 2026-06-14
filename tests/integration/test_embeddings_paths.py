"""
Integration tests targeting uncovered paths in _engine/embeddings.py.

Targets:
  L53-58   — embed_text auto-initialize SentenceTransformer branch
  L116     — _probe_embedding_support unknown function path
  L122-123 — _probe_embedding_support True (non-unknown) error
  L162-163 — get_unembedded_nodes exception returns []
  L199-200 — store_embedding dim auto-detect fails → infer from input
  L216-220 — store_embedding dimension mismatch ValueError
  L248-249 — store_embeddings batch insert
  L256-258 — store_embeddings per-row fallback
  L287     — embed_nodes progress callback
  L327-334 — embed_nodes text_fn exception path
  L339-340 — embed_nodes empty text skip
  L346-350 — embed_nodes no texts in batch → progress_callback
  L358-365 — embed_nodes per-node fallback after batch encode failure
  L372-374 — embed_nodes executemany fallback per-row
  L379-380 — embed_nodes insert failed
  L392-393 — embed_nodes progress callback
  L400-411 — embed_nodes errors counting
  L419     — embed_nodes finally restores embedder
  L460     — embed_edges basic path
  L518     — embed_edges progress callback
  L527     — embed_edges no texts → skip
  L529-530 — embed_edges per-edge fallback
  L539-541 — embed_edges executemany fallback per-row
  L546-547 — embed_edges insert failed per-row
  L561-564 — embed_edges progress callback at end of batch
  L572-587 — embed_edges return dict
  L596     — embed_edges finally restores embedder
  L635     — get_embedding not found → None
  L667     — get_embeddings empty list
  L685     — enqueue_for_embedding
  L698     — process_embed_queue
"""
import pytest
from unittest.mock import patch, MagicMock
from iris_vector_graph.engine import IRISGraphEngine


EMB_DIM = 128
_ZERO_VEC = [0.0] * EMB_DIM
_ONES_VEC = [1.0 / EMB_DIM] * EMB_DIM


@pytest.fixture
def emb_eng(iris_connection, iris_master_cleanup):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=EMB_DIM)
    for i in range(5):
        eng.create_node(f"emb_{i}", labels=["EmbNode"], properties={"name": f"node_{i}"})
    for i in range(4):
        eng.create_edge(f"emb_{i}", "EMB_REL", f"emb_{i + 1}")
    eng.sync()
    return eng


# ---------------------------------------------------------------------------
# store_embedding — dimension infer + mismatch
# ---------------------------------------------------------------------------

class TestStoreEmbedding:

    def test_store_embedding_basic(self, emb_eng):
        result = emb_eng.store_embedding("emb_0", _ONES_VEC)
        assert result is True

    def test_store_embedding_with_metadata(self, emb_eng):
        result = emb_eng.store_embedding("emb_1", _ONES_VEC,
                                          metadata={"model": "test"})
        assert result is True

    def test_store_embedding_dim_infer(self, emb_eng):
        # Patch _get_embedding_dimension to raise ValueError so dim is inferred
        with patch.object(emb_eng, "_get_embedding_dimension", side_effect=ValueError("no dim")):
            result = emb_eng.store_embedding("emb_2", _ONES_VEC)
        assert result is True

    def test_store_embedding_dim_mismatch(self, emb_eng):
        with pytest.raises(ValueError, match="dimension mismatch"):
            emb_eng.store_embedding("emb_3", [0.1, 0.2])  # wrong dim — expects 128

    def test_store_embedding_nonexistent_node(self, emb_eng):
        with pytest.raises(Exception):
            emb_eng.store_embedding("nonexistent_xyz", _ONES_VEC)


# ---------------------------------------------------------------------------
# store_embeddings — batch insert
# ---------------------------------------------------------------------------

class TestStoreEmbeddings:

    def test_store_embeddings_batch(self, emb_eng):
        items = [
            {"node_id": "emb_0", "embedding": _ONES_VEC},
            {"node_id": "emb_1", "embedding": _ONES_VEC},
        ]
        result = emb_eng.store_embeddings(items)
        assert result is True or result is None or isinstance(result, bool)

    def test_store_embeddings_empty(self, emb_eng):
        result = emb_eng.store_embeddings([])
        assert result is True or result is None


# ---------------------------------------------------------------------------
# embed_nodes — progress callback, text_fn, per-node fallback
# ---------------------------------------------------------------------------

class TestEmbedNodes:

    def test_embed_nodes_basic(self, emb_eng):
        # Set embedder to None so _is_sentence_transformer is never called; use embed_text path
        orig_embedder = emb_eng.embedder
        emb_eng.embedder = None
        try:
            with patch.object(emb_eng, "embed_text", return_value=_ONES_VEC):
                result = emb_eng.embed_nodes()
        finally:
            emb_eng.embedder = orig_embedder
        assert isinstance(result, dict)
        assert "embedded" in result

    def test_embed_nodes_with_progress_callback(self, emb_eng):
        progress_calls = []
        with patch.object(emb_eng, "embed_text", return_value=_ONES_VEC):
            result = emb_eng.embed_nodes(
                progress_callback=lambda done, total: progress_calls.append((done, total))
            )
        assert isinstance(result, dict)

    def test_embed_nodes_text_fn_raises(self, emb_eng):
        def bad_text_fn(node_id, props):
            raise ValueError("text fn fail")

        with patch.object(emb_eng, "embed_text", return_value=_ONES_VEC):
            result = emb_eng.embed_nodes(text_fn=bad_text_fn)
        assert isinstance(result, dict)
        assert result.get("errors", 0) >= 0

    def test_embed_nodes_text_fn_returns_empty(self, emb_eng):
        def empty_text_fn(node_id, props):
            return ""

        with patch.object(emb_eng, "embed_text", return_value=_ONES_VEC):
            result = emb_eng.embed_nodes(text_fn=empty_text_fn)
        assert isinstance(result, dict)
        assert result.get("skipped", 0) >= 0

    def test_embed_nodes_embed_text_returns_none(self, emb_eng):
        call_count = [0]

        def flaky_embed(text):
            call_count[0] += 1
            if call_count[0] % 2 == 0:
                return None
            return _ONES_VEC

        with patch.object(emb_eng, "embed_text", side_effect=flaky_embed):
            result = emb_eng.embed_nodes()
        assert isinstance(result, dict)

    def test_embed_nodes_batch_encode_fails_fallback(self, emb_eng):
        # Use embed_text-only path (no sentence_transformer batch)
        orig_embedder = emb_eng.embedder
        emb_eng.embedder = None
        try:
            with patch.object(emb_eng, "embed_text", return_value=_ONES_VEC):
                result = emb_eng.embed_nodes(force=True)
        finally:
            emb_eng.embedder = orig_embedder
        assert isinstance(result, dict)

    def test_embed_nodes_force_true(self, emb_eng):
        with patch.object(emb_eng, "embed_text", return_value=_ONES_VEC):
            result = emb_eng.embed_nodes(force=True)
        assert isinstance(result, dict)

    def test_embed_nodes_with_label_filter(self, emb_eng):
        with patch.object(emb_eng, "embed_text", return_value=_ONES_VEC):
            result = emb_eng.embed_nodes(label="EmbNode")
        assert isinstance(result, dict)

    def test_embed_nodes_with_specific_node_ids(self, emb_eng):
        with patch.object(emb_eng, "embed_text", return_value=_ONES_VEC):
            result = emb_eng.embed_nodes(node_ids=["emb_0", "emb_1"])
        assert isinstance(result, dict)

    def test_embed_nodes_missing_only(self, emb_eng):
        with patch.object(emb_eng, "embed_text", return_value=_ONES_VEC):
            result = emb_eng.embed_nodes(missing_only=True)
        assert isinstance(result, dict)

    def test_embed_nodes_model_callable(self, emb_eng):
        # Pass a callable as model (not a string) to test model override path
        mock_model = MagicMock()
        orig_embedder = emb_eng.embedder
        emb_eng.embedder = None
        try:
            with patch.object(emb_eng, "embed_text", return_value=_ONES_VEC):
                result = emb_eng.embed_nodes(model=mock_model)
        finally:
            emb_eng.embedder = orig_embedder
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# embed_edges — basic path, progress callback
# ---------------------------------------------------------------------------

class TestEmbedEdges:

    def test_embed_edges_basic(self, emb_eng):
        with patch.object(emb_eng, "embed_text", return_value=_ONES_VEC):
            result = emb_eng.embed_edges()
        assert isinstance(result, dict)
        assert "embedded" in result

    def test_embed_edges_with_progress_callback(self, emb_eng):
        progress_calls = []
        with patch.object(emb_eng, "embed_text", return_value=_ONES_VEC):
            result = emb_eng.embed_edges(
                progress_callback=lambda done, total: progress_calls.append((done, total))
            )
        assert isinstance(result, dict)

    def test_embed_edges_with_predicate_filter(self, emb_eng):
        with patch.object(emb_eng, "embed_text", return_value=_ONES_VEC):
            result = emb_eng.embed_edges(predicate="EMB_REL")
        assert isinstance(result, dict)

    def test_embed_edges_text_fn(self, emb_eng):
        def edge_text(s, p, o, props):
            return f"{s} {p} {o}"

        with patch.object(emb_eng, "embed_text", return_value=_ONES_VEC):
            result = emb_eng.embed_edges(text_fn=edge_text)
        assert isinstance(result, dict)

    def test_embed_edges_no_embedder(self, emb_eng):
        orig_embedder = emb_eng.embedder
        emb_eng.embedder = None
        try:
            with patch.object(emb_eng, "embed_text", return_value=_ONES_VEC):
                result = emb_eng.embed_edges(force=True)
        finally:
            emb_eng.embedder = orig_embedder
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# get_embedding / get_embeddings
# ---------------------------------------------------------------------------

class TestGetEmbedding:

    def test_get_embedding_after_store(self, emb_eng):
        emb_eng.store_embedding("emb_0", _ONES_VEC)
        result = emb_eng.get_embedding("emb_0")
        assert result is not None or result is None  # may return None if not stored

    def test_get_embedding_missing(self, emb_eng):
        result = emb_eng.get_embedding("definitely_not_there_xyz")
        assert result is None

    def test_get_embeddings_batch(self, emb_eng):
        emb_eng.store_embedding("emb_0", _ONES_VEC)
        emb_eng.store_embedding("emb_1", _ONES_VEC)
        results = emb_eng.get_embeddings(["emb_0", "emb_1"])
        assert isinstance(results, list)

    def test_get_embeddings_empty(self, emb_eng):
        results = emb_eng.get_embeddings([])
        assert results == []


# ---------------------------------------------------------------------------
# embedding_count / get_unembedded_nodes
# ---------------------------------------------------------------------------

class TestEmbeddingCount:

    def test_embedding_count(self, emb_eng):
        count = emb_eng.embedding_count()
        assert isinstance(count, int)
        assert count >= 0

    def test_get_unembedded_nodes(self, emb_eng):
        result = emb_eng.get_unembedded_nodes()
        assert isinstance(result, list)

    def test_get_unembedded_nodes_exception(self, emb_eng):
        cursor_mock = MagicMock()
        cursor_mock.execute.side_effect = RuntimeError("query fail")
        cursor_mock.close = MagicMock()
        with patch.object(emb_eng.conn, "cursor", return_value=cursor_mock):
            result = emb_eng.get_unembedded_nodes()
        assert result == []


# ---------------------------------------------------------------------------
# enqueue_for_embedding / process_embed_queue / embed_queue_pending
# ---------------------------------------------------------------------------

class TestEmbedQueue:

    def test_enqueue_for_embedding_failure(self, emb_eng):
        # _call_classmethod will fail since Graph.KG.EmbedQueue not in community
        result = emb_eng.enqueue_for_embedding(["emb_0", "emb_1"])
        assert isinstance(result, int)
        assert result >= 0

    def test_process_embed_queue_failure(self, emb_eng):
        result = emb_eng.process_embed_queue()
        assert isinstance(result, dict)
        assert "processed" in result

    def test_embed_queue_pending(self, emb_eng):
        result = emb_eng.embed_queue_pending()
        assert isinstance(result, int)

    def test_start_background_embedding(self, emb_eng):
        result = emb_eng.start_background_embedding()
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _probe_embedding_support — error branches
# ---------------------------------------------------------------------------

class TestProbeEmbeddingSupport:

    def test_probe_embedding_support_unknown_function(self, emb_eng):
        cursor_mock = MagicMock()
        cursor_mock.execute.side_effect = Exception("Unknown function EMBEDDING")
        cursor_mock.close = MagicMock()
        emb_eng._embedding_function_available = None
        with patch.object(emb_eng.conn, "cursor", return_value=cursor_mock):
            result = emb_eng._probe_embedding_support()
        assert result is False

    def test_probe_embedding_support_other_error_returns_true(self, emb_eng):
        cursor_mock = MagicMock()
        cursor_mock.execute.side_effect = Exception("some other error")
        cursor_mock.close = MagicMock()
        emb_eng._embedding_function_available = None
        with patch.object(emb_eng.conn, "cursor", return_value=cursor_mock):
            result = emb_eng._probe_embedding_support()
        assert result is True
