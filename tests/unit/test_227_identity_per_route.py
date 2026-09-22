"""Spec 227 T038 — identity is compared per route, and the graph is named (FR-016).

Spec 226 enforces identity per *table*. With routing, the table is derived from
`(graph, model)`, so the same enforcement is already per route — but two things
that were not true at 3.2.0 are true now, and both are silent when wrong:

- A conflict message that names only the table name names a hash. `kg_emb_3f2a…`
  tells an operator nothing about which graph refused the write, and the whole
  point of the message is that a same-width different-model conflict is
  undetectable below the registry.
- One graph's refusal must not touch another's. A conflict is raised before
  anything is written, and the route that raised it is one physical table, so
  graph B keeps working — asserted here rather than assumed, because a shared
  mutable `self.embedding_dimension` used to make one graph's inference leak into
  the next write.
"""

import pytest

from iris_vector_graph.constants import DEFAULT_GRAPH
from iris_vector_graph.exceptions import EmbeddingIdentityConflict
from iris_vector_graph.routing import route_table_name
from tests.unit.route_fakes_227 import FakeRegistry, engine_with

GRAPH_A = "ivg227-ident-a"
GRAPH_B = "ivg227-ident-b"
MODEL_A = "model-a"
MODEL_B = "model-b"


def _row(graph_id, model_key, dimension):
    return {
        "table_name": route_table_name(graph_id, model_key),
        "graph_id": graph_id,
        "mechanism": "iris-embedding-config",
        "model_key": model_key,
        "declared_config": model_key,
        "dimension": dimension,
        "dtype": "DOUBLE",
        "index_state": "present",
        "index_error": None,
    }


def _two_graphs():
    """Graph A at 384 under model A, graph B at 768 under model B."""
    registry = FakeRegistry(
        rows=[_row(GRAPH_A, MODEL_A, 384), _row(GRAPH_B, MODEL_B, 768)],
        tables=[route_table_name(GRAPH_A, MODEL_A), route_table_name(GRAPH_B, MODEL_B)],
    )
    return registry, engine_with(registry, embedding_dimension=384)


def test_a_width_mismatch_on_one_route_is_refused():
    registry, engine = _two_graphs()

    with pytest.raises(EmbeddingIdentityConflict):
        engine.enforce_embedding_identity(
            route_table_name(GRAPH_A, MODEL_A),
            dimension=768,
            config=MODEL_A,
            graph_id=GRAPH_A,
        )


def test_the_conflict_message_names_the_graph():
    """An opaque table name is not an answer to "which graph refused this?"."""
    registry, engine = _two_graphs()

    with pytest.raises(EmbeddingIdentityConflict) as excinfo:
        engine.enforce_embedding_identity(
            route_table_name(GRAPH_A, MODEL_A),
            dimension=768,
            config=MODEL_A,
            graph_id=GRAPH_A,
        )

    assert GRAPH_A in str(excinfo.value)
    assert excinfo.value.graph_id == GRAPH_A


def test_the_default_graph_is_named_as_the_default_graph():
    """`graph_id=''` has to read as something. An empty string spliced into a
    message reads as a missing word."""
    registry = FakeRegistry(
        rows=[_row(DEFAULT_GRAPH, MODEL_A, 384)],
        tables=[route_table_name(DEFAULT_GRAPH, MODEL_A)],
    )
    engine = engine_with(registry, embedding_dimension=384)

    with pytest.raises(EmbeddingIdentityConflict) as excinfo:
        engine.enforce_embedding_identity(
            route_table_name(DEFAULT_GRAPH, MODEL_A),
            dimension=768,
            config=MODEL_A,
            graph_id=DEFAULT_GRAPH,
        )

    assert "default graph" in str(excinfo.value)


def test_a_model_mismatch_on_one_route_is_refused():
    registry, engine = _two_graphs()

    with pytest.raises(EmbeddingIdentityConflict) as excinfo:
        engine.enforce_embedding_identity(
            route_table_name(GRAPH_A, MODEL_A),
            dimension=384,
            config=MODEL_B,
            graph_id=GRAPH_A,
        )

    assert GRAPH_A in str(excinfo.value)


def test_the_same_identity_on_the_same_route_is_accepted():
    registry, engine = _two_graphs()

    recorded = engine.enforce_embedding_identity(
        route_table_name(GRAPH_A, MODEL_A),
        dimension=384,
        config=MODEL_A,
        graph_id=GRAPH_A,
    )

    assert recorded.dimension == 384
    assert recorded.model_key == MODEL_A


def test_graph_bs_write_is_unaffected_by_graph_as_refusal():
    registry, engine = _two_graphs()

    with pytest.raises(EmbeddingIdentityConflict):
        engine.enforce_embedding_identity(
            route_table_name(GRAPH_A, MODEL_A),
            dimension=768,
            config=MODEL_A,
            graph_id=GRAPH_A,
        )

    recorded = engine.enforce_embedding_identity(
        route_table_name(GRAPH_B, MODEL_B),
        dimension=768,
        config=MODEL_B,
        graph_id=GRAPH_B,
    )
    assert recorded.dimension == 768


def test_each_route_is_read_with_its_own_graph_id():
    """The identity read is keyed `(table_name, graph_id)`; a routed read that
    passes `graph_id=''` finds nothing and enforces nothing."""
    registry, engine = _two_graphs()
    registry.statements.clear()

    engine.enforce_embedding_identity(
        route_table_name(GRAPH_A, MODEL_A),
        dimension=384,
        config=MODEL_A,
        graph_id=GRAPH_A,
    )

    reads = registry.statements_matching("SELECT mechanism")
    assert reads, "no identity read was issued"
    assert reads[0][1] == [route_table_name(GRAPH_A, MODEL_A), GRAPH_A]


def test_a_route_with_no_registry_row_enforces_nothing_and_raises_nothing():
    """Same rule as 3.2.0: nothing recorded means nothing to conflict with. An
    unrouted pair is `resolve_route`'s business, not enforcement's."""
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=384)

    assert (
        engine.enforce_embedding_identity(
            route_table_name(GRAPH_A, MODEL_A),
            dimension=384,
            config=MODEL_A,
            graph_id=GRAPH_A,
        )
        is None
    )


def test_recording_an_identity_for_a_real_graph_is_allowed_now():
    """3.2.0 refused any `graph_id` other than `''` because nothing read it. 227
    makes it real, so the guard has to go — with the route table admitted too."""
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=384)
    table = route_table_name(GRAPH_A, MODEL_A)

    from iris_vector_graph.embedding_identity import identity_from_config

    recorded = engine.set_embedding_identity(
        identity_from_config(MODEL_A, dimension=384), table, graph_id=GRAPH_A
    )

    assert recorded.model_key == MODEL_A
    assert registry.row_for(table, GRAPH_A) is not None


def test_a_table_that_is_neither_legacy_nor_routed_is_still_refused():
    """The guard that goes is the graph one. The table-name guard stays: a typo
    would otherwise create a registry row nothing ever reads."""
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=384)

    from iris_vector_graph.embedding_identity import identity_from_config

    with pytest.raises(ValueError):
        engine.set_embedding_identity(
            identity_from_config(MODEL_A, dimension=384), "nodes", graph_id=GRAPH_A
        )
