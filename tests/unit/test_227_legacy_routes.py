"""`kg_NodeEmbeddings` is a route, under a name no hash produces (T046, FR-015).

An upgraded install has vectors in `kg_NodeEmbeddings` and a registry row adopted for
it at `graph_id = ''`. That row *is* the default pair's route: routing has to resolve
to the table the data is in, not to the table a hash would have named. Anything else
silently strands every vector written before the upgrade.

Two rows answer the default pair on such an install — `kg_NodeEmbeddings` and
`kg_NodeEmbeddings_optimized`, both adopted at `('', NULL)` — so the tie has to break
deterministically, and toward the table writes actually went.
"""

from iris_vector_graph.constants import VECTOR_TABLE_NAMES
from iris_vector_graph.routing import route_table_name
from tests.unit.route_fakes_227 import FakeRegistry, engine_with

DIM = 4
VEC = [0.1, 0.2, 0.3, 0.4]


def _adopted(table_name, dimension=DIM):
    return {
        "table_name": table_name,
        "graph_id": "",
        "mechanism": None,
        "model_key": None,
        "declared_config": None,
        "dimension": dimension,
        "dtype": "DOUBLE",
        "index_state": None,
        "index_error": None,
    }


class TestAdoptionRegistersTheLegacyTables:
    def _engine(self):
        registry = FakeRegistry(
            tables=list(VECTOR_TABLE_NAMES),
            dimension_for={name: DIM for name in VECTOR_TABLE_NAMES},
        )
        return registry, engine_with(registry, embedding_dimension=DIM)

    def test_every_legacy_table_gets_a_row_at_the_default_graph(self):
        registry, engine = self._engine()

        engine.adopt_embedding_identities()

        for name in VECTOR_TABLE_NAMES:
            row = registry.row_for(name, "")
            assert row is not None, f"{name} was not registered"
            assert row["graph_id"] == ""

    def test_the_model_is_recorded_as_unknown_rather_than_guessed(self):
        registry, engine = self._engine()
        engine.adopt_embedding_identities()

        assert registry.row_for("kg_NodeEmbeddings", "")["model_key"] is None

    def test_the_width_comes_from_the_column_declaration(self):
        registry, engine = self._engine()
        engine.adopt_embedding_identities()

        assert registry.row_for("kg_NodeEmbeddings", "")["dimension"] == DIM

    def test_adoption_run_twice_leaves_one_row_per_table(self):
        registry, engine = self._engine()
        engine.adopt_embedding_identities()
        before = len(registry.rows)

        outcomes = engine.adopt_embedding_identities()

        assert len(registry.rows) == before
        assert set(outcomes.values()) == {"already_recorded"}

    def test_the_adopted_row_is_immediately_a_usable_route(self):
        registry, engine = self._engine()
        engine.adopt_embedding_identities()

        route = engine.resolve_route()

        assert route is not None
        assert route.table_name == "kg_NodeEmbeddings"
        assert route.dimension == DIM


class TestTheDefaultPairResolvesToTheTableTheDataIsIn:
    def test_the_optimized_table_does_not_win_the_tie(self):
        registry = FakeRegistry(
            rows=[_adopted(name) for name in VECTOR_TABLE_NAMES],
            tables=list(VECTOR_TABLE_NAMES),
        )
        engine = engine_with(registry, embedding_dimension=DIM)

        assert engine.resolve_route().table_name == "kg_NodeEmbeddings"

    def test_the_row_order_does_not_decide_it(self):
        registry = FakeRegistry(
            rows=[_adopted(name) for name in reversed(VECTOR_TABLE_NAMES)],
            tables=list(VECTOR_TABLE_NAMES),
        )
        engine = engine_with(registry, embedding_dimension=DIM)

        assert engine.resolve_route().table_name == "kg_NodeEmbeddings"

    def test_a_legacy_route_claims_no_index_it_was_never_asked_about(self):
        """`index_state` is `None` on an adopted row, and `None` is not "no index".

        Spec 226 stopped `SHOW INDEXES` synthesizing an HNSW row that
        `%Dictionary.CompiledIndex` does not have. The same honesty applies here: an
        adopted route has never been asked, and saying `refused` would be a claim about
        a build nobody ran.
        """
        registry = FakeRegistry(
            rows=[_adopted("kg_NodeEmbeddings")], tables=["kg_NodeEmbeddings"]
        )
        engine = engine_with(registry, embedding_dimension=DIM)

        route = engine.resolve_route()
        assert route.index_state is None
        assert route.is_indexed is False

    def test_a_write_to_the_default_graph_never_creates_a_hashed_table(self):
        registry = FakeRegistry(
            rows=[_adopted("kg_NodeEmbeddings")], tables=["kg_NodeEmbeddings"]
        )
        engine = engine_with(registry, embedding_dimension=DIM)

        engine.store_embedding("n1", VEC)

        assert registry.created_tables == []
        hashed = route_table_name("", None)
        assert not any(hashed in sql for sql, _ in registry.statements)


class TestAnEmptyRegistryStillAnswersTheDefaultPair:
    """A readable registry with no row for `('', None)` is not evidence of no data.

    A namespace built by running the DDL without `initialize_schema`, or one whose
    registry row was deleted, still has 3.2.0's vectors in `kg_NodeEmbeddings`. The
    default pair is 3.2.0's own world — default graph, no model declared — so that
    table is its route whether or not a row says so (FR-015). Concluding "unrouted"
    would report the data as absent and then write the next vector into a hashed
    table beside it.
    """

    def test_a_default_read_still_reads_the_legacy_table(self):
        registry = FakeRegistry(tables=["kg_NodeEmbeddings"])
        engine = engine_with(registry, embedding_dimension=DIM)

        engine.kg_KNN_VEC("[0.1,0.2,0.3,0.4]", k=5)

        searched = [
            sql
            for sql, _ in registry.statements
            if "VECTOR_COSINE" in sql or sql.startswith("SELECT emb FROM")
        ]
        assert searched and all("kg_NodeEmbeddings" in sql for sql in searched)

    def test_a_default_write_still_writes_the_legacy_table(self):
        registry = FakeRegistry(tables=["kg_NodeEmbeddings"])
        engine = engine_with(registry, embedding_dimension=DIM)

        assert engine.store_embedding("n1", VEC) is True

        inserted = [
            sql
            for sql, _ in registry.statements
            if sql.startswith("INSERT INTO") and "TO_VECTOR" in sql
        ]
        assert inserted and all("kg_NodeEmbeddings" in sql for sql in inserted)
        assert registry.created_tables == []

    def test_a_named_graph_gets_no_such_benefit(self):
        registry = FakeRegistry(tables=["kg_NodeEmbeddings"])
        engine = engine_with(registry, embedding_dimension=DIM)

        assert engine.kg_KNN_VEC("[0.1,0.2,0.3,0.4]", k=5, graph="A") == []

    def test_a_declared_model_in_the_default_graph_gets_no_such_benefit(self):
        registry = FakeRegistry(tables=["kg_NodeEmbeddings"])
        engine = engine_with(registry, embedding_dimension=DIM)

        engine.store_embedding("n1", VEC, model_key="m-a")

        assert registry.created_tables and registry.created_tables[0].startswith("kg_emb_")


class TestTheLegacyRouteIsNotEveryGraphsRoute:
    def test_another_graph_does_not_inherit_it(self):
        """The one thing adoption must not do is make `kg_NodeEmbeddings` answer for
        every graph. It holds the default graph's vectors; graph A's are elsewhere or
        nowhere, and "nowhere" is the honest answer (FR-013)."""
        registry = FakeRegistry(
            rows=[_adopted("kg_NodeEmbeddings")], tables=["kg_NodeEmbeddings"]
        )
        engine = engine_with(registry, embedding_dimension=DIM)

        assert engine.resolve_route("A") is None
        assert engine.kg_KNN_VEC("[0.1,0.2,0.3,0.4]", k=5, graph="A") == []

    def test_another_model_in_the_default_graph_does_not_inherit_it(self):
        registry = FakeRegistry(
            rows=[_adopted("kg_NodeEmbeddings")], tables=["kg_NodeEmbeddings"]
        )
        engine = engine_with(registry, embedding_dimension=DIM)

        assert engine.resolve_route("", "m-b") is None
