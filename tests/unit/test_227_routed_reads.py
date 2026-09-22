"""A search reads the route, and an unrouted pair returns nothing (T045).

Spec 227 FR-013. The hard half is the miss. A search of a pair with no route has no
table to read, and every available substitute — the default graph's table, another
model's, the legacy table — returns real vectors from the wrong space. Those score,
rank, and come back looking exactly like an answer. So a miss is `[]`.

The exception is a database with no registry at all, which cannot route and is not
making a claim about routes: there, 3.2.0's table is still the right answer.
"""

import pytest

from iris_vector_graph.routing import route_table_name
from tests.unit.route_fakes_227 import (
    FakeRegistry,
    GarbledRegistry,
    UnreadableRegistry,
    engine_with,
)


@pytest.fixture
def no_embedded(monkeypatch):
    """Force the fallbacks onto the DB-API cursor, where the fake can see the SQL.

    The first thing the Python fallback tries is embedded SQL, which outside an IRIS
    process answers with nothing rather than raising — so without this the fallback
    looks like it ran and issued no statement at all.
    """
    import iris_vector_graph.embedded as embedded

    def _refuse(sql, params=None):
        raise RuntimeError("no embedded IRIS in this process")

    monkeypatch.setattr(embedded, "_sql_statement_execute", _refuse)

DIM = 4
QUERY = "[0.1,0.2,0.3,0.4]"


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


def _legacy_row():
    return {
        "table_name": "kg_NodeEmbeddings",
        "graph_id": "",
        "mechanism": None,
        "model_key": None,
        "declared_config": None,
        "dimension": DIM,
        "dtype": "DOUBLE",
        "index_state": None,
        "index_error": None,
    }


def _searches(registry):
    """Every statement that scores or reads vectors."""
    return [
        sql
        for sql, _ in registry.statements
        if "VECTOR_COSINE" in sql or sql.startswith("SELECT emb FROM")
    ]


def _routed_engine(graph="A", model="m-a", **kwargs):
    table = route_table_name(graph, model)
    registry = FakeRegistry(rows=[_routed_row(graph, model)], tables=[table])
    return registry, engine_with(registry, embedding_dimension=DIM, **kwargs), table


class TestKnnReadsTheRoute:
    def test_the_search_names_the_routed_table(self):
        registry, engine, table = _routed_engine()

        assert engine.kg_KNN_VEC(QUERY, k=5, graph="A", model_key="m-a") == []

        searched = _searches(registry)
        assert searched, "no search was issued"
        assert all(table in sql for sql in searched)
        assert not any("kg_NodeEmbeddings" in sql for sql in searched)

    def test_the_search_still_carries_the_graph_predicate(self):
        """A routed table holds one graph, and the predicate stays anyway (FR-037).

        The scope is not load-bearing there — it is the same guarantee stated twice —
        but a statement that is correct only because of what it was routed to stops
        being correct the moment a migration copies rows into the table.
        """
        registry, engine, table = _routed_engine()
        engine.kg_KNN_VEC(QUERY, k=5, graph="A", model_key="m-a")

        scored = [sql for sql, _ in registry.statements if "VECTOR_COSINE" in sql]
        assert all("graph_id" in sql for sql in scored)

    def test_the_seed_vector_lookup_reads_the_same_route(self):
        """A node ID as the query is a read of that node's vector, in that route.

        Reading the seed from `kg_NodeEmbeddings` while searching the route means
        searching graph A with whatever vector another table happened to hold for that
        ID — a wrong answer with no error anywhere.
        """
        registry, engine, table = _routed_engine()

        assert engine.kg_KNN_VEC("node:1", k=5, graph="A", model_key="m-a") == []

        seed = [sql for sql, _ in registry.statements if sql.startswith("SELECT emb FROM")]
        assert seed and all(table in sql for sql in seed)

    def test_a_label_filtered_search_reads_the_route_too(self):
        registry, engine, table = _routed_engine()
        engine.kg_KNN_VEC(QUERY, k=5, label_filter="Patient", graph="A", model_key="m-a")

        scored = [sql for sql, _ in registry.statements if "VECTOR_COSINE" in sql]
        assert scored and all(table in sql for sql in scored)

    def test_another_models_route_is_not_searched(self):
        a_table = route_table_name("A", "m-a")
        b_table = route_table_name("A", "m-b")
        registry = FakeRegistry(
            rows=[_routed_row("A", "m-a"), _routed_row("A", "m-b")],
            tables=[a_table, b_table],
        )
        engine = engine_with(registry, embedding_dimension=DIM)

        engine.kg_KNN_VEC(QUERY, k=5, graph="A", model_key="m-b")

        searched = _searches(registry)
        assert searched and all(b_table in sql for sql in searched)
        assert not any(a_table in sql for sql in searched)


class TestAMissIsAMiss:
    def test_an_unrouted_pair_returns_nothing(self):
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)

        assert engine.kg_KNN_VEC(QUERY, k=5, graph="A", model_key="m-a") == []

    def test_an_unrouted_pair_searches_nothing(self):
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)
        engine.kg_KNN_VEC(QUERY, k=5, graph="A", model_key="m-a")

        assert _searches(registry) == []

    def test_a_graph_with_no_route_does_not_read_another_graphs(self):
        registry = FakeRegistry(
            rows=[_routed_row("B", "m-a")], tables=[route_table_name("B", "m-a")]
        )
        engine = engine_with(registry, embedding_dimension=DIM)

        assert engine.kg_KNN_VEC(QUERY, k=5, graph="A", model_key="m-a") == []
        assert _searches(registry) == []

    def test_both_fallbacks_also_return_nothing_for_an_unrouted_pair(self):
        registry = FakeRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)

        assert engine._kg_KNN_VEC_python_optimized(
            QUERY, 5, None, graph="A", model_key="m-a"
        ) == []
        assert engine._kg_KNN_VEC_client_side(
            QUERY, 5, None, graph="A", model_key="m-a"
        ) == []
        assert _searches(registry) == []


class TestTheLegacyTableIsStillARoute:
    def test_the_default_graph_reads_where_320_read(self):
        registry = FakeRegistry(rows=[_legacy_row()], tables=["kg_NodeEmbeddings"])
        engine = engine_with(registry, embedding_dimension=DIM)

        engine.kg_KNN_VEC(QUERY, k=5)

        searched = _searches(registry)
        assert searched and all("kg_NodeEmbeddings" in sql for sql in searched)
        assert not any("kg_emb_" in sql for sql in searched)

    def test_an_unreadable_registry_reads_where_320_read(self):
        registry = UnreadableRegistry()
        engine = engine_with(registry, embedding_dimension=DIM)

        engine.kg_KNN_VEC(QUERY, k=5)

        searched = _searches(registry)
        assert searched and all("kg_NodeEmbeddings" in sql for sql in searched)


class TestAnAnswerThatIsNotARouteRowIsNotARoute:
    def test_a_short_answer_does_not_crash_the_read(self):
        """The route query asks for five columns; fewer means read something else.

        Unpacking it anyway raises `ValueError: not enough values to unpack` from
        inside a KNN — an error about tuple arity, for a database whose registry
        does not have the columns 227 added.
        """
        engine = engine_with(GarbledRegistry(), embedding_dimension=DIM)

        assert engine.kg_KNN_VEC(QUERY, k=5) == []

    def test_a_short_answer_reads_where_320_read(self):
        registry = GarbledRegistry(tables=["kg_NodeEmbeddings"])
        engine = engine_with(registry, embedding_dimension=DIM)

        engine.kg_KNN_VEC(QUERY, k=5)

        searched = _searches(registry)
        assert searched and all("kg_NodeEmbeddings" in sql for sql in searched)
        assert not any("kg_emb_" in sql for sql in searched)

    def test_a_short_answer_writes_where_320_wrote(self):
        registry = GarbledRegistry(tables=["kg_NodeEmbeddings"])
        engine = engine_with(registry, embedding_dimension=DIM)

        assert engine.store_embedding("n1", [0.1, 0.2, 0.3, 0.4]) is True

        inserted = [
            sql
            for sql, _ in registry.statements
            if sql.startswith("INSERT INTO") and "TO_VECTOR" in sql
        ]
        assert inserted and all("kg_NodeEmbeddings" in sql for sql in inserted)
        assert registry.created_tables == []


class TestTheFallbacksReadTheSameRoute:
    def test_the_python_fallback_reads_the_route(self, no_embedded):
        registry, engine, table = _routed_engine()

        engine._kg_KNN_VEC_python_optimized(QUERY, 5, None, graph="A", model_key="m-a")

        scored = [sql for sql, _ in registry.statements if "VECTOR_COSINE" in sql]
        assert scored and all(table in sql for sql in scored)

    def test_the_client_side_fallback_reads_the_route(self):
        registry, engine, table = _routed_engine()

        engine._kg_KNN_VEC_client_side(QUERY, 5, None, graph="A", model_key="m-a")

        reads = [sql for sql, _ in registry.statements if "n.emb" in sql]
        assert reads and all(table in sql for sql in reads)
        assert not any("kg_NodeEmbeddings" in sql for sql in reads)

    def test_a_failing_procedure_path_falls_back_into_the_same_route(self, no_embedded):
        """The fallback is a different code path to the same table.

        A fallback that widens is worse than one that fails: the caller gets an answer,
        and the only symptom is extra neighbours they have no reason to distrust.
        """
        registry, engine, table = _routed_engine()

        class _Failing:
            def __init__(self, inner):
                self._inner = inner
                self._scored = False

            def execute(self, sql, params=None):
                if "VECTOR_COSINE" in str(sql) and not self._scored:
                    self._scored = True
                    self._inner.execute(sql, params)
                    raise RuntimeError("[SQLCODE: <-400>] simulated procedure failure")
                return self._inner.execute(sql, params)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        real_cursor = engine.conn.cursor
        engine.conn.cursor = lambda: _Failing(real_cursor())

        assert engine.kg_KNN_VEC(QUERY, k=5, graph="A", model_key="m-a") == []

        scored = [sql for sql, _ in registry.statements if "VECTOR_COSINE" in sql]
        assert len(scored) >= 2, "the fallback never ran"
        assert all(table in sql for sql in scored)
