"""Spec 230 T035 — edge vectors are written and read through their route (FR-006).

T034 gave an edge route a name, a table and a registry row. This is the half that
matters to a caller: the writers and readers have to actually go there.

Before 4.0.0 every edge vector in the namespace lived in one `kg_EdgeEmbeddings`
keyed `(s, p, o_id)`, so two graphs asserting the same triple shared one row — the
second write silently replaced the first, at one declared width, and
`edge_vector_search` returned both graphs' edges to either caller. Four claims fix
that, and each one is a separate way to get it wrong:

1. A write goes into the pair's routed table, creating it on first use, with
   `graph_id` bound rather than defaulted.
2. A write deletes only its own graph's row for the triple. An unscoped delete is
   the old collision wearing new column names.
3. A read selects from the pair's routed table under a graph predicate, and a pair
   with no route reads nothing — not the default table, and not another model's.
4. A bulk embed selects the graph's own edges and asks the route, not
   `kg_EdgeEmbeddings`, which edges it has already done.

Statements, not a live IRIS: the fake registry serves no vector rows, so what is
asserted here is which table each statement names and what it binds. The live
half — that the widths are enforced and the answers separate — is
`tests/e2e/test_230_retrieval_scope.py`.
"""

import pytest

from iris_vector_graph.routing import edge_route_table_name, route_table_name
from tests.unit.route_fakes_227 import FakeRegistry, UnreadableRegistry, engine_with

DIM = 4
VEC = [0.1, 0.2, 0.3, 0.4]
S, P, O = "ivg230:s", "ivg230:p", "ivg230:o"


def _edge_row(graph, model, dimension=DIM, **over):
    row = {
        "table_name": edge_route_table_name(graph, model),
        "graph_id": graph,
        "mechanism": "iris-embedding-config" if model else None,
        "model_key": model,
        "declared_config": None,
        "dimension": dimension,
        "dtype": "DOUBLE",
        "index_state": "present",
        "index_error": None,
        "kind": "edge",
    }
    row.update(over)
    return row


def _vector_inserts(registry):
    return [
        (sql, params)
        for sql, params in registry.statements
        if sql.startswith("INSERT INTO") and "TO_VECTOR" in sql
    ]


def _naming(registry, table):
    """Every statement that names `table` — reads and writes alike."""
    return [
        (sql, params)
        for sql, params in registry.statements
        if f".{table} " in f"{sql} " or f".{table}(" in sql
    ]


# ------------------------------------------------------------------- writing


class TestAnEdgeWriteGoesIntoItsRoute:
    def test_a_first_write_creates_the_edge_route(self):
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)

        assert (
            engine.store_edge_embedding(S, P, O, VEC, graph="A", model_key="m-a")
            is True
        )

        assert registry.created_tables == [edge_route_table_name("A", "m-a")]
        assert _naming(registry, registry.created_tables[0])
        assert not _vector_inserts_into(registry, "kg_EdgeEmbeddings")

    def test_the_insert_carries_the_triple_and_the_graph(self):
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)
        engine.store_edge_embedding(S, P, O, VEC, graph="A", model_key="m-a")

        sql, params = _vector_inserts(registry)[0]
        for column in ("graph_id", "s", "p", "o_id"):
            assert column in sql, sql
        assert params[:4] == ["A", S, P, O], params

    def test_an_edge_write_never_lands_in_the_node_route(self):
        """One table cannot answer both: the node route is keyed `(graph_id,
        node_id)` and has a foreign key to `nodes`."""
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)
        engine.store_edge_embedding(S, P, O, VEC, graph="A", model_key="m-a")

        assert route_table_name("A", "m-a") not in registry.created_tables
        for sql, _ in _vector_inserts(registry):
            assert route_table_name("A", "m-a") not in sql

    def test_the_route_records_itself_as_an_edge_route(self):
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)
        engine.store_edge_embedding(S, P, O, VEC, graph="A", model_key="m-a")

        row = registry.row_for(edge_route_table_name("A", "m-a"), "A")
        assert row is not None, registry.rows
        assert row["kind"] == "edge"
        assert row["dimension"] == DIM

    def test_two_graphs_asserting_the_same_triple_get_two_tables(self):
        """The collision this spec exists to end. One row per triple per graph."""
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)

        engine.store_edge_embedding(S, P, O, VEC, graph="A", model_key="m")
        engine.store_edge_embedding(S, P, O, [0.5, 0.6, 0.7], graph="B", model_key="m")

        assert len(set(registry.created_tables)) == 2
        widths = {
            sql.split("VECTOR(DOUBLE, ")[1].split(")")[0]
            for sql, _ in registry.statements_matching("CREATE TABLE")
        }
        assert widths == {"4", "3"}, "each graph's edge route declares its own width"

    def test_the_delete_before_the_insert_is_scoped_to_the_graph(self):
        """An unscoped `DELETE ... WHERE s=? AND p=? AND o_id=?` is the old
        namespace-wide overwrite: graph B's write erases graph A's vector."""
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)
        engine.store_edge_embedding(S, P, O, VEC, graph="A", model_key="m-a")

        deletes = registry.statements_matching("DELETE FROM")
        assert deletes, registry.statements
        sql, params = deletes[0]
        assert "graph_id" in sql, sql
        assert "A" in params, params


class TestTheLegacyEdgeTableIsStillARoute:
    def test_an_unreadable_registry_writes_where_320_wrote(self):
        """A database with no registry cannot route, and the only honest fallback
        is 3.2.0's own table. Concluding "unrouted, so refuse" would break every
        install that has not migrated."""
        registry = UnreadableRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)

        assert engine.store_edge_embedding(S, P, O, VEC) is True
        assert _vector_inserts_into(registry, "kg_EdgeEmbeddings")
        assert registry.created_tables == []

    def test_the_legacy_write_still_binds_the_default_graph(self):
        """`kg_EdgeEmbeddings` gains `graph_id` in 4.0.0, so even the fallback row
        names its graph rather than leaning on a nullable column's default."""
        registry = UnreadableRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)
        engine.store_edge_embedding(S, P, O, VEC)

        sql, params = _vector_inserts(registry)[0]
        assert "graph_id" in sql, sql
        assert params[0] == "", params


class TestBatches:
    def test_a_batch_lands_in_one_edge_route(self):
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)
        items = [
            {"s": S, "p": P, "o_id": f"{O}{i}", "embedding": VEC} for i in range(3)
        ]

        assert engine.store_edge_embeddings(items, graph="A", model_key="m-a") is True

        assert registry.created_tables == [edge_route_table_name("A", "m-a")]
        assert len(_vector_inserts(registry)) == 3

    def test_a_mixed_width_batch_writes_nothing_and_creates_nothing(self):
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)
        items = [
            {"s": S, "p": P, "o_id": O, "embedding": VEC},
            {"s": S, "p": P, "o_id": f"{O}2", "embedding": [0.1] * 5},
        ]

        with pytest.raises(ValueError) as exc:
            engine.store_edge_embeddings(items, graph="A", model_key="m-a")

        assert "4" in str(exc.value) and "5" in str(exc.value)
        assert registry.created_tables == []
        assert _vector_inserts(registry) == []


# ------------------------------------------------------------------- reading


class TestAnEdgeSearchReadsItsRoute:
    def test_the_search_selects_from_the_pairs_routed_table(self):
        table = edge_route_table_name("A", "m-a")
        registry = FakeRegistry(rows=[_edge_row("A", "m-a")], tables=[table])
        engine = engine_with(registry, embedding_dimension=DIM)

        engine.edge_vector_search(VEC, top_k=5, graph="A", model_key="m-a")

        searches = [
            sql for sql, _ in registry.statements if "VECTOR_COSINE" in sql
        ]
        assert searches, registry.statements
        assert table in searches[0], searches[0]
        assert "kg_EdgeEmbeddings" not in searches[0], searches[0]

    def test_the_search_carries_a_graph_predicate(self):
        """A routed table holds one graph today, but a migrated one holds the rows
        the migration placed there, and the predicate is what makes the answer
        true of the graph rather than of the table."""
        table = edge_route_table_name("A", "m-a")
        registry = FakeRegistry(rows=[_edge_row("A", "m-a")], tables=[table])
        engine = engine_with(registry, embedding_dimension=DIM)

        engine.edge_vector_search(VEC, top_k=5, graph="A", model_key="m-a")

        sql, params = next(
            (s, p) for s, p in registry.statements if "VECTOR_COSINE" in s
        )
        assert "graph_id" in sql, sql
        assert "A" in params, params

    def test_a_pair_with_no_edge_route_reads_nothing(self):
        """Not the default table and not another model's. A substituted route
        returns real vectors from the wrong space, which score and rank exactly
        like an answer."""
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)

        assert engine.edge_vector_search(VEC, top_k=5, graph="A", model_key="m-a") == []

        assert not [sql for sql, _ in registry.statements if "VECTOR_COSINE" in sql]
        assert registry.created_tables == [], "a read must not create a route"

    def test_an_unreadable_registry_reads_where_320_read(self):
        registry = UnreadableRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)

        engine.edge_vector_search(VEC, top_k=5)

        searches = [sql for sql, _ in registry.statements if "VECTOR_COSINE" in sql]
        assert searches, registry.statements
        assert "kg_EdgeEmbeddings" in searches[0], searches[0]


class TestBulkEdgeEmbedding:
    def test_the_edge_selection_is_scoped_to_the_graph(self):
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)

        engine.embed_edges(text_fn=lambda s, p, o: f"{s} {p} {o}", graph="A")

        reads = [sql for sql, _ in registry.statements if "rdf_edges" in sql]
        assert reads, registry.statements
        assert "graph_id" in reads[0], reads[0]
        assert "'A'" in reads[0], reads[0]

    def test_the_already_embedded_probe_asks_the_route(self):
        """Asking `kg_EdgeEmbeddings` reports graph A's edges as done on the
        strength of the default graph's rows, and the backfill skips them."""
        table = edge_route_table_name("A", "m-a")
        registry = FakeRegistry(rows=[_edge_row("A", "m-a")], tables=[table])
        engine = engine_with(registry, embedding_dimension=DIM)

        engine.embed_edges(
            text_fn=lambda s, p, o: f"{s} {p} {o}", graph="A", model_key="m-a"
        )

        probes = [
            sql
            for sql, _ in registry.statements
            if sql.startswith("SELECT s, p, o_id FROM") and "rdf_edges" not in sql
        ]
        assert probes, registry.statements
        assert table in probes[0], probes[0]
        assert "kg_EdgeEmbeddings" not in probes[0], probes[0]

    def test_an_unrouted_pair_has_embedded_nothing_yet(self):
        """No route means no edge vectors, so there is no probe to run and every
        selected edge is still to do."""
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)

        engine.embed_edges(
            text_fn=lambda s, p, o: f"{s} {p} {o}", graph="A", model_key="m-a"
        )

        probes = [
            sql
            for sql, _ in registry.statements
            if sql.startswith("SELECT s, p, o_id FROM") and "rdf_edges" not in sql
        ]
        assert probes == [], probes


def _vector_inserts_into(registry, table):
    return [
        (sql, params)
        for sql, params in _vector_inserts(registry)
        if f".{table} " in f"{sql} " or f".{table}(" in sql
    ]
