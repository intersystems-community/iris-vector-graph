"""Unit tests for the biomedical example domain's `Protein.similar` resolver.

The resolver used to hand-write SQL against `Graph_KG.kg_NodeEmbeddings` keyed on
an `id` column.  Post-4.0.0 that table is keyed `(graph_id, node_id)` and has no
`id` column — but a DDL-created table still carries an implicit RowID spelled
`ID`, so `SELECT COUNT(*) ... WHERE id = 'PROTEIN:TP53'` parses, matches nothing,
and the resolver reported "no similar proteins" in every deployment.  These tests
pin the behaviour through the engine seam instead, where routing (spec 227) and
graph scope are already handled.
"""

import asyncio
import types as pytypes

import pytest

from examples.domains.biomedical.types import Protein


class _FakeEngine:
    """Records what the resolver asked for and answers with fixed rows."""

    def __init__(self, own=None, knn=None, nodes=None):
        self._own = own
        self._knn = knn or []
        self._nodes = nodes or {}
        self.get_embedding_calls = []
        self.search_calls = []

    def get_embedding(self, node_id, **kw):
        self.get_embedding_calls.append((node_id, kw))
        return self._own

    def search_nodes_by_vector(self, **kw):
        self.search_calls.append(kw)
        return self._knn

    def get_nodes(self, node_ids, **kw):
        return [self._nodes[n] for n in node_ids if n in self._nodes]


def _info(engine):
    return pytypes.SimpleNamespace(context={"engine": engine})


def _node(node_id, name):
    return {"id": node_id, "labels": ["Protein"], "properties": {}, "name": name}


def _protein(node_id="PROTEIN:TP53"):
    return Protein(
        id=node_id,
        labels=["Protein"],
        properties={},
        created_at=None,
        name="Tumor protein p53",
    )


def _run(coro):
    """Own the loop rather than borrowing the ambient one.

    `asyncio.get_event_loop()` passes in isolation and raises "There is no current event
    loop in thread 'MainThread'" in a full-suite run, because an earlier test closes the
    loop it found here. That made all six tests in this file fail together only when run
    with their neighbours — which reads as pollution from elsewhere, not as a defect here.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestSimilarResolver:

    def test_reads_own_embedding_then_searches_with_it(self):
        """The query of a KNN is a vector, never a node ID — passing `self.id`
        searches for a vector spelled `PROTEIN:TP53`."""
        engine = _FakeEngine(
            own={"embedding": [0.1, 0.2, 0.3]},
            knn=[("PROTEIN:TP53", 1.0), ("PROTEIN:MDM2", 0.58)],
            nodes={"PROTEIN:MDM2": _node("PROTEIN:MDM2", "MDM2 proto-oncogene")},
        )

        out = _run(_protein().similar(_info(engine), limit=10, threshold=0.0))

        assert engine.get_embedding_calls[0][0] == "PROTEIN:TP53"
        assert engine.search_calls[0]["query"] == [0.1, 0.2, 0.3]
        assert engine.search_calls[0]["label_filter"] == "Protein"
        assert [sp.protein.id for sp in out] == ["PROTEIN:MDM2"]
        assert out[0].similarity == pytest.approx(0.58)
        assert out[0].protein.name == "MDM2 proto-oncogene"

    def test_no_stored_embedding_returns_empty(self):
        engine = _FakeEngine(own=None)

        assert _run(_protein().similar(_info(engine), threshold=0.0)) == []
        assert engine.search_calls == []

    def test_threshold_filters_and_self_is_excluded(self):
        engine = _FakeEngine(
            own={"embedding": [1.0]},
            knn=[("PROTEIN:TP53", 1.0), ("PROTEIN:MDM2", 0.9), ("PROTEIN:P21", 0.1)],
            nodes={
                "PROTEIN:MDM2": _node("PROTEIN:MDM2", "MDM2"),
                "PROTEIN:P21": _node("PROTEIN:P21", "P21"),
            },
        )

        out = _run(_protein().similar(_info(engine), limit=10, threshold=0.5))

        assert [sp.protein.id for sp in out] == ["PROTEIN:MDM2"]

    def test_pairs_by_id_not_by_position(self):
        """`get_nodes` drops an ID it cannot find; indexing by position then pairs a
        node with another node's similarity."""
        engine = _FakeEngine(
            own={"embedding": [1.0]},
            knn=[("PROTEIN:GONE", 0.9), ("PROTEIN:MDM2", 0.7)],
            nodes={"PROTEIN:MDM2": _node("PROTEIN:MDM2", "MDM2")},
        )

        out = _run(_protein().similar(_info(engine), limit=10, threshold=0.0))

        assert [(sp.protein.id, sp.similarity) for sp in out] == [("PROTEIN:MDM2", 0.7)]

    def test_limit_caps_results(self):
        engine = _FakeEngine(
            own={"embedding": [1.0]},
            knn=[("PROTEIN:A", 0.9), ("PROTEIN:B", 0.8), ("PROTEIN:C", 0.7)],
            nodes={
                "PROTEIN:A": _node("PROTEIN:A", "A"),
                "PROTEIN:B": _node("PROTEIN:B", "B"),
                "PROTEIN:C": _node("PROTEIN:C", "C"),
            },
        )

        out = _run(_protein().similar(_info(engine), limit=2, threshold=0.0))

        assert len(out) == 2

    def test_no_engine_and_no_connection_returns_empty(self):
        info = pytypes.SimpleNamespace(context={})

        assert _run(_protein().similar(info, threshold=0.0)) == []
