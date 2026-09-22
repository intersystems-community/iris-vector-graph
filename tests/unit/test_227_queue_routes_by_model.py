"""The embed queue writes through routing, like every other writer (spec 227).

`_upsert_node_embedding` is the seam the queue stores vectors through, and it named
`kg_NodeEmbeddings` in its own SQL — graph-scoped since 227, but never model-routed. So a
worker declaring a model wrote every queued vector into the default pair's table: invisible
to the routed search the vector was queued for, and colliding on `(graph_id, node_id)` with
whatever the default pair holds for that node.

The refusal moved too. FR-011 asks whether the entry's model is one this worker can
produce, and 3.2.0 answered it by enforcing the entry's config against
`kg_NodeEmbeddings`' registry row. Under 227 that row is the default pair's row and nothing
else, so it is the wrong authority: the honest comparison is between the model the entry
asked for and the worker's own identity, which needs no table at all.

Fakes rather than a container because the question is which table the statement names.
"""

import json

from iris_vector_graph.constants import DEFAULT_GRAPH
from iris_vector_graph.routing import route_table_name
from tests.unit.route_fakes_227 import FakeRegistry, engine_with

GRAPH = "ivg227-queue"
MODEL = "queue-model"
LEGACY = "kg_NodeEmbeddings"


class _FixedEmbedder:
    """Returns the same vector for every text. Named, so it has an identity."""

    def __init__(self, width: int, model_name: str = "local-embedder"):
        self.width = width
        self.model_name = model_name

    def encode(self, texts):
        if isinstance(texts, str):
            return [0.5] * self.width
        return [[0.5] * self.width for _ in texts]


def _vector_inserts(registry):
    """Every INSERT that carries a vector, by the table it names."""
    return [
        sql
        for sql, _params in registry.statements_matching("INSERT INTO")
        if "TO_VECTOR" in sql
    ]


def _tables_written(registry):
    tables = set()
    for sql in _vector_inserts(registry):
        after = sql.split("INSERT INTO", 1)[1].strip()
        tables.add(after.split("(", 1)[0].strip().split(".")[-1])
    return tables


class TestTheQueueWriteIsRouted:
    def test_a_declared_model_writes_to_its_own_route(self):
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=4, embedding_config=MODEL)

        engine._upsert_node_embedding("n1", [0.1] * 4, graph=GRAPH)

        assert _tables_written(registry) == {route_table_name(GRAPH, MODEL)}

    def test_the_default_pair_still_writes_where_3_2_0_wrote(self):
        """FR-015: the one pair with a pre-227 home keeps it, queue included."""
        registry = FakeRegistry(dimension_for={LEGACY: 4})
        engine = engine_with(registry, embedding_dimension=4)

        engine._upsert_node_embedding("n1", [0.1] * 4, graph=DEFAULT_GRAPH)

        assert _tables_written(registry) == {LEGACY}

    def test_a_per_entry_model_routes_without_the_engine_declaring_it(self):
        """The entry names the model, not the engine — that is what `config` is for."""
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=4)

        engine._upsert_node_embedding("n1", [0.1] * 4, graph=GRAPH, model_key=MODEL)

        assert _tables_written(registry) == {route_table_name(GRAPH, MODEL)}

    def test_two_graphs_do_not_share_a_table(self):
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=4, embedding_config=MODEL)

        engine._upsert_node_embedding("shared", [0.1] * 4, graph="graph-a")
        engine._upsert_node_embedding("shared", [0.2] * 4, graph="graph-b")

        assert _tables_written(registry) == {
            route_table_name("graph-a", MODEL),
            route_table_name("graph-b", MODEL),
        }


class TestProcessEmbedQueueRoutes:
    """`process_embed_queue` end to end over a faked `Graph.KG.EmbedQueue`."""

    def _served(self, monkeypatch, entries):
        """Serve `entries` from `ClaimPendingBatch`; record every call made back."""
        calls = []

        def fake_call(conn, class_name, method, *args):
            calls.append((method, args))
            if method == "ClaimPendingBatch":
                return json.dumps(entries)
            return "1"

        monkeypatch.setattr(
            "iris_vector_graph.schema._call_classmethod", fake_call
        )
        return calls

    @staticmethod
    def _errors(calls):
        return [args for method, args in calls if method == "SetResult" and args[1] == "ERROR"]

    def test_an_agreeing_entry_lands_in_the_workers_route(self, monkeypatch):
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=4, embedding_config=MODEL)
        engine.embedder = _FixedEmbedder(4)
        calls = self._served(
            monkeypatch,
            [{"reqId": "r1", "text": "t", "node_id": "n1", "config": MODEL, "graph": GRAPH}],
        )

        report = engine.process_embed_queue(batch_size=10)

        assert report == {"processed": 1, "errors": 0}
        assert self._errors(calls) == []
        assert _tables_written(registry) == {route_table_name(GRAPH, MODEL)}

    def test_an_entry_naming_another_model_is_refused(self, monkeypatch):
        """The worker cannot produce `other-model` vectors, so it must not claim to.

        Nothing is written anywhere — not into the other model's route, and not into the
        default pair's table on the way past.
        """
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=4, embedding_config=MODEL)
        engine.embedder = _FixedEmbedder(4)
        calls = self._served(
            monkeypatch,
            [
                {"reqId": "bad", "text": "t", "node_id": "n1",
                 "config": "other-model", "graph": GRAPH},
                {"reqId": "good", "text": "t", "node_id": "n2",
                 "config": MODEL, "graph": GRAPH},
            ],
        )

        report = engine.process_embed_queue(batch_size=10)

        assert report == {"processed": 1, "errors": 1}, "one bad entry stalled the batch"
        errors = self._errors(calls)
        assert [args[0] for args in errors] == ["bad"]
        assert "other-model" in errors[0][2] and MODEL in errors[0][2]
        assert _tables_written(registry) == {route_table_name(GRAPH, MODEL)}
        assert not any("n1" in params for _sql, params in registry.statements)

    def test_an_empty_config_is_the_workers_own_identity(self, monkeypatch):
        """Empty means "whatever this worker is", which is a route, not a free pass."""
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=4, embedding_config=MODEL)
        engine.embedder = _FixedEmbedder(4)
        calls = self._served(
            monkeypatch,
            [{"reqId": "r1", "text": "t", "node_id": "n1", "config": "", "graph": GRAPH}],
        )

        report = engine.process_embed_queue(batch_size=10)

        assert report == {"processed": 1, "errors": 0}
        assert self._errors(calls) == []
        assert _tables_written(registry) == {route_table_name(GRAPH, MODEL)}

    def test_an_undeclared_worker_refuses_an_entry_that_names_a_model(self, monkeypatch):
        """An engine that cannot say what produces its vectors cannot honour a demand."""
        registry = FakeRegistry(dimension_for={LEGACY: 4})
        engine = engine_with(registry, embedding_dimension=4)
        # A working embedder, so the refusal is the reason nothing is written and not a
        # missing model: `embedder=None` would fail the entry from `embed_text` instead.
        engine.embedder = _FixedEmbedder(4)
        calls = self._served(
            monkeypatch,
            [{"reqId": "r1", "text": "t", "node_id": "n1", "config": MODEL, "graph": GRAPH}],
        )

        report = engine.process_embed_queue(batch_size=10)

        assert report == {"processed": 0, "errors": 1}
        assert MODEL in self._errors(calls)[0][2]
        assert _vector_inserts(registry) == []

    def test_an_absent_graph_subscript_is_the_default_graph(self, monkeypatch):
        """A pre-upgrade entry has no `graph` key, and `None` would bind as SQL NULL."""
        registry = FakeRegistry(dimension_for={LEGACY: 4})
        engine = engine_with(registry, embedding_dimension=4)
        engine.embedder = _FixedEmbedder(4)
        self._served(
            monkeypatch,
            [{"reqId": "r1", "text": "t", "node_id": "n1", "config": ""}],
        )

        report = engine.process_embed_queue(batch_size=10)

        assert report == {"processed": 1, "errors": 0}
        inserts = _vector_inserts(registry)
        assert len(inserts) == 1
        params = [
            params
            for sql, params in registry.statements_matching("INSERT INTO")
            if "TO_VECTOR" in sql
        ][0]
        assert params[0] == DEFAULT_GRAPH
