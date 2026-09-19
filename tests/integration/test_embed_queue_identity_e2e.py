"""E2E tests for the embed queue under the identity contract (spec 226, US3, FR-011).

The queue is the one seam where the declared configuration lives in the database and never
reaches Python: `Enqueue` has always written `^EmbedQueue(reqId, "config")`, while
`ClaimPendingBatch` pushed `reqId`, `text`, and `node_id` only. A worker therefore had no
way to know which model an entry asked for, which makes the queue the way past the
registry. These tests are live because the fact under test is what the ObjectScript
classmethod puts on the wire.

Live against `ivg-iris-enterprise` (port 31972). Widths are read at test time — the live
declared width oscillates across this suite.
"""

import contextlib
import json
import os
import uuid

import pytest

from iris_vector_graph.embedding_identity import identity_from_config
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.exceptions import EmbeddingIdentityConflict
from iris_vector_graph.schema import _call_classmethod
from tests.integration.test_embedding_registry_e2e import (
    _clear_registry,
    _live_dimension,
)

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

_NODE_TABLE = "kg_NodeEmbeddings"
_QUEUE = "Graph.KG.EmbedQueue"


def _queue_call(conn, method: str, *args):
    return _call_classmethod(conn, _QUEUE, method, *args)


def _claim(conn, batch_size: int = 100):
    """Whatever `ClaimPendingBatch` actually puts on the wire, parsed."""
    raw = str(_queue_call(conn, "ClaimPendingBatch", batch_size))
    return json.loads(raw) if raw else []


def _entry(conn, req_id: str) -> dict:
    """One queue entry's own state, including a failed entry's reason."""
    raw = str(_queue_call(conn, "GetEntry", req_id))
    return json.loads(raw) if raw else {}


def _drain(conn) -> None:
    """Leave the queue as it was found: mark everything DONE, then clear DONE."""
    with contextlib.suppress(Exception):
        for entry in _claim(conn, 1000):
            _queue_call(conn, "SetResult", entry["reqId"], "DONE", "")
    with contextlib.suppress(Exception):
        _queue_call(conn, "ClearDone")


def _forget(conn, req_id: str) -> None:
    with contextlib.suppress(Exception):
        _queue_call(conn, "SetResult", req_id, "DONE", "")
        _queue_call(conn, "ClearDone")


def _deterministic_embedder(dim: int):
    """Stable stand-in so the test does not depend on sentence-transformers."""
    import hashlib

    def _vec(text: str):
        h = hashlib.sha256(text.encode("utf-8")).digest()
        return [(h[i % len(h)] / 255.0) for i in range(dim)]

    class _Emb:
        def encode(self, texts):
            if isinstance(texts, str):
                return _vec(texts)
            return [_vec(t) for t in texts]

    return _Emb()


def _engine(conn, *, dim, model=None):
    return IRISGraphEngine(conn, embedding_dimension=dim, embedding_config=model)


@pytest.fixture
def width(iris_connection):
    dim = _live_dimension(iris_connection)
    assert dim, f"Graph_KG.{_NODE_TABLE}.emb has no declared width"
    return dim


@pytest.fixture
def clean(iris_connection):
    _clear_registry(iris_connection)
    _drain(iris_connection)
    yield
    _drain(iris_connection)
    _clear_registry(iris_connection)


def _embedding_exists(conn, node_id: str) -> bool:
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) FROM Graph_KG.{_NODE_TABLE} WHERE id = ?", [node_id])
        return int(cur.fetchone()[0]) > 0
    finally:
        with contextlib.suppress(Exception):
            cur.close()


# ------------------------------------------------------------------------------- T028


class TestClaimCarriesConfig:
    def test_claim_batch_returns_config(self, iris_connection, width, clean):
        """T028 / FR-011: the claimed entry names the model it asked for.

        `Enqueue` has always stored the config. Before this feature `ClaimPendingBatch`
        dropped it, so the worker could not have enforced anything even in principle.
        """
        node = f"ivg226-q-{uuid.uuid4().hex[:8]}"
        engine = _engine(iris_connection, dim=width, model="queue-model")
        engine.create_node(node)

        assert _queue_call(iris_connection, "Enqueue", node, "queue-model", node)

        claimed = {e["reqId"]: e for e in _claim(iris_connection)}
        assert node in claimed, "the entry was not claimable"
        assert claimed[node].get("config") == "queue-model", (
            "ClaimPendingBatch dropped the config — the worker cannot enforce what it "
            "never receives"
        )

        # And an entry enqueued with no config reports an empty config, not a missing key:
        # empty means "whatever the worker itself is", which the worker can resolve.
        plain = f"ivg226-qplain-{uuid.uuid4().hex[:8]}"
        engine.create_node(plain)
        _queue_call(iris_connection, "Enqueue", plain, "", plain)
        claimed = {e["reqId"]: e for e in _claim(iris_connection)}
        assert "config" in claimed[plain]
        assert claimed[plain]["config"] == ""


# ------------------------------------------------------------------------------- T029


class TestEnqueueRefusesConflict:
    def test_enqueue_refuses_conflicting_config(self, iris_connection, width, clean):
        """T029: a conflicting entry never enters the queue.

        Refusing at enqueue is the whole point: an entry that cannot be honoured is work
        that will fail later, on a worker, away from whoever asked for it.
        """
        node = f"ivg226-qref-{uuid.uuid4().hex[:8]}"
        engine_a = _engine(iris_connection, dim=width, model="model-alpha")
        engine_a.create_node(node)
        engine_a.set_embedding_identity(
            identity_from_config("model-alpha", dimension=width), _NODE_TABLE
        )

        engine_b = _engine(iris_connection, dim=width, model="model-beta")
        before = engine_b.embed_queue_pending()

        with pytest.raises(EmbeddingIdentityConflict):
            engine_b.enqueue_for_embedding(node_ids=[node], embedding_config="model-beta")

        assert engine_b.embed_queue_pending() == before, "a refused enqueue still queued work"

        # The agreeing config is accepted, so the refusal is about the conflict and not
        # about enqueueing being broken.
        assert engine_a.enqueue_for_embedding(node_ids=[node], embedding_config="model-alpha") == 1
        _forget(iris_connection, node)

    def test_free_text_enqueue_is_refused_too(self, iris_connection, width, clean):
        """The `texts=` mode writes the same `config` subscript, so it is the same seam."""
        engine_a = _engine(iris_connection, dim=width, model="model-alpha")
        engine_a.set_embedding_identity(
            identity_from_config("model-alpha", dimension=width), _NODE_TABLE
        )

        engine_b = _engine(iris_connection, dim=width, model="model-beta")
        before = engine_b.embed_queue_pending()
        with pytest.raises(EmbeddingIdentityConflict):
            engine_b.enqueue_for_embedding(texts=["some text"], embedding_config="model-beta")
        assert engine_b.embed_queue_pending() == before


# ------------------------------------------------------------------------------- T030


class TestWorkerFailsOnlyTheConflictingEntry:
    def test_conflicting_entry_fails_and_batch_continues(self, iris_connection, width, clean):
        """T030 / FR-011: one bad entry must not stall the queue.

        The conflicting entry is enqueued through the ObjectScript classmethod directly,
        which is how such an entry gets there in reality: written by a different writer,
        or queued before the registry row existed.
        """
        good = f"ivg226-qgood-{uuid.uuid4().hex[:8]}"
        bad = f"ivg226-qbad-{uuid.uuid4().hex[:8]}"

        engine = _engine(iris_connection, dim=width, model="model-alpha")
        engine.create_node(good)
        engine.create_node(bad)
        engine.set_embedding_identity(
            identity_from_config("model-alpha", dimension=width), _NODE_TABLE
        )
        engine.embedder = _deterministic_embedder(width)

        _queue_call(iris_connection, "Enqueue", good, "model-alpha", good)
        _queue_call(iris_connection, "Enqueue", bad, "model-beta", bad)

        report = engine.process_embed_queue(batch_size=10)
        assert report["processed"] >= 1, "the agreeing entry did not complete"
        assert report["errors"] >= 1, "the conflicting entry was processed anyway"

        bad_entry = _entry(iris_connection, bad)
        assert bad_entry["status"] == "ERROR"
        assert "model-beta" in bad_entry["error"]
        assert "model-alpha" in bad_entry["error"]
        assert not _embedding_exists(
            iris_connection, bad
        ), "the conflicting entry still wrote a vector"

        good_entry = _entry(iris_connection, good)
        assert good_entry["status"] == "DONE"
        assert _embedding_exists(iris_connection, good)

        _forget(iris_connection, good)

    def test_empty_config_is_the_workers_own_identity(self, iris_connection, width, clean):
        """An empty `config` is not an unknown model — it is whatever the worker is.

        Reading it as unknown would let every legacy entry through untouched, which is
        exactly the hole this feature closes.
        """
        node = f"ivg226-qempty-{uuid.uuid4().hex[:8]}"
        engine = _engine(iris_connection, dim=width, model="model-alpha")
        engine.create_node(node)
        engine.embedder = _deterministic_embedder(width)

        # The registry names a different model than the worker declares, and the entry
        # itself names nothing: the worker's own identity is what gets compared.
        engine.set_embedding_identity(
            identity_from_config("model-gamma", dimension=width), _NODE_TABLE
        )
        _queue_call(iris_connection, "Enqueue", node, "", node)

        report = engine.process_embed_queue(batch_size=10)
        assert report["errors"] >= 1
        entry = _entry(iris_connection, node)
        assert entry["status"] == "ERROR"
        assert "model-gamma" in entry["error"] and "model-alpha" in entry["error"]
        assert not _embedding_exists(iris_connection, node)
