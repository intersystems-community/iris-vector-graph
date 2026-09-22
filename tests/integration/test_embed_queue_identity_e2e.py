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

from iris_vector_graph.embedding_identity import identity_from_config, normalize_model_key
from iris_vector_graph.routing import route_table_name
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


def _route_table(engine, model, graph: str = "") -> str:
    """The table a model's vectors live in, asked of the registry and not of the hash.

    `route_table_name` is only the name a *new* route gets. An existing row can point a
    pair somewhere else — notably at `kg_NodeEmbeddings` itself, which is what spec 226's
    adoption leaves behind when a 3.2.0 install declared a model: that install keeps
    writing where it wrote, and recomputing the hash here would look for its vectors in a
    table that does not exist.
    """
    route = engine.resolve_route(graph, model, create=False)
    return route.table_name if route is not None else route_table_name(
        graph, normalize_model_key(model)
    )


def _embedding_exists(
    conn, node_id: str, *, model=None, graph: str = "", engine=None
) -> bool:
    """Whether a vector for ``node_id`` exists in the table its model routes to.

    Two things moved under 227. The row is keyed ``node_id``, not ``id`` — and ``id`` is
    the table's RowID alias, so the old predicate compared an integer against a string,
    matched nothing, and reported "no vector" for every write. And a declared model does
    not live in ``kg_NodeEmbeddings`` at all unless the registry says so: only the default
    pair does by default (FR-015), so a queue entry naming a model has to be looked for
    where routing put it.
    """
    if model is None:
        table = _NODE_TABLE
    elif engine is not None:
        table = _route_table(engine, model, graph)
    else:
        table = route_table_name(graph, normalize_model_key(model))
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT COUNT(*) FROM Graph_KG.{table} "
            f"WHERE node_id = ? AND COALESCE(graph_id, '') = COALESCE(?, '')",
            [node_id, graph],
        )
        return int(cur.fetchone()[0]) > 0
    except Exception:
        # No routed table means no vector, which is an answer and not an error: the
        # refusal path under test is meant to leave nothing behind, table included.
        return False
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
        # Nowhere: not in the table its own model would route to, and not in the default
        # pair's table either.
        assert not _embedding_exists(
            iris_connection, bad, model="model-beta", engine=engine
        ), "the conflicting entry still wrote a vector to its own route"
        assert not _embedding_exists(
            iris_connection, bad
        ), "the conflicting entry wrote a vector to the default table"

        good_entry = _entry(iris_connection, good)
        assert good_entry["status"] == "DONE"
        assert _embedding_exists(
            iris_connection, good, model="model-alpha", engine=engine
        )

        _forget(iris_connection, good)

    def test_empty_config_is_the_workers_own_identity(self, iris_connection, width, clean):
        """An empty `config` is not an unknown model — it is whatever the worker is.

        Reading it as unknown would let every legacy entry through untouched, which is
        exactly the hole this feature closes. Under 227 "whatever the worker is" is also a
        *route*: the entry's vector goes where the worker's own model routes, not into the
        default pair's table. `kg_NodeEmbeddings` here holds a row for a third model, and
        it is no longer the authority over a `model-alpha` worker's writes — the test that
        pinned the old behaviour asserted a refusal on the strength of exactly that row.
        """
        node = f"ivg226-qempty-{uuid.uuid4().hex[:8]}"
        engine = _engine(iris_connection, dim=width, model="model-alpha")
        engine.create_node(node)
        engine.embedder = _deterministic_embedder(width)

        engine.set_embedding_identity(
            identity_from_config("model-gamma", dimension=width), _NODE_TABLE
        )
        _queue_call(iris_connection, "Enqueue", node, "", node)

        report = engine.process_embed_queue(batch_size=10)
        assert report["errors"] == 0, "an entry naming nothing was refused"
        assert _entry(iris_connection, node)["status"] == "DONE"
        assert _embedding_exists(
            iris_connection, node, model="model-alpha", engine=engine
        )
        assert not _embedding_exists(iris_connection, node), (
            "the worker wrote into the default pair's table, which records another model"
        )
        _forget(iris_connection, node)

    def test_an_entry_naming_the_workers_own_model_is_honoured(
        self, iris_connection, width, clean
    ):
        """The agreeing case, so the refusal above is about the disagreement.

        And the vector is where a routed search will look for it: the same table an entry
        that named nothing lands in, because both resolve to the worker's own identity.
        """
        node = f"ivg227-qsame-{uuid.uuid4().hex[:8]}"
        engine = _engine(iris_connection, dim=width, model="model-alpha")
        engine.create_node(node)
        engine.embedder = _deterministic_embedder(width)

        _queue_call(iris_connection, "Enqueue", node, "model-alpha", node)

        report = engine.process_embed_queue(batch_size=10)
        assert report["errors"] == 0
        assert _entry(iris_connection, node)["status"] == "DONE"
        assert _embedding_exists(
            iris_connection, node, model="model-alpha", engine=engine
        )
        _forget(iris_connection, node)
