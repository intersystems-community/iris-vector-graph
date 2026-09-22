"""A route records the mechanism of the writer that created it (spec 227 + 226).

The registry row is a claim about *how* a table's vectors were produced, and the only
writer whose mechanism is knowable when a route is created is the one creating it.
Deriving the row from the route's model key instead reads every key as an IRIS
`embedding_config`, because that is what naming a config means — so a worker with a local
`sentence-transformers` embedder created a route that said `iris-embedding-config`, and
then the identity check on that same write refused it against the row it had just written.
A local-embedder writer could not store a single vector into a new route.

`route_table_name` hashes the model key and nothing else, which is what makes this
reachable rather than theoretical: one model name produced two ways is one route.
"""

import pytest

from iris_vector_graph.exceptions import EmbeddingIdentityConflict
from iris_vector_graph.routing import route_table_name
from tests.unit.route_fakes_227 import FakeRegistry, engine_with

GRAPH = "ivg227-writer"
MODEL = "all-minilm-l6-v2"


class _StubEmbedder:
    """Names itself, which is all `_embedder_model_name` reads. Never embeds here."""

    def __init__(self, model_name: str):
        self.model_name = model_name


class TestSentenceTransformersWriter:
    def test_route_records_sentence_transformers(self):
        registry = FakeRegistry()
        engine = engine_with(
            registry, embedding_dimension=16, embedder=_StubEmbedder(MODEL)
        )

        route = engine.resolve_route(graph=GRAPH, create=True, dimension=16)

        assert route is not None
        assert route.table_name == route_table_name(GRAPH, MODEL)
        row = registry.row_for(route.table_name, GRAPH)
        assert row is not None, "the route was created without a registry row"
        assert row["mechanism"] == "sentence-transformers"
        assert row["model_key"] == MODEL

    def test_the_creating_writer_is_not_refused_by_its_own_row(self):
        """The defect end to end: create the route, then write through it."""
        registry = FakeRegistry()
        engine = engine_with(
            registry, embedding_dimension=16, embedder=_StubEmbedder(MODEL)
        )

        assert engine.store_embedding("n1", [0.5] * 16, graph=GRAPH) is True

    def test_an_iris_config_writer_still_conflicts_with_it(self):
        """The refusal that should happen: same model name, different producer.

        Both resolve to one route, and their vectors are not interchangeable. This is the
        conflict the wrong mechanism was masking — it fired against the local writer
        instead of against the one that disagreed with the record.
        """
        registry = FakeRegistry()
        engine = engine_with(
            registry, embedding_dimension=16, embedder=_StubEmbedder(MODEL)
        )
        assert engine.store_embedding("n1", [0.5] * 16, graph=GRAPH) is True

        other = engine_with(registry, embedding_dimension=16, embedding_config=MODEL)
        with pytest.raises(EmbeddingIdentityConflict) as excinfo:
            other.store_embedding("n2", [0.25] * 16, graph=GRAPH)

        assert excinfo.value.recorded.mechanism == "sentence-transformers"
        assert excinfo.value.offered.mechanism == "iris-embedding-config"


class TestOtherWritersAreUnchanged:
    def test_an_embedding_config_engine_records_iris_embedding_config(self):
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=16, embedding_config=MODEL)

        route = engine.resolve_route(graph=GRAPH, create=True, dimension=16)

        row = registry.row_for(route.table_name, GRAPH)
        assert row["mechanism"] == "iris-embedding-config"
        assert row["model_key"] == MODEL

    def test_a_per_work_model_key_records_iris_embedding_config(self):
        """A model named for one piece of work, not by the engine (spec 226, FR-011).

        The engine declares nothing, so there is no writer whose mechanism could be
        borrowed; a named configuration is an IRIS embedding config.
        """
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=16)

        route = engine.resolve_route(graph=GRAPH, model_key=MODEL, create=True, dimension=16)

        row = registry.row_for(route.table_name, GRAPH)
        assert row["mechanism"] == "iris-embedding-config"
        assert row["model_key"] == MODEL

    def test_an_engine_declaring_nothing_records_the_undeclared_identity(self):
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=16)

        route = engine.resolve_route(graph=GRAPH, create=True, dimension=16)

        assert route.table_name == route_table_name(GRAPH, None)
        row = registry.row_for(route.table_name, GRAPH)
        assert row["mechanism"] is None
        assert row["model_key"] is None
