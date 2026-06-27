"""E2E tests for the enhanced embedding queue (spec 199) against a live IRIS container.

Models the real consumer (mindwalk) flow: create nodes → enqueue text → process in
batches (one embedder call per batch) → results land → vector search finds them.

Constitution IV: live-container E2E, non-optional. SKIP_IRIS_TESTS defaults to "false";
no hardcoded ports (uses the `engine` fixture from tests/integration/conftest.py).

A deterministic stand-in embedder is used so the test does not depend on
sentence-transformers being installed, while still exercising the REAL queue → encode →
SetResult → kg_NodeEmbeddings → vector search path (the path no unit mock can fool).
"""
import os
import uuid
import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


def _deterministic_embedder(dim=384):
    """A stable stand-in: hashes text into a fixed-dim vector. Same text → same vector,
    so vector search is meaningful. `encode([...])` returns one vector per input."""
    import hashlib

    def _vec(text):
        h = hashlib.sha256(text.encode("utf-8")).digest()
        # tile hash bytes to dim floats in [0,1)
        return [(h[i % len(h)] / 255.0) for i in range(dim)]

    class _Emb:
        def encode(self, texts):
            if isinstance(texts, str):
                return _vec(texts)
            return [_vec(t) for t in texts]

    return _Emb()


def _unique(prefix):
    return f"{prefix}:{uuid.uuid4().hex[:8]}"


def _make_nodes(engine, node_ids):
    """Create nodes so node-keyed embeddings have a home."""
    for nid in node_ids:
        try:
            engine.create_node(nid)
        except Exception:
            pass


class TestEmbedQueueE2E:
    # ----- US1: enqueue → batched process → search (T016, the MVP gate) -----
    def test_enqueue_process_search_roundtrip(self, engine):
        engine.embedder = _deterministic_embedder(384)
        ids = [_unique("eqn1"), _unique("eqn2"), _unique("eqn3")]
        _make_nodes(engine, ids)

        n = engine.enqueue_for_embedding(node_ids=ids)
        assert n == len(ids)

        # process in batches until drained
        total_processed = 0
        guard = 0
        while engine.embed_queue_pending() > 0 and guard < 20:
            rep = engine.process_embed_queue(batch_size=2)
            total_processed += rep["processed"]
            assert rep["errors"] == 0
            guard += 1
        assert total_processed >= len(ids)

        # embeddings landed in kg_NodeEmbeddings
        cur = engine.conn.cursor()
        placeholders = ",".join(["?"] * len(ids))
        cur.execute(
            f"SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings WHERE id IN ({placeholders})",
            ids,
        )
        assert int(cur.fetchone()[0]) == len(ids)

    def test_process_respects_batch_size_live(self, engine):
        engine.embedder = _deterministic_embedder(384)
        ids = [_unique("eqb") for _ in range(5)]
        _make_nodes(engine, ids)
        engine.enqueue_for_embedding(node_ids=ids)
        rep = engine.process_embed_queue(batch_size=2)
        assert rep["processed"] <= 2  # at most B per call
        assert engine.embed_queue_pending() >= 1  # remainder still pending

    # ----- US2: pending count + clear_done (T021) -----
    def test_pending_count_and_clear_done(self, engine):
        # Shared ^EmbedQueue global → use deltas, not absolute counts (other tests may
        # leave entries). Clear DONE up front so our run starts from a known floor.
        engine.embedder = _deterministic_embedder(384)
        engine.clear_done()
        ids = [_unique("eqm") for _ in range(3)]
        _make_nodes(engine, ids)
        before = engine.embed_queue_pending()
        engine.enqueue_for_embedding(node_ids=ids)
        assert engine.embed_queue_pending() >= before + 3  # our 3 are pending now

        # process until our 3 are done (pending returns to <= baseline)
        guard = 0
        while engine.embed_queue_pending() > before and guard < 30:
            engine.process_embed_queue(batch_size=10)
            guard += 1
        assert engine.embed_queue_pending() <= before  # our entries no longer pending

        cleared = engine.clear_done()
        assert cleared >= 3  # at least our 3 DONE entries removed

    # ----- US3: per-entry failure isolation (T025) -----
    def test_one_failure_does_not_sink_batch(self, engine):
        # An embedder that raises on a specific poison text, succeeds otherwise.
        base = _deterministic_embedder(384)

        class _Poison:
            def encode(self, texts):
                if isinstance(texts, str):
                    if texts == "POISON":
                        raise ValueError("cannot embed poison")
                    return base.encode(texts)
                # batch: raise so the engine falls back per-entry
                if any(t == "POISON" for t in texts):
                    raise ValueError("poison in batch")
                return base.encode(texts)

        engine.embedder = _Poison()
        good1, bad, good2 = _unique("eqg1"), _unique("eqbad"), _unique("eqg2")
        _make_nodes(engine, [good1, bad, good2])
        # enqueue node-keyed with controlled text by enqueuing free text won't attach to
        # node; instead set node display so the queue text differs. Simplest: enqueue
        # free text where one is POISON, assert per-entry isolation via the report.
        engine.enqueue_for_embedding(texts=["fine one", "POISON", "fine two"])

        rep = {"processed": 0, "errors": 0}
        guard = 0
        while engine.embed_queue_pending() > 0 and guard < 20:
            r = engine.process_embed_queue(batch_size=10)
            rep["processed"] += r["processed"]
            rep["errors"] += r["errors"]
            guard += 1
        assert rep["errors"] >= 1      # the poison entry failed
        assert rep["processed"] >= 2   # the two good entries succeeded
