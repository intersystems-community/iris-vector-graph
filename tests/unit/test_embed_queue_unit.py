"""Unit tests for the enhanced embedding queue (spec 199).

Covers the Python EmbeddingsMixin queue methods:
- enqueue_for_embedding (node_ids and texts)
- process_embed_queue (batched: ONE embedder.encode call per batch)
- embed_queue_pending
- clear_done
- start_background_embedding (regression)

Test discipline (R8): patch the REAL seam iris_vector_graph.schema._call_classmethod.
NEVER mock.patch(..., create=True) on a name the code does not resolve at that target —
that fabricates phantom symbols and masks bugs (it is what hid the 3 bugs that spawned
this spec). No IRIS connection needed — mocks conn/cursor and the seam.
"""
import json
import pytest
from unittest.mock import MagicMock, patch
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


SEAM = "iris_vector_graph.schema._call_classmethod"


# ---------------------------------------------------------------------------
# Phase 2 / Foundational — T003: ClaimPendingBatch + SetResult + status canon
# ---------------------------------------------------------------------------

class TestQueuePrimitivesSeam:
    def test_pending_count_calls_pendingcount(self):
        eng, _, _ = _make_eng()
        with patch(SEAM, return_value="7") as m:
            assert eng.embed_queue_pending() == 7
        assert m.call_args.args[1:3] == ("Graph.KG.EmbedQueue", "PendingCount")

    def test_pending_count_zero_on_backend_absent(self):
        eng, _, _ = _make_eng()
        with patch(SEAM, side_effect=RuntimeError("no class")):
            assert eng.embed_queue_pending() == 0  # FR-011: graceful, no raise


# ---------------------------------------------------------------------------
# US1 — enqueue + batched process
# ---------------------------------------------------------------------------

class TestEnqueue:
    def test_enqueue_node_ids_calls_bulkenqueue(self):  # T009, FR-004/FR-012
        eng, _, _ = _make_eng()
        with patch(SEAM, return_value="3") as m:
            n = eng.enqueue_for_embedding(node_ids=["a", "b", "c"])
        assert n == 3
        assert m.call_args.args[1:3] == ("Graph.KG.EmbedQueue", "BulkEnqueue")

    def test_enqueue_node_ids_positional_backcompat(self):  # FR-012
        eng, _, _ = _make_eng()
        with patch(SEAM, return_value="2") as m:
            n = eng.enqueue_for_embedding(["x", "y"])
        assert n == 2
        assert m.call_args.args[2] == "BulkEnqueue"

    def test_enqueue_texts_calls_bulkenqueuetext(self):  # T010, FR-001/FR-014
        eng, _, _ = _make_eng()
        with patch(SEAM, return_value="2") as m:
            n = eng.enqueue_for_embedding(texts=["hello world", "foo bar"])
        assert n == 2
        assert m.call_args.args[1:3] == ("Graph.KG.EmbedQueue", "BulkEnqueueText")

    def test_enqueue_empty_returns_zero(self):
        eng, _, _ = _make_eng()
        assert eng.enqueue_for_embedding() == 0
        assert eng.enqueue_for_embedding(node_ids=[]) == 0


class TestProcessBatch:
    def _claim(self, items):
        return json.dumps(items)

    def test_process_encodes_batch_in_one_call(self):  # T011, FR-005/FR-006, SC-002
        eng, _, _ = _make_eng()
        embedder = MagicMock()
        embedder.encode.return_value = [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]]
        eng.embedder = embedder
        claim = self._claim([
            {"reqId": "n1", "text": "alpha", "node_id": "n1"},
            {"reqId": "n2", "text": "beta", "node_id": "n2"},
        ])
        # ClaimPendingBatch returns the claim JSON; SetResult calls return "" (ok)
        with patch(SEAM, side_effect=[claim, "", ""]) as m:
            report = eng.process_embed_queue(batch_size=10)
        # ONE encode call for the whole batch
        assert embedder.encode.call_count == 1
        assert embedder.encode.call_args.args[0] == ["alpha", "beta"]
        assert report["processed"] == 2 and report["errors"] == 0
        # First seam call is ClaimPendingBatch
        assert m.call_args_list[0].args[2] == "ClaimPendingBatch"
        # Followed by SetResult DONE for each
        setresult_calls = [c for c in m.call_args_list if c.args[2] == "SetResult"]
        assert len(setresult_calls) == 2
        assert all(c.args[4] == "DONE" for c in setresult_calls)

    def test_process_empty_queue_returns_zero(self):
        eng, _, _ = _make_eng()
        eng.embedder = MagicMock()
        with patch(SEAM, return_value="[]"):
            report = eng.process_embed_queue(batch_size=10)
        assert report == {"processed": 0, "errors": 0}

    def test_process_respects_batch_size(self):  # T012
        eng, _, _ = _make_eng()
        embedder = MagicMock()
        embedder.encode.return_value = [[0.1, 0.2, 0.3, 0.4]]
        eng.embedder = embedder
        # ClaimPendingBatch is asked for batch_size=1; returns 1 item even though more pending
        claim = self._claim([{"reqId": "n1", "text": "a", "node_id": "n1"}])
        with patch(SEAM, side_effect=[claim, ""]) as m:
            report = eng.process_embed_queue(batch_size=1)
        assert report["processed"] == 1
        # batch_size passed through to ClaimPendingBatch
        assert m.call_args_list[0].args[3] == 1

    def test_process_backend_absent_safe(self):  # FR-011
        eng, _, _ = _make_eng()
        eng.embedder = MagicMock()
        with patch(SEAM, side_effect=RuntimeError("no class")):
            report = eng.process_embed_queue()
        assert report == {"processed": 0, "errors": 0}


# ---------------------------------------------------------------------------
# US2 — pending / clear_done
# ---------------------------------------------------------------------------

class TestQueueManagement:
    def test_clear_done_calls_cleardone(self):  # T018, FR-009
        eng, _, _ = _make_eng()
        with patch(SEAM, return_value="5") as m:
            assert eng.clear_done() == 5
        assert m.call_args.args[1:3] == ("Graph.KG.EmbedQueue", "ClearDone")

    def test_clear_done_zero_on_backend_absent(self):
        eng, _, _ = _make_eng()
        with patch(SEAM, side_effect=RuntimeError("gone")):
            assert eng.clear_done() == 0


# ---------------------------------------------------------------------------
# US3 — per-entry failure isolation
# ---------------------------------------------------------------------------

class TestFailureIsolation:
    def test_one_entry_error_rest_done(self):  # T022, FR-007
        eng, _, _ = _make_eng()
        embedder = MagicMock()

        # encode raises for the batch containing a poison item only if called per-item;
        # for the batch path, simulate a per-entry encode that fails on "bad".
        def encode(texts):
            out = []
            for t in texts:
                if t == "bad":
                    raise ValueError("cannot embed")
                out.append([0.1, 0.2, 0.3, 0.4])
            return out

        embedder.encode.side_effect = encode
        eng.embedder = embedder
        claim = json.dumps([
            {"reqId": "n1", "text": "good", "node_id": "n1"},
            {"reqId": "n2", "text": "bad", "node_id": "n2"},
            {"reqId": "n3", "text": "good2", "node_id": "n3"},
        ])
        # ClaimPendingBatch then SetResult per entry (3 SetResults)
        with patch(SEAM, side_effect=[claim, "", "", ""]) as m:
            report = eng.process_embed_queue(batch_size=10)
        assert report["errors"] == 1
        assert report["processed"] == 2
        setresults = [c for c in m.call_args_list if c.args[2] == "SetResult"]
        statuses = {c.args[3]: c.args[4] for c in setresults}  # reqId -> status
        assert statuses.get("n2") == "ERROR"
        assert statuses.get("n1") == "DONE" and statuses.get("n3") == "DONE"


# ---------------------------------------------------------------------------
# Polish — regression: start_background_embedding (T026, FR-010)
# ---------------------------------------------------------------------------

class TestEncodeBatchPaths:
    """Cover the three embedder shapes in _encode_batch."""
    def test_encode_path(self):
        eng, _, _ = _make_eng()
        e = MagicMock()
        e.encode.return_value = [[1.0, 2.0]]
        eng.embedder = e
        assert eng._encode_batch(["x"]) == [[1.0, 2.0]]

    def test_embed_path(self):
        eng, _, _ = _make_eng()
        e = MagicMock(spec=["embed"])
        e.embed.return_value = [3.0, 4.0]
        eng.embedder = e
        assert eng._encode_batch(["x"]) == [[3.0, 4.0]]

    def test_callable_path(self):
        eng, _, _ = _make_eng()
        eng.embedder = lambda t: [9.0, 9.0]
        assert eng._encode_batch(["x"]) == [[9.0, 9.0]]

    def test_unsupported_embedder_raises(self):
        eng, _, _ = _make_eng()
        eng.embedder = object()
        with pytest.raises(TypeError):
            eng._encode_batch(["x"])


class TestUpsertAndNodeLanding:
    def test_node_keyed_result_lands_in_table(self):
        # process a node-keyed entry → _upsert_node_embedding issues DELETE+INSERT
        eng, conn, cursor = _make_eng()
        e = MagicMock()
        e.encode.return_value = [[0.1, 0.2, 0.3, 0.4]]
        eng.embedder = e
        claim = json.dumps([{"reqId": "n1", "text": "t", "node_id": "n1"}])
        with patch(SEAM, side_effect=[claim, ""]):
            report = eng.process_embed_queue(batch_size=10)
        assert report["processed"] == 1
        # an INSERT into kg_NodeEmbeddings was attempted
        sqls = [str(c.args[0]) for c in cursor.execute.call_args_list if c.args]
        assert any("kg_NodeEmbeddings" in s and "INSERT" in s for s in sqls)

    def test_free_text_entry_no_node_upsert(self):
        eng, conn, cursor = _make_eng()
        e = MagicMock()
        e.encode.return_value = [[0.1, 0.2, 0.3, 0.4]]
        eng.embedder = e
        claim = json.dumps([{"reqId": "txt:1", "text": "t", "node_id": ""}])
        before = cursor.execute.call_count
        with patch(SEAM, side_effect=[claim, ""]):
            report = eng.process_embed_queue(batch_size=10)
        assert report["processed"] == 1
        # No node_id → _upsert_node_embedding must NOT run, so no new execute() calls
        # during processing (result is stored on the queue entry via SetResult only).
        assert cursor.execute.call_count == before


class TestStartBackground:
    def test_start_background_calls_task(self):
        eng, _, _ = _make_eng()
        with patch(SEAM, return_value="task-9") as m:
            assert eng.start_background_embedding(batch_size=50) == "task-9"
        assert m.call_args.args[1:3] == ("Graph.KG.EmbedQueue", "StartBackgroundTask")

    def test_start_background_empty_on_backend_absent(self):
        eng, _, _ = _make_eng()
        with patch(SEAM, side_effect=RuntimeError("no task")):
            assert eng.start_background_embedding() == ""
