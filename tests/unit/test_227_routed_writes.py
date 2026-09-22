"""A write lands in its route, not in a table name compiled into the source (T044).

Spec 227 FR-002, FR-013. The three things a write has to get right, in order:

1. Resolve the `(graph, model)` pair to a physical table, creating it if this is the
   pair's first write.
2. Check the offered identity against *that* table's registry row.
3. Only then INSERT, into that table, with the graph bound.

Getting the order wrong is not a style question. Enforcing before resolving checks the
wrong row — `kg_NodeEmbeddings`' row, for a write that is going somewhere else — so a
384-wide write into a 768-wide route passes the check and is refused by IRIS at INSERT
with SQLCODE -104, which says nothing about models.
"""

import pytest

from iris_vector_graph.exceptions import EmbeddingIdentityConflict
from iris_vector_graph.routing import route_table_name
from tests.unit.route_fakes_227 import FakeRegistry, UnreadableRegistry, engine_with

DIM = 4
VEC = [0.1, 0.2, 0.3, 0.4]


def _routed_row(graph, model, dimension=DIM, **over):
    row = {
        "table_name": route_table_name(graph, model),
        "graph_id": graph,
        "mechanism": "iris-embedding-config" if model else None,
        "model_key": model,
        "declared_config": None,
        "dimension": dimension,
        "dtype": "DOUBLE",
        "index_state": "present",
        "index_error": None,
    }
    row.update(over)
    return row


def _legacy_row(dimension=DIM):
    return {
        "table_name": "kg_NodeEmbeddings",
        "graph_id": "",
        "mechanism": None,
        "model_key": None,
        "declared_config": None,
        "dimension": dimension,
        "dtype": "DOUBLE",
        "index_state": None,
        "index_error": None,
    }


def _inserts(registry, table):
    return [
        (sql, params)
        for sql, params in registry.statements
        if sql.startswith("INSERT INTO") and f".{table} " in f"{sql} "
    ]


def _vector_inserts(registry):
    """Every INSERT that carries a vector, whatever table it names."""
    return [
        (sql, params)
        for sql, params in registry.statements
        if sql.startswith("INSERT INTO") and "TO_VECTOR" in sql
    ]


class TestFirstWriteCreatesTheRoute:
    def test_a_graph_with_no_route_gets_one_and_the_vector_goes_into_it(self):
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)

        assert engine.store_embedding("n1", VEC, graph="A", model_key="m-a") is True

        assert len(registry.created_tables) == 1
        table = registry.created_tables[0]
        assert table.startswith("kg_emb_")
        assert _inserts(registry, table), "the vector did not go into the route"
        assert not _inserts(registry, "kg_NodeEmbeddings")

    def test_the_insert_binds_the_graph_rather_than_relying_on_the_default(self):
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)
        engine.store_embedding("n1", VEC, graph="A", model_key="m-a")

        sql, params = _vector_inserts(registry)[0]
        assert "graph_id" in sql
        assert "A" in params

    def test_the_route_records_the_model_and_the_width_it_was_created_at(self):
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)
        engine.store_embedding("n1", VEC, graph="A", model_key="m-a")

        row = registry.row_for(registry.created_tables[0], "A")
        assert row["model_key"] == "m-a"
        assert row["dimension"] == DIM

    def test_a_second_write_into_the_same_pair_creates_nothing_further(self):
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)
        engine.store_embedding("n1", VEC, graph="A", model_key="m-a")
        engine.store_embedding("n2", VEC, graph="A", model_key="m-a")

        assert len(registry.created_tables) == 1
        assert len(registry.rows) == 1
        assert len(_inserts(registry, registry.created_tables[0])) == 2

    def test_two_models_in_one_graph_get_two_tables(self):
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)
        engine.store_embedding("n1", VEC, graph="A", model_key="m-a")
        engine.store_embedding("n1", VEC, graph="A", model_key="m-b")

        assert len(registry.created_tables) == 2
        assert len(set(registry.created_tables)) == 2

    def test_each_graph_declares_its_own_width(self):
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)
        engine.store_embedding("n1", VEC, graph="A", model_key="m")
        engine.store_embedding("n1", [0.5, 0.6, 0.7], graph="B", model_key="m")

        widths = {
            sql.split("VECTOR(DOUBLE, ")[1].split(")")[0]
            for sql, _ in registry.statements_matching("CREATE TABLE")
        }
        assert widths == {"4", "3"}


class TestAnExistingRouteIsTheAuthority:
    def test_a_wrong_width_write_is_refused_and_names_the_graph(self):
        table = route_table_name("A", "m-a")
        registry = FakeRegistry(rows=[_routed_row("A", "m-a")], tables=[table])
        engine = engine_with(registry, embedding_dimension=DIM)

        with pytest.raises(EmbeddingIdentityConflict) as exc:
            engine.store_embedding("n1", [0.1] * 5, graph="A", model_key="m-a")

        assert "graph 'A'" in str(exc.value)

    def test_a_refused_write_writes_nothing_and_creates_nothing(self):
        table = route_table_name("A", "m-a")
        registry = FakeRegistry(rows=[_routed_row("A", "m-a")], tables=[table])
        engine = engine_with(registry, embedding_dimension=DIM)

        with pytest.raises(EmbeddingIdentityConflict):
            engine.store_embedding("n1", [0.1] * 5, graph="A", model_key="m-a")

        assert _vector_inserts(registry) == []
        assert registry.created_tables == []

    def test_the_width_checked_is_the_routes_own_not_the_engines(self):
        """An engine configured at 768 writing 384 into a 384 route is fine.

        The engine-wide `embedding_dimension` is a default for reads and for creating a
        route, not a claim about every route in the namespace — spec 227 exists because
        one namespace now holds several widths.
        """
        table = route_table_name("A", "m-a")
        registry = FakeRegistry(
            rows=[_routed_row("A", "m-a", dimension=DIM)], tables=[table]
        )
        engine = engine_with(registry, embedding_dimension=768)

        assert engine.store_embedding("n1", VEC, graph="A", model_key="m-a") is True
        assert _inserts(registry, table)


class TestTheLegacyTableIsStillARoute:
    def test_the_default_graph_writes_where_320_wrote(self):
        registry = FakeRegistry(rows=[_legacy_row()], tables=["kg_NodeEmbeddings"])
        engine = engine_with(registry, embedding_dimension=DIM)

        assert engine.store_embedding("n1", VEC) is True
        assert _inserts(registry, "kg_NodeEmbeddings")
        assert registry.created_tables == []

    def test_an_unreadable_registry_writes_where_320_wrote(self):
        """A pre-227 database is not a database in which nothing is routed.

        Reading the registry is how routing works, so a database without one cannot
        route — and the only honest thing left is 3.2.0's behaviour. Concluding "no
        route" instead would refuse every write on every installation that has not run
        the migration yet.
        """
        registry = UnreadableRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)

        assert engine.store_embedding("n1", VEC) is True
        assert _inserts(registry, "kg_NodeEmbeddings")
        assert registry.created_tables == []


class TestBatches:
    def test_a_batch_lands_in_one_route(self):
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)
        items = [{"node_id": f"n{i}", "embedding": VEC} for i in range(3)]

        assert engine.store_embeddings(items, graph="A", model_key="m-a") is True

        assert len(registry.created_tables) == 1
        assert len(_inserts(registry, registry.created_tables[0])) == 3

    def test_every_row_in_the_batch_binds_the_batchs_graph(self):
        """A per-item `"graph"` key is ignored, not honoured for some rows.

        A route is one physical table at one declared width, so a batch spanning graphs
        could only be honoured by splitting it across tables — which loses the single
        transaction that is the reason to batch. The graph is the batch's, and an item
        that disagrees does not get its own.
        """
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)
        items = [
            {"node_id": "n1", "embedding": VEC, "graph": "B"},
            {"node_id": "n2", "embedding": VEC},
        ]

        engine.store_embeddings(items, graph="A", model_key="m-a")

        for _, params in _vector_inserts(registry):
            assert "A" in params
            assert "B" not in params
        assert len(registry.created_tables) == 1

    def test_a_mixed_width_batch_writes_nothing_and_creates_nothing(self):
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)
        items = [
            {"node_id": "n1", "embedding": VEC},
            {"node_id": "n2", "embedding": [0.1] * 5},
        ]

        with pytest.raises(ValueError) as exc:
            engine.store_embeddings(items, graph="A", model_key="m-a")

        assert "4" in str(exc.value) and "5" in str(exc.value)
        assert registry.created_tables == []
        assert _vector_inserts(registry) == []


class TestBackfillReadsTheRoute:
    def test_unembedded_nodes_are_missing_from_the_route_not_from_the_legacy_table(self):
        table = route_table_name("A", "m-a")
        registry = FakeRegistry(rows=[_routed_row("A", "m-a")], tables=[table])
        engine = engine_with(registry, embedding_dimension=DIM)

        engine.get_unembedded_nodes(graph="A", model_key="m-a")

        joined = " ".join(sql for sql, _ in registry.statements if "nodes" in sql)
        assert table in joined
        assert "kg_NodeEmbeddings" not in joined

    def test_an_unrouted_graph_has_no_embedded_nodes_at_all(self):
        """No route means no vectors, so every node in the graph is unembedded.

        Answered without a join, because there is no table to join to. Joining against
        `kg_NodeEmbeddings` instead would report graph A's nodes as embedded on the
        strength of the default graph's vectors.
        """
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)

        engine.get_unembedded_nodes(graph="A", model_key="m-a")

        node_reads = [sql for sql, _ in registry.statements if " FROM Graph_KG.nodes" in sql]
        assert node_reads, "the nodes were never read"
        assert all("JOIN" not in sql for sql in node_reads)
        assert registry.created_tables == []
