"""A routed table that already exists declares the width, not the writer that finds it.

`CREATE TABLE` is DDL and durable the moment it runs, while the registry row that
names it is an ordinary INSERT, so a routed table outliving its row is a normal
state — an interrupted create, a rolled-back fixture, a registry someone cleared.
Route creation has to tolerate that, and it does: `_create_routed_table` reports
"it was already there" rather than raising.

What it did not do is ask the surviving table how wide it is. It recorded the width
*this* caller asked for, and IRIS then refused every write against the real column
with `SQLCODE -104` — a raw driver error naming a hashed table, from a caller that
was told its route was ready. The column declaration is the truth about width
(FR-006), so the reuse path reconciles against it: same width adopts, different
width refuses with the identity conflict that says which widths and why.
"""

import pytest

from iris_vector_graph.exceptions import EmbeddingIdentityConflict
from iris_vector_graph.routing import route_table_name
from tests.unit.route_fakes_227 import FakeRegistry, engine_with

GRAPH = "ivg227-reuse"
MODEL = "model-reuse"


def _table() -> str:
    return route_table_name(GRAPH, MODEL)


def _registry_with_orphan(width) -> FakeRegistry:
    """The routed table, no row for it, and a declared width of `width`."""
    table = _table()
    return FakeRegistry(
        tables=[table],
        dimension_for=({table: width} if width else {}),
    )


class TestWidthConflictIsRefused:
    def test_orphan_table_at_another_width_refuses_the_route(self):
        registry = _registry_with_orphan(128)
        engine = engine_with(registry, embedding_dimension=384)

        with pytest.raises(EmbeddingIdentityConflict) as excinfo:
            engine.resolve_route(graph=GRAPH, model_key=MODEL, create=True, dimension=384)

        msg = str(excinfo.value)
        assert "128" in msg and "384" in msg
        assert "column declaration" in msg
        assert "-104" in msg, "the refusal should name the SQLCODE the write would have hit"
        assert excinfo.value.table_name == _table()
        assert excinfo.value.graph_id == GRAPH

    def test_a_refused_route_records_nothing(self):
        registry = _registry_with_orphan(128)
        engine = engine_with(registry, embedding_dimension=384)

        with pytest.raises(EmbeddingIdentityConflict):
            engine.resolve_route(graph=GRAPH, model_key=MODEL, create=True, dimension=384)

        assert registry.rows == [], "a refused route left a registry row behind"
        assert registry.created_tables == [], "the table was already there; nothing to create"

    def test_the_refusal_survives_a_second_attempt(self):
        """Not a one-shot: the second caller gets the same refusal, not a bad route."""
        registry = _registry_with_orphan(128)
        engine = engine_with(registry, embedding_dimension=384)

        for _ in range(2):
            with pytest.raises(EmbeddingIdentityConflict):
                engine.resolve_route(graph=GRAPH, model_key=MODEL, create=True, dimension=384)


class TestMatchingWidthIsAdopted:
    def test_orphan_table_at_the_same_width_is_reused(self):
        registry = _registry_with_orphan(384)
        engine = engine_with(registry, embedding_dimension=384)

        route = engine.resolve_route(graph=GRAPH, model_key=MODEL, create=True, dimension=384)

        assert route is not None and route.table_name == _table()
        assert route.dimension == 384
        assert registry.row_for(_table(), GRAPH) is not None, "the route was not recorded"

    def test_a_column_with_no_declared_width_does_not_block_the_route(self):
        """No declared width is the SQLCODE -260 shape, not evidence of a conflict.

        Nothing can be proved about such a column, and refusing on the strength of
        an unanswered question would make an undeclared column unroutable.
        """
        registry = _registry_with_orphan(None)
        engine = engine_with(registry, embedding_dimension=384)

        route = engine.resolve_route(graph=GRAPH, model_key=MODEL, create=True, dimension=384)

        assert route is not None and route.dimension == 384


class TestFreshCreateIsUnaffected:
    def test_a_table_this_call_creates_is_not_width_checked(self):
        """The width it was just created at is the width; asking again proves nothing."""
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=384)

        route = engine.resolve_route(graph=GRAPH, model_key=MODEL, create=True, dimension=384)

        assert route is not None and route.dimension == 384
        assert registry.created_tables == [_table()]
