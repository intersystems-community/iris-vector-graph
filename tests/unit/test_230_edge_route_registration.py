"""Spec 230 T034 — an edge route is a route, and it is not the node route (FR-006).

Edge vectors used to live in one namespace-wide `Graph_KG.kg_EdgeEmbeddings` with no
`graph_id` at all, so two graphs holding the same `(s, p, o_id)` shared one row and one
declared width. Spec 227 gave node embeddings a route per `(graph, model)`; this is the
same treatment for edges, and the thing that can go wrong is subtler than the leak it
fixes: one `(graph, model)` pair now has *two* routes, and every lookup keys on the pair.

So the claims here are about keeping them apart:

1. An edge route is its own table, under its own prefix, with the edge key.
2. The registry row says `kind = 'edge'`, and a node lookup does not find it.
3. A row that says nothing about kind is a node route — that is every row written
   before 4.0.0, including 3.2.0's adopted ones.
4. A name and a kind are checked together, so a `kg_emb_` name can never get edge
   columns and a `kg_eemb_` name can never get node columns.

Statements, not a live IRIS: the fake registry enforces the primary key and refuses a
duplicate `CREATE TABLE`. The live half is the US2 E2E.
"""

import re

import pytest

from iris_vector_graph.constants import EDGE_ROUTE_TABLE_PREFIX, ROUTE_TABLE_PREFIX
from iris_vector_graph._engine.schema import ROUTE_KIND_EDGE, ROUTE_KIND_NODE
from iris_vector_graph.routing import edge_route_table_name, route_table_name
from tests.unit.route_fakes_227 import FakeRegistry, engine_with

GRAPH = "ivg230-edge-a"
MODEL = "model-a"


def _node_row(table_name, graph_id, model_key, dimension=4, **extra):
    """A registry row as 227 wrote it: no `kind` column value at all."""
    row = {
        "table_name": table_name,
        "graph_id": graph_id,
        "mechanism": None if model_key is None else "iris-embedding-config",
        "model_key": model_key,
        "declared_config": model_key,
        "dimension": dimension,
        "dtype": "DOUBLE",
        "index_state": "present",
        "index_error": None,
    }
    row.update(extra)
    return row


# ------------------------------------------------------------------ the table


def test_creating_an_edge_route_creates_an_edge_table():
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=4)

    route = engine.resolve_route(
        graph=GRAPH, model_key=MODEL, create=True, dimension=4, kind=ROUTE_KIND_EDGE
    )

    assert route is not None
    assert route.table_name == edge_route_table_name(GRAPH, MODEL)
    assert route.table_name.startswith(EDGE_ROUTE_TABLE_PREFIX)
    assert route.kind == ROUTE_KIND_EDGE
    assert route.table_name in registry.created_tables


def test_the_edge_table_is_keyed_on_the_triple():
    """`(graph_id, s, p, o_id)`. A node route's `(graph_id, node_id)` cannot hold an
    edge: two endpoints and a predicate do not fit in one node reference."""
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=4)
    engine.resolve_route(
        graph=GRAPH, model_key=MODEL, create=True, dimension=4, kind=ROUTE_KIND_EDGE
    )

    creates = registry.statements_matching("CREATE TABLE")
    assert creates, registry.statements
    ddl = creates[0][0]
    m = re.search(r"UNIQUE\s*\(([^)]*)\)", ddl, re.IGNORECASE)
    assert m, ddl
    cols = [c.strip().lower() for c in m.group(1).split(",")]
    assert cols == ["graph_id", "s", "p", "o_id"], ddl


def test_the_edge_table_carries_no_foreign_key():
    """There is nothing composite to point at: `rdf_edges`'s primary key is an
    IDENTITY `edge_id`, not the triple, and an FK would make an edge vector
    unwritable until its edge row exists."""
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=4)
    engine.resolve_route(
        graph=GRAPH, model_key=MODEL, create=True, dimension=4, kind=ROUTE_KIND_EDGE
    )

    ddl = registry.statements_matching("CREATE TABLE")[0][0]
    assert "FOREIGN KEY" not in ddl.upper(), ddl


def test_the_edge_predicate_column_is_as_wide_as_the_table_it_replaces():
    """`kg_EdgeEmbeddings.p` is VARCHAR(512). Narrowing it here would refuse a
    predicate the old table accepted, which the 4.0.0 migration could not copy."""
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=4)
    engine.resolve_route(
        graph=GRAPH, model_key=MODEL, create=True, dimension=4, kind=ROUTE_KIND_EDGE
    )

    ddl = registry.statements_matching("CREATE TABLE")[0][0]
    m = re.search(r"\bp\s+VARCHAR\((\d+)\)", ddl, re.IGNORECASE)
    assert m, ddl
    assert int(m.group(1)) >= 512, ddl


# ------------------------------------------------------------------ the row


def test_the_registry_row_records_the_kind():
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=4)
    engine.resolve_route(
        graph=GRAPH, model_key=MODEL, create=True, dimension=4, kind=ROUTE_KIND_EDGE
    )

    row = registry.row_for(edge_route_table_name(GRAPH, MODEL), GRAPH)
    assert row is not None, registry.rows
    assert row.get("kind") == ROUTE_KIND_EDGE, row


def test_one_pair_can_hold_both_kinds_at_once():
    """The whole point of the discriminator: both routes exist, and each lookup
    finds its own."""
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=4)

    node = engine.resolve_route(graph=GRAPH, model_key=MODEL, create=True, dimension=4)
    edge = engine.resolve_route(
        graph=GRAPH, model_key=MODEL, create=True, dimension=4, kind=ROUTE_KIND_EDGE
    )

    assert node is not None and edge is not None
    assert node.table_name == route_table_name(GRAPH, MODEL)
    assert edge.table_name == edge_route_table_name(GRAPH, MODEL)
    assert node.table_name != edge.table_name
    assert len(registry.rows) == 2, registry.rows


def test_an_edge_lookup_does_not_answer_with_the_node_route():
    """A pair with only a node route has no edge route. Answering with the node
    table would hand a reader `(graph_id, node_id)` rows where it selects `s`."""
    registry = FakeRegistry(
        rows=[_node_row(route_table_name(GRAPH, MODEL), GRAPH, MODEL, kind=ROUTE_KIND_NODE)],
        tables=[route_table_name(GRAPH, MODEL)],
    )
    engine = engine_with(registry, embedding_dimension=4)

    assert engine.resolve_route(graph=GRAPH, model_key=MODEL, kind=ROUTE_KIND_EDGE) is None


def test_a_node_lookup_does_not_answer_with_the_edge_route():
    registry = FakeRegistry(
        rows=[
            _node_row(
                edge_route_table_name(GRAPH, MODEL), GRAPH, MODEL, kind=ROUTE_KIND_EDGE
            )
        ],
        tables=[edge_route_table_name(GRAPH, MODEL)],
    )
    engine = engine_with(registry, embedding_dimension=4)

    assert engine.resolve_route(graph=GRAPH, model_key=MODEL) is None


def test_a_row_that_says_nothing_about_kind_is_a_node_route():
    """Every row written before 4.0.0, including 3.2.0's adopted ones. An equality
    test against `'node'` would hide all of them from the node lookup."""
    registry = FakeRegistry(
        rows=[_node_row(route_table_name(GRAPH, MODEL), GRAPH, MODEL)],
        tables=[route_table_name(GRAPH, MODEL)],
    )
    engine = engine_with(registry, embedding_dimension=4)

    route = engine.resolve_route(graph=GRAPH, model_key=MODEL)
    assert route is not None
    assert route.table_name == route_table_name(GRAPH, MODEL)
    assert engine.resolve_route(graph=GRAPH, model_key=MODEL, kind=ROUTE_KIND_EDGE) is None


# ----------------------------------------------------------- name against kind


def test_the_node_ddl_refuses_an_edge_name():
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=4)

    with pytest.raises(ValueError, match="kg_emb_"):
        engine.routed_table_ddl(edge_route_table_name(GRAPH, MODEL), dimension=4)


def test_the_edge_ddl_refuses_a_node_name():
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=4)

    with pytest.raises(ValueError, match="kg_eemb_"):
        engine.routed_table_ddl(
            route_table_name(GRAPH, MODEL), dimension=4, kind=ROUTE_KIND_EDGE
        )


def test_an_unknown_kind_is_refused_rather_than_treated_as_node():
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=4)

    with pytest.raises(ValueError, match="kind"):
        engine.resolve_route(graph=GRAPH, model_key=MODEL, kind="nodes")


def test_the_two_prefixes_cannot_be_confused_by_a_prefix_test():
    """The admin row count and the migration's table scan both classify by prefix."""
    node = route_table_name(GRAPH, MODEL)
    edge = edge_route_table_name(GRAPH, MODEL)
    assert not node.startswith(EDGE_ROUTE_TABLE_PREFIX)
    assert not edge.startswith(ROUTE_TABLE_PREFIX)
