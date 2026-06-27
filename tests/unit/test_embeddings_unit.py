"""
Unit tests for _engine/embeddings.py covering:
- embed_text: python embedder paths (encode/embed/callable)
- embed_text: auto-load SentenceTransformer fallback
- _probe_embedding_support: cached, function-absent, function-present
- _probe_native_vec: cached, not supported, supported
- get_unembedded_nodes: success and error paths
- store_embedding: success, dim mismatch, dim inference
- store_embeddings: success, rollback on error, empty list
- embed_nodes: basic path, skip-already-embedded, text_fn, progress callback
- embed_edges: basic path, text_fn error, executemany fallback
- get_embedding: found with metadata, found no metadata, not found
- get_embeddings: empty list, multiple nodes
- enqueue_for_embedding: success and failure
- process_embed_queue: success and failure
- embed_queue_pending: success and failure
- embedding_count: success and error

No IRIS connection needed — mocks conn and cursor.
"""
import pytest
from unittest.mock import MagicMock, patch, call
from iris_vector_graph.engine import IRISGraphEngine


def _make_eng(dim=4):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.return_value = None
    cursor.executemany.return_value = None
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    cursor.description = []
    cursor.close.return_value = None
    eng = IRISGraphEngine(conn, embedding_dimension=dim)
    return eng, conn, cursor


# ---------------------------------------------------------------------------
# embed_text — python embedder paths
# ---------------------------------------------------------------------------

class TestEmbedText:

    def test_encode_path(self):
        eng, conn, cursor = _make_eng()
        embedder = MagicMock()
        embedder.encode.return_value = MagicMock(tolist=lambda: [0.1, 0.2, 0.3, 0.4])
        eng.embedder = embedder
        result = eng.embed_text("hello world")
        embedder.encode.assert_called_once_with("hello world")
        assert isinstance(result, list)

    def test_embed_method_path(self):
        eng, conn, cursor = _make_eng()
        embedder = MagicMock(spec=["embed"])
        embedder.embed.return_value = [0.5, 0.6, 0.7, 0.8]
        eng.embedder = embedder
        result = eng.embed_text("test text")
        embedder.embed.assert_called_once_with("test text")
        assert result == [0.5, 0.6, 0.7, 0.8]

    def test_callable_embedder_path(self):
        eng, conn, cursor = _make_eng()
        eng.embedder = lambda text: [float(i) for i in range(4)]
        result = eng.embed_text("any text")
        assert result == [0.0, 1.0, 2.0, 3.0]

    def test_no_embedder_raises_without_sentence_transformers(self):
        eng, conn, cursor = _make_eng()
        eng.embedder = None
        eng.embedding_config = None
        with patch("builtins.__import__", side_effect=ImportError("no sentence-transformers")):
            with pytest.raises((RuntimeError, ImportError)):
                eng.embed_text("text")

    def test_unsupported_embedder_raises_type_error(self):
        eng, conn, cursor = _make_eng()
        eng.embedder = object()  # no encode/embed, not callable
        eng.embedding_config = None
        with pytest.raises(TypeError):
            eng.embed_text("text")

    def test_native_embedding_path(self):
        eng, conn, cursor = _make_eng()
        eng.embedding_config = "my_model"
        eng._embedding_function_available = True
        cursor.fetchone.return_value = ("0.1,0.2,0.3,0.4",)
        result = eng.embed_text("probe text")
        assert isinstance(result, list)
        assert len(result) == 4

    def test_native_embedding_list_return(self):
        eng, conn, cursor = _make_eng()
        eng.embedding_config = "my_model"
        eng._embedding_function_available = True
        cursor.fetchone.return_value = ([0.1, 0.2, 0.3, 0.4],)
        result = eng.embed_text("probe text")
        assert result == [0.1, 0.2, 0.3, 0.4]

    def test_native_embedding_fallback_on_error(self):
        eng, conn, cursor = _make_eng()
        eng.embedding_config = "my_model"
        eng._embedding_function_available = True
        cursor.execute.side_effect = RuntimeError("embedding call failed")
        embedder = MagicMock()
        embedder.encode.return_value = MagicMock(tolist=lambda: [0.9, 0.8, 0.7, 0.6])
        eng.embedder = embedder
        result = eng.embed_text("fallback text")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _probe_embedding_support
# ---------------------------------------------------------------------------

class TestProbeEmbeddingSupport:

    def test_cached_true(self):
        eng, conn, cursor = _make_eng()
        eng._embedding_function_available = True
        call_count_before = cursor.execute.call_count
        result = eng._probe_embedding_support()
        assert result is True
        # Should not make any additional DB call beyond what __init__ already did
        assert cursor.execute.call_count == call_count_before

    def test_cached_false(self):
        eng, conn, cursor = _make_eng()
        eng._embedding_function_available = False
        call_count_before = cursor.execute.call_count
        result = eng._probe_embedding_support()
        assert result is False
        assert cursor.execute.call_count == call_count_before

    def test_unknown_function_error_returns_false(self):
        eng, conn, cursor = _make_eng()
        eng._embedding_function_available = None
        cursor.execute.side_effect = RuntimeError("unknown function EMBEDDING")
        result = eng._probe_embedding_support()
        assert result is False

    def test_config_missing_error_returns_true(self):
        eng, conn, cursor = _make_eng()
        eng._embedding_function_available = None
        cursor.execute.side_effect = RuntimeError("embedding config not found")
        result = eng._probe_embedding_support()
        assert result is True

    def test_no_error_returns_true(self):
        eng, conn, cursor = _make_eng()
        eng._embedding_function_available = None
        cursor.execute.return_value = None
        result = eng._probe_embedding_support()
        assert result is True


# ---------------------------------------------------------------------------
# _probe_native_vec
# ---------------------------------------------------------------------------

class TestProbeNativeVec:

    def test_cached_result_reused(self):
        eng, conn, cursor = _make_eng()
        eng._native_vec_available = True
        call_count_before = cursor.execute.call_count
        result = eng._probe_native_vec()
        assert result is True
        assert cursor.execute.call_count == call_count_before

    def test_unknown_function_returns_false(self):
        eng, conn, cursor = _make_eng()
        eng._native_vec_available = None
        cursor.execute.side_effect = RuntimeError("unknown function VECTOR_COSINE")
        result = eng._probe_native_vec()
        assert result is False

    def test_other_error_returns_true(self):
        eng, conn, cursor = _make_eng()
        eng._native_vec_available = None
        cursor.execute.side_effect = RuntimeError("table does not exist but function works")
        result = eng._probe_native_vec()
        assert result is True

    def test_success_returns_true(self):
        eng, conn, cursor = _make_eng()
        eng._native_vec_available = None
        cursor.execute.return_value = None
        result = eng._probe_native_vec()
        assert result is True


# ---------------------------------------------------------------------------
# get_unembedded_nodes
# ---------------------------------------------------------------------------

class TestGetUnembeddedNodes:

    def test_returns_node_ids(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = [("n1",), ("n2",), ("n3",)]
        result = eng.get_unembedded_nodes()
        assert result == ["n1", "n2", "n3"]

    def test_sql_error_returns_empty(self):
        eng, conn, cursor = _make_eng()
        cursor.execute.side_effect = RuntimeError("table not found")
        result = eng.get_unembedded_nodes()
        assert result == []


# ---------------------------------------------------------------------------
# store_embedding
# ---------------------------------------------------------------------------

class TestStoreEmbedding:

    def test_success_returns_true(self):
        eng, conn, cursor = _make_eng(dim=4)
        with patch.object(eng, "_assert_node_exists"):
            with patch.object(eng, "_get_embedding_dimension", return_value=4):
                result = eng.store_embedding("n1", [0.1, 0.2, 0.3, 0.4])
        assert result is True

    def test_dim_mismatch_raises_value_error(self):
        eng, conn, cursor = _make_eng(dim=4)
        with patch.object(eng, "_assert_node_exists"):
            with patch.object(eng, "_get_embedding_dimension", return_value=4):
                with pytest.raises(ValueError, match="dimension mismatch"):
                    eng.store_embedding("n1", [0.1, 0.2])  # only 2 dims

    def test_dim_inferred_from_input_when_detection_fails(self):
        eng, conn, cursor = _make_eng(dim=None)
        eng.embedding_dimension = None
        with patch.object(eng, "_assert_node_exists"):
            with patch.object(eng, "_get_embedding_dimension", side_effect=ValueError("no dim")):
                result = eng.store_embedding("n1", [0.1, 0.2, 0.3, 0.4])
        assert result is True
        assert eng.embedding_dimension == 4

    def test_with_metadata(self):
        eng, conn, cursor = _make_eng(dim=4)
        with patch.object(eng, "_assert_node_exists"):
            with patch.object(eng, "_get_embedding_dimension", return_value=4):
                result = eng.store_embedding("n1", [0.1, 0.2, 0.3, 0.4],
                                             metadata={"source": "test"})
        assert result is True


# ---------------------------------------------------------------------------
# store_embeddings
# ---------------------------------------------------------------------------

class TestStoreEmbeddings:

    def test_empty_list_returns_true(self):
        eng, conn, cursor = _make_eng(dim=4)
        result = eng.store_embeddings([])
        assert result is True

    def test_success_inserts_all(self):
        eng, conn, cursor = _make_eng(dim=4)
        items = [
            {"node_id": "n1", "embedding": [0.1, 0.2, 0.3, 0.4]},
            {"node_id": "n2", "embedding": [0.5, 0.6, 0.7, 0.8]},
        ]
        with patch.object(eng, "_assert_node_exists"):
            with patch.object(eng, "_get_embedding_dimension", return_value=4):
                result = eng.store_embeddings(items)
        assert result is True

    def test_dim_mismatch_raises(self):
        eng, conn, cursor = _make_eng(dim=4)
        items = [
            {"node_id": "n1", "embedding": [0.1, 0.2]},  # only 2 dims
        ]
        with patch.object(eng, "_assert_node_exists"):
            with patch.object(eng, "_get_embedding_dimension", return_value=4):
                with pytest.raises(ValueError, match="dimension mismatch"):
                    eng.store_embeddings(items)

    def test_insert_failure_triggers_rollback(self):
        eng, conn, cursor = _make_eng(dim=4)
        items = [{"node_id": "n1", "embedding": [0.1, 0.2, 0.3, 0.4]}]

        call_count = [0]
        def execute_side(sql, *args, **kwargs):
            call_count[0] += 1
            if "INSERT" in sql.upper():
                raise RuntimeError("insert blocked")
        cursor.execute.side_effect = execute_side

        with patch.object(eng, "_assert_node_exists"):
            with patch.object(eng, "_get_embedding_dimension", return_value=4):
                with pytest.raises(RuntimeError):
                    eng.store_embeddings(items)


# ---------------------------------------------------------------------------
# embed_nodes
# ---------------------------------------------------------------------------

class TestEmbedNodes:

    def test_basic_path_returns_stats(self):
        eng, conn, cursor = _make_eng(dim=4)
        call_seq = iter([
            [("n1",)],  # SELECT node_id FROM nodes
            [],         # SELECT id FROM kg_NodeEmbeddings (already embedded)
            [],         # SELECT rdf_props
        ])
        cursor.fetchall.side_effect = lambda: next(call_seq)
        with patch("iris_vector_graph.engine._is_sentence_transformer", return_value=False):
            with patch.object(eng, "embed_text", return_value=[0.1, 0.2, 0.3, 0.4]):
                result = eng.embed_nodes()
        assert "embedded" in result
        assert "total" in result

    def test_empty_graph_returns_zero_stats(self):
        eng, conn, cursor = _make_eng(dim=4)
        cursor.fetchall.return_value = []
        result = eng.embed_nodes()
        assert result == {"embedded": 0, "skipped": 0, "errors": 0, "total": 0}

    def test_force_flag_re_embeds_already_embedded(self):
        eng, conn, cursor = _make_eng(dim=4)
        call_seq = iter([[("n1",)], []])  # nodes, rdf_props
        cursor.fetchall.side_effect = lambda: next(call_seq)
        with patch.object(eng, "embed_text", return_value=[0.1, 0.2, 0.3, 0.4]):
            with patch("iris_vector_graph.engine._is_sentence_transformer", return_value=False):
                result = eng.embed_nodes(force=True)
        assert result["total"] == 1

    def test_text_fn_error_increments_errors(self):
        eng, conn, cursor = _make_eng(dim=4)
        call_seq = iter([[("n1",)], [], []])
        cursor.fetchall.side_effect = lambda: next(call_seq)
        def bad_text_fn(node_id, props):
            raise ValueError("text_fn exploded")
        with patch("iris_vector_graph.engine._is_sentence_transformer", return_value=False):
            result = eng.embed_nodes(text_fn=bad_text_fn)
        assert result["errors"] >= 1

    def test_progress_callback_is_called(self):
        eng, conn, cursor = _make_eng(dim=4)
        call_seq = iter([[("n1",)], [], []])
        cursor.fetchall.side_effect = lambda: next(call_seq)
        callback_calls = []
        def cb(done, total):
            callback_calls.append((done, total))
        with patch.object(eng, "embed_text", return_value=[0.1, 0.2, 0.3, 0.4]):
            with patch("iris_vector_graph.engine._is_sentence_transformer", return_value=False):
                eng.embed_nodes(progress_callback=cb)
        assert len(callback_calls) > 0


# ---------------------------------------------------------------------------
# embed_edges
# ---------------------------------------------------------------------------

class TestEmbedEdges:

    def test_basic_path_returns_stats(self):
        eng, conn, cursor = _make_eng(dim=4)
        call_seq = iter([[("n1", "TREATS", "n2")], []])
        cursor.fetchall.side_effect = lambda: next(call_seq)
        with patch.object(eng, "embed_text", return_value=[0.1, 0.2, 0.3, 0.4]):
            with patch("iris_vector_graph.engine._is_sentence_transformer", return_value=False):
                result = eng.embed_edges()
        assert "embedded" in result
        assert result["total"] == 1

    def test_empty_edges_returns_zero_stats(self):
        eng, conn, cursor = _make_eng(dim=4)
        cursor.fetchall.return_value = []
        result = eng.embed_edges()
        assert result == {"embedded": 0, "skipped": 0, "errors": 0, "total": 0}

    def test_text_fn_error_increments_errors(self):
        eng, conn, cursor = _make_eng(dim=4)
        call_seq = iter([[("n1", "TREATS", "n2")], []])
        cursor.fetchall.side_effect = lambda: next(call_seq)
        def bad_text_fn(s, p, o):
            raise RuntimeError("text_fn failed")
        with patch("iris_vector_graph.engine._is_sentence_transformer", return_value=False):
            result = eng.embed_edges(text_fn=bad_text_fn)
        assert result["errors"] >= 1

    def test_executemany_failure_falls_back_per_row(self):
        eng, conn, cursor = _make_eng(dim=4)
        call_seq = iter([[("n1", "TREATS", "n2")], []])
        cursor.fetchall.side_effect = lambda: next(call_seq)
        cursor.executemany.side_effect = RuntimeError("executemany not supported")
        with patch.object(eng, "embed_text", return_value=[0.1, 0.2, 0.3, 0.4]):
            with patch("iris_vector_graph.engine._is_sentence_transformer", return_value=False):
                result = eng.embed_edges()
        assert "embedded" in result


# ---------------------------------------------------------------------------
# get_embedding / get_embeddings
# ---------------------------------------------------------------------------

class TestGetEmbedding:

    def test_found_with_metadata(self):
        eng, conn, cursor = _make_eng()
        import json
        cursor.fetchone.return_value = ("n1", "0.1,0.2,0.3,0.4", json.dumps({"source": "test"}))
        result = eng.get_embedding("n1")
        assert result is not None
        assert result["id"] == "n1"
        assert len(result["embedding"]) == 4
        assert result["metadata"]["source"] == "test"

    def test_found_no_metadata(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.return_value = ("n1", "0.5,0.6,0.7,0.8", None)
        result = eng.get_embedding("n1")
        assert result is not None
        assert "metadata" not in result

    def test_not_found_returns_none(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.return_value = None
        result = eng.get_embedding("missing_node")
        assert result is None


class TestGetEmbeddings:

    def test_empty_list_returns_empty(self):
        eng, conn, cursor = _make_eng()
        result = eng.get_embeddings([])
        assert result == []

    def test_multiple_nodes(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = [
            ("n1", "0.1,0.2,0.3,0.4", None),
            ("n2", "0.5,0.6,0.7,0.8", None),
        ]
        result = eng.get_embeddings(["n1", "n2"])
        assert len(result) == 2
        assert result[0]["id"] == "n1"
        assert result[1]["id"] == "n2"


# ---------------------------------------------------------------------------
# embedding queue operations
# ---------------------------------------------------------------------------

class TestEmbedQueue:

    # The embed-queue methods call schema._call_classmethod(self.conn, ...) — the
    # canonical module-function seam (matches algorithms.py). Patch it there.
    # (A prior create=True patch targeted a self._call_classmethod that does not
    # exist on the engine; every call AttributeError'd into the except branch and
    # the queue feature was silently dead. These tests now assert the real value.)

    def test_enqueue_success(self):
        eng, conn, cursor = _make_eng()
        with patch("iris_vector_graph.schema._call_classmethod", return_value="5") as m:
            result = eng.enqueue_for_embedding(["n1", "n2", "n3"])
        assert result == 5
        assert m.call_args.args[1:3] == ("Graph.KG.EmbedQueue", "BulkEnqueue")

    def test_enqueue_failure_returns_zero(self):
        eng, conn, cursor = _make_eng()
        with patch("iris_vector_graph.schema._call_classmethod",
                   side_effect=RuntimeError("queue not available")):
            result = eng.enqueue_for_embedding(["n1"])
        assert result == 0

    def test_process_embed_queue_empty_claim(self):
        # spec 199: process_embed_queue now claims a batch (JSON array) then encodes.
        # Empty claim → nothing to do. (Full batched behavior is covered in
        # test_embed_queue_unit.py.)
        eng, conn, cursor = _make_eng()
        eng.embedder = MagicMock()
        with patch("iris_vector_graph.schema._call_classmethod", return_value="[]"):
            result = eng.process_embed_queue(batch_size=10)
        assert result == {"processed": 0, "errors": 0}

    def test_process_embed_queue_failure_returns_defaults(self):
        eng, conn, cursor = _make_eng()
        with patch("iris_vector_graph.schema._call_classmethod",
                   side_effect=RuntimeError("not deployed")):
            result = eng.process_embed_queue()
        assert result == {"processed": 0, "errors": 0}

    def test_embed_queue_pending_success(self):
        eng, conn, cursor = _make_eng()
        with patch("iris_vector_graph.schema._call_classmethod", return_value="42"):
            result = eng.embed_queue_pending()
        assert result == 42

    def test_embed_queue_pending_failure_returns_zero(self):
        eng, conn, cursor = _make_eng()
        with patch("iris_vector_graph.schema._call_classmethod",
                   side_effect=RuntimeError("queue gone")):
            result = eng.embed_queue_pending()
        assert result == 0

    def test_start_background_embedding_success(self):
        eng, conn, cursor = _make_eng()
        with patch("iris_vector_graph.schema._call_classmethod", return_value="task-1") as m:
            result = eng.start_background_embedding(batch_size=50)
        assert result == "task-1"
        assert m.call_args.args[1:3] == ("Graph.KG.EmbedQueue", "StartBackgroundTask")

    def test_start_background_embedding_failure_returns_empty(self):
        eng, conn, cursor = _make_eng()
        with patch("iris_vector_graph.schema._call_classmethod",
                   side_effect=RuntimeError("no bg task")):
            result = eng.start_background_embedding()
        assert result == ""


# ---------------------------------------------------------------------------
# embedding_count
# ---------------------------------------------------------------------------

class TestEmbeddingCount:

    def test_returns_count(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.return_value = (57,)
        result = eng.embedding_count()
        assert result == 57

    def test_sql_error_returns_zero(self):
        eng, conn, cursor = _make_eng()
        cursor.execute.side_effect = RuntimeError("table missing")
        result = eng.embedding_count()
        assert result == 0


# ---------------------------------------------------------------------------
# embed_text: auto-init SentenceTransformer + transformers quieting (lines 53-54, 56)
# ---------------------------------------------------------------------------

class TestEmbedTextAutoInit:

    def test_auto_init_sentence_transformer(self):
        """Lines 55-56: no embedder → auto-init SentenceTransformer."""
        eng, conn, cursor = _make_eng()
        eng.embedder = None
        eng.embedding_config = None

        mock_st = MagicMock()
        mock_result = MagicMock()
        mock_result.tolist.return_value = [0.1, 0.2, 0.3, 0.4]
        mock_st.encode.return_value = mock_result

        # Patch at the engine module — embeddings.py resolves the name via a
        # function-local `from iris_vector_graph.engine import ...`. No create=True:
        # the symbol must genuinely exist at the patch target. A previous
        # create=True (against the embeddings module) fabricated a missing name and
        # masked a production NameError on the auto-init path (agent-bus segfault).
        with patch("iris_vector_graph.engine._load_sentence_transformer", return_value=mock_st):
            result = eng.embed_text("hello world")

        assert mock_st is eng.embedder

    def test_auto_init_symbol_is_resolvable(self):
        """Regression: _load_sentence_transformer must be resolvable on the
        no-embedder auto-init path. It was called in embeddings.py but never
        imported → NameError (not ImportError, so the graceful fallback at
        embed_text never caught it). agent-bus auto-embed segfault."""
        from iris_vector_graph.engine import _load_sentence_transformer
        assert callable(_load_sentence_transformer)

    def test_auto_init_no_embedder_does_not_nameerror(self):
        """Regression: real auto-init path (embedder=None, transformers present)
        must not raise NameError. Patches sentence-transformers loading at the
        engine module (where _load_sentence_transformer is defined), NOT the
        embeddings module, so the actual name resolution in embeddings.py runs."""
        eng, conn, cursor = _make_eng()
        eng.embedder = None
        eng.embedding_config = None

        mock_st = MagicMock()
        mock_st.encode.return_value = MagicMock(tolist=lambda: [0.1, 0.2, 0.3, 0.4])

        with patch("iris_vector_graph.engine._load_sentence_transformer", return_value=mock_st):
            result = eng.embed_text("hello world")

        assert result == [0.1, 0.2, 0.3, 0.4]
        assert eng.embedder is mock_st

    def test_transformers_import_error_swallowed(self):
        """Lines 53-54: ImportError for transformers logging setup is swallowed."""
        eng, conn, cursor = _make_eng()
        eng.embedder = None
        eng.embedding_config = None

        mock_st = MagicMock()
        mock_result = MagicMock()
        mock_result.tolist.return_value = [0.1, 0.2, 0.3, 0.4]
        mock_st.encode.return_value = mock_result

        import builtins
        real_import = builtins.__import__
        def mock_import(name, *args, **kwargs):
            if name == "transformers":
                raise ImportError("no transformers")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with patch("iris_vector_graph.engine._load_sentence_transformer", return_value=mock_st):
                result = eng.embed_text("test")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _probe_native_vec: cursor.close() error swallowed (lines 122-123)
# ---------------------------------------------------------------------------

class TestProbeNativeVecClose:

    def test_cursor_close_error_swallowed(self):
        """Lines 122-123: cursor.close() raises → swallowed."""
        eng, conn, cursor = _make_eng()
        eng._native_vec_available = None
        cursor.execute.side_effect = Exception("VECTOR_COSINE not found unknown function")
        cursor.close.side_effect = RuntimeError("close failed")

        result = eng._probe_native_vec()
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# store_embeddings: dim infer path (lines 216-220)
# ---------------------------------------------------------------------------

class TestStoreEmbeddingsDimInfer:

    def test_dim_inferred_from_first_item(self):
        """Lines 216-220: _get_embedding_dimension raises → dim inferred from input."""
        eng, conn, cursor = _make_eng()
        eng.embedding_dimension = None

        with patch.object(eng, "_get_embedding_dimension", side_effect=ValueError("no dim")):
            with patch.object(eng, "_assert_node_exists", return_value=True):
                result = eng.store_embeddings([
                    {"node_id": "n1", "embedding": [0.1, 0.2, 0.3, 0.4]},
                ])
        assert result is True
        assert eng.embedding_dimension == 4

    def test_delete_exception_swallowed(self):
        """Lines 248-249: DELETE exception in store_embeddings swallowed."""
        eng, conn, cursor = _make_eng()

        sqls = []
        def exec_side(sql, *args, **kwargs):
            sqls.append(sql)
            if "DELETE" in sql:
                raise Exception("delete failed")
        cursor.execute.side_effect = exec_side

        with patch.object(eng, "_get_embedding_dimension", return_value=4):
            with patch.object(eng, "_assert_node_exists", return_value=True):
                result = eng.store_embeddings([
                    {"node_id": "n1", "embedding": [0.1, 0.2, 0.3, 0.4]},
                ])
        assert result is True


# ---------------------------------------------------------------------------
# embed_nodes: SentenceTransformer batch path + executemany fallback
# ---------------------------------------------------------------------------

class TestEmbedNodesBatchPath:

    def test_sentence_transformer_batch_encode(self):
        """Lines 360-363: use_batch=True → embedder.encode() batch called."""
        eng, conn, cursor = _make_eng()

        import numpy as np
        mock_embedder = MagicMock()
        mock_embedder.encode.return_value = np.array([[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]])
        eng.embedder = mock_embedder
        eng.embedding_config = None

        cursor.fetchall.side_effect = [
            [("n1",), ("n2",)],  # node_ids
            [],                  # already_embedded
            [("n1", "a", "text"), ("n2", "b", "text")],  # props
        ]
        cursor.fetchone.return_value = (0,)

        with patch("iris_vector_graph.engine._is_sentence_transformer", return_value=True):
            result = eng.embed_nodes()

        mock_embedder.encode.assert_called()
        assert result["embedded"] >= 0

    def test_executemany_fallback_per_row_on_nodes(self):
        """Lines 400-411: executemany fails → per-row fallback."""
        eng, conn, cursor = _make_eng()
        eng.embedding_config = None

        cursor.fetchall.side_effect = [
            [("n1",)],  # node_ids
            [],          # already_embedded
            [],          # props
        ]
        cursor.fetchone.return_value = (0,)
        cursor.executemany.side_effect = Exception("executemany failed")

        insert_sqls = []
        def exec_side(sql, *args, **kwargs):
            if "INSERT" in (sql or ""):
                insert_sqls.append(sql)
        cursor.execute.side_effect = exec_side

        with patch("iris_vector_graph.engine._is_sentence_transformer", return_value=False):
            with patch.object(eng, "embed_text", return_value=[0.1, 0.2, 0.3, 0.4]):
                result = eng.embed_nodes()

        assert isinstance(result, dict)
        assert len(insert_sqls) >= 1  # per-row INSERT called

    def test_no_texts_empty_batch_callback(self):
        """Lines 346-350: empty texts → commit called + progress_callback invoked."""
        eng, conn, cursor = _make_eng()
        eng.embedding_config = None

        cursor.fetchall.side_effect = [
            [("n1",)],  # node_ids
            [],          # already_embedded
            [],          # props
        ]
        cursor.fetchone.return_value = (0,)

        progress_calls = []
        with patch("iris_vector_graph.engine._is_sentence_transformer", return_value=False):
            result = eng.embed_nodes(
                text_fn=lambda nid, props: "",  # empty text → skipped
                progress_callback=lambda done, total: progress_calls.append((done, total))
            )

        assert len(progress_calls) >= 1
        assert progress_calls[0][1] >= 1


# ---------------------------------------------------------------------------
# embed_edges: batch path, executemany fallback, no-texts callback, delete swallowed
# ---------------------------------------------------------------------------

class TestEmbedEdgesBatchPath:

    def test_sentence_transformer_batch_encode(self):
        """Lines 529-530: use_batch=True → embedder.encode() batch called."""
        eng, conn, cursor = _make_eng()

        import numpy as np
        mock_embedder = MagicMock()
        mock_embedder.encode.return_value = np.array([[0.1, 0.2, 0.3, 0.4]])
        eng.embedder = mock_embedder
        eng.embedding_config = None

        cursor.fetchall.side_effect = [
            [("n1", "TREATS", "n2")],  # edges
            [],                          # already_embedded
        ]

        with patch("iris_vector_graph.engine._is_sentence_transformer", return_value=True):
            result = eng.embed_edges()

        mock_embedder.encode.assert_called()
        assert result["embedded"] >= 0

    def test_executemany_fallback_per_row_on_edges(self):
        """Lines 582-587: executemany fails → per-row fallback for edges."""
        eng, conn, cursor = _make_eng()
        eng.embedding_config = None

        cursor.fetchall.side_effect = [
            [("n1", "TREATS", "n2")],  # edges
            [],                          # already_embedded
        ]
        cursor.executemany.side_effect = Exception("executemany failed")

        insert_sqls = []
        def exec_side(sql, *args, **kwargs):
            if "INSERT" in (sql or ""):
                insert_sqls.append(sql)
        cursor.execute.side_effect = exec_side

        with patch("iris_vector_graph.engine._is_sentence_transformer", return_value=False):
            with patch.object(eng, "embed_text", return_value=[0.1, 0.2, 0.3, 0.4]):
                result = eng.embed_edges()

        assert isinstance(result, dict)

    def test_no_texts_progress_callback(self):
        """Lines 514-519: empty texts for edge → commit + progress_callback."""
        eng, conn, cursor = _make_eng()
        eng.embedding_config = None

        cursor.fetchall.side_effect = [
            [("n1", "TREATS", "n2")],  # edges
            [],                          # already_embedded
        ]

        progress_calls = []
        with patch("iris_vector_graph.engine._is_sentence_transformer", return_value=False):
            result = eng.embed_edges(
                text_fn=lambda s, p, o, props: "",
                progress_callback=lambda done, total: progress_calls.append((done, total))
            )

        assert len(progress_calls) >= 1

    def test_delete_exception_swallowed_in_edges(self):
        """Lines 561-564: delete exception in embed_edges insert loop swallowed."""
        eng, conn, cursor = _make_eng()
        eng.embedding_config = None

        cursor.fetchall.side_effect = [
            [("n1", "TREATS", "n2")],  # edges
            [],                          # already_embedded
        ]

        delete_calls = [0]
        def exec_side(sql, *args, **kwargs):
            if "DELETE" in (sql or ""):
                delete_calls[0] += 1
                raise Exception("delete failed")
        cursor.execute.side_effect = exec_side

        with patch("iris_vector_graph.engine._is_sentence_transformer", return_value=False):
            with patch.object(eng, "embed_text", return_value=[0.1, 0.2, 0.3, 0.4]):
                result = eng.embed_edges()

        assert delete_calls[0] >= 1
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# get_embeddings: metadata present (line 667)
# ---------------------------------------------------------------------------

class TestGetEmbeddingsMetadata:

    def test_metadata_included(self):
        """Line 667: metadata key added when metadata_json is not None."""
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = [
            ("n1", "0.1,0.2,0.3,0.4", '{"source": "test"}'),
        ]
        result = eng.get_embeddings(["n1"])
        assert len(result) == 1
        assert "metadata" in result[0]
        assert result[0]["metadata"]["source"] == "test"
