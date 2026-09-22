"""Spec 230 T036 — `status()` counts every routed embedding table, not one name.

`engine.status()` answered `node_embeddings` and `edge_embeddings` with a
`SELECT COUNT(*)` against `kg_NodeEmbeddings` and `kg_EdgeEmbeddings`. Once a pair's
vectors live in `kg_emb_<hash>` or `kg_eemb_<hash>`, that count reports zero for an
install whose vectors are all routed — a number an operator reads as "the embeddings
are gone" while a search over the same rows answers fine.

Four claims:

1. The count is the sum over the tables of that kind: the legacy default route plus
   every route the registry names.
2. Kind is read from the registry row, not from a prefix test on the name. A node
   route must not be added to the edge total.
3. An install with no registry counts its legacy tables and reports nothing missing —
   that is a 3.2.0 database, not a broken one.
4. A route the registry names but the database has lost is an error on the report,
   not a silent zero and not an exception out of `status()`.
"""

from iris_vector_graph.routing import edge_route_table_name, route_table_name
from tests.unit.route_fakes_227 import FakeRegistry, UnreadableRegistry, engine_with

DIM = 4
GRAPH_A, GRAPH_B = "ivg230-count-a", "ivg230-count-b"


def _row(table_name, graph_id, kind, model="m"):
    return {
        "table_name": table_name,
        "graph_id": graph_id,
        "mechanism": "iris-embedding-config",
        "model_key": model,
        "declared_config": model,
        "dimension": DIM,
        "dtype": "DOUBLE",
        "index_state": "present",
        "index_error": None,
        "kind": kind,
    }


class TestTheNodeCount:
    def test_it_sums_the_legacy_table_and_every_node_route(self):
        a = route_table_name(GRAPH_A, "m")
        b = route_table_name(GRAPH_B, "m")
        registry = FakeRegistry(
            rows=[_row(a, GRAPH_A, "node"), _row(b, GRAPH_B, "node")],
            tables=[a, b],
            counts={"kg_NodeEmbeddings": 5, a: 7, b: 11},
        )
        engine = engine_with(registry, embedding_dimension=DIM)

        assert engine.status().tables.node_embeddings == 23

    def test_an_edge_route_is_not_part_of_the_node_total(self):
        """`kg_eemb_` does not start with `kg_emb_`, but the kind is what decides:
        a row saying `edge` is an edge route whatever it is called."""
        edge = edge_route_table_name(GRAPH_A, "m")
        registry = FakeRegistry(
            rows=[_row(edge, GRAPH_A, "edge")],
            tables=[edge],
            counts={"kg_NodeEmbeddings": 2, edge: 100},
        )
        engine = engine_with(registry, embedding_dimension=DIM)

        assert engine.status().tables.node_embeddings == 2


class TestTheEdgeCount:
    def test_it_sums_the_legacy_table_and_every_edge_route(self):
        a = edge_route_table_name(GRAPH_A, "m")
        b = edge_route_table_name(GRAPH_B, "m")
        registry = FakeRegistry(
            rows=[_row(a, GRAPH_A, "edge"), _row(b, GRAPH_B, "edge")],
            tables=[a, b],
            counts={"kg_EdgeEmbeddings": 1, a: 3, b: 4},
        )
        engine = engine_with(registry, embedding_dimension=DIM)

        assert engine.status().tables.edge_embeddings == 8

    def test_a_node_route_is_not_part_of_the_edge_total(self):
        node = route_table_name(GRAPH_A, "m")
        registry = FakeRegistry(
            rows=[_row(node, GRAPH_A, "node")],
            tables=[node],
            counts={"kg_EdgeEmbeddings": 6, node: 50},
        )
        engine = engine_with(registry, embedding_dimension=DIM)

        assert engine.status().tables.edge_embeddings == 6

    def test_a_row_that_says_nothing_about_kind_is_a_node_route(self):
        """Every registry row written before 4.0.0 is a node route, and the column's
        DEFAULT says so. Counting one as an edge would inflate the edge total by a
        whole install's worth of node vectors."""
        node = route_table_name(GRAPH_A, "m")
        row = _row(node, GRAPH_A, "node")
        del row["kind"]
        registry = FakeRegistry(
            rows=[row],
            tables=[node],
            counts={"kg_EdgeEmbeddings": 6, "kg_NodeEmbeddings": 0, node: 50},
        )
        engine = engine_with(registry, embedding_dimension=DIM)

        status = engine.status()
        assert status.tables.edge_embeddings == 6
        assert status.tables.node_embeddings == 50


class TestWhatTheCountDoesNotHide:
    def test_an_install_with_no_registry_counts_its_legacy_tables(self):
        registry = UnreadableRegistry(
            counts={"kg_NodeEmbeddings": 9, "kg_EdgeEmbeddings": 4}
        )
        engine = engine_with(registry, embedding_dimension=DIM)

        status = engine.status()
        assert status.tables.node_embeddings == 9
        assert status.tables.edge_embeddings == 4

    def test_a_routed_table_the_database_has_lost_is_reported(self):
        """The registry row outlives a dropped table, so the sum is short. Saying so
        is the difference between a count an operator can act on and a wrong one."""
        missing = route_table_name(GRAPH_A, "m")
        registry = FakeRegistry(
            rows=[_row(missing, GRAPH_A, "node")],
            counts={"kg_NodeEmbeddings": 3, missing: None},
        )
        engine = engine_with(registry, embedding_dimension=DIM)

        status = engine.status()
        assert status.tables.node_embeddings == 3
        assert any(missing in err for err in status.errors), status.errors
