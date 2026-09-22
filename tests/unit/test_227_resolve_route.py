"""Spec 227 T037 — `resolve_route` reads the registry and never guesses (FR-011, FR-013).

Three claims, and each one is a way routing can go wrong quietly:

1. **The registry row is the authority, not the hash.** `route_table_name` says
   what a *new* route will be called. It says nothing about what an existing one
   is called: IRIS derives the class name from the table name by stripping
   underscores and de-duplicating collisions with a numeric suffix, and spec 227
   FR-015 keeps `kg_NodeEmbeddings` as a route under a name the hash would never
   produce. A resolver that recomputes the name finds nothing, creates a second
   table, and splits one graph's vectors across two.

2. **`create=False` creates nothing.** A read of an unrouted pair returns `None`.
   Not an empty table, not a default-graph fallback, and not the other model's
   route — a substituted route returns real vectors from the wrong space, which
   scores and ranks and looks like an answer.

3. **A route is never substituted.** The lookup names the graph *and* the model;
   matching on either alone is a cross-graph or cross-model read.

The fake registry enforces the primary key and refuses a duplicate `CREATE
TABLE`, so these are assertions about statements issued, not about a live IRIS.
The live half is `tests/e2e/test_227_two_models.py`.
"""

import pytest

from iris_vector_graph.constants import DEFAULT_GRAPH, ROUTE_TABLE_PREFIX
from iris_vector_graph.routing import route_table_name
from iris_vector_graph.security import validate_table_name
from tests.unit.route_fakes_227 import FakeRegistry, engine_with

GRAPH_A = "ivg227-route-a"
GRAPH_B = "ivg227-route-b"
MODEL_A = "model-a"
MODEL_B = "model-b"


def _route_row(table_name, graph_id, model_key, dimension=384, **extra):
    row = {
        "table_name": table_name,
        "graph_id": graph_id,
        "mechanism": None if model_key is None else "iris-embedding-config",
        "model_key": model_key,
        "declared_config": model_key,
        "dimension": dimension,
        "dtype": "DOUBLE",
        "index_state": None,
        "index_error": None,
    }
    row.update(extra)
    return row


# --------------------------------------------------------------------- reading


def test_an_unrouted_pair_resolves_to_nothing():
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=384)

    assert engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A) is None


def test_reading_an_unrouted_pair_creates_nothing():
    """The one that matters operationally: a KNN against a graph that was never
    embedded must not leave a table and a registry row behind."""
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=384)

    engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A)

    assert registry.created_tables == []
    assert registry.rows == []
    assert registry.statements_matching("CREATE TABLE") == []
    assert registry.statements_matching("INSERT INTO") == []


def test_the_registry_row_is_the_authority_not_the_hash():
    """The recorded name wins even when it is not the name the hash would give."""
    recorded = "kg_NodeEmbeddings"
    assert recorded != route_table_name(DEFAULT_GRAPH, None)

    registry = FakeRegistry(rows=[_route_row(recorded, DEFAULT_GRAPH, None, dimension=768)])
    engine = engine_with(registry, embedding_dimension=768)

    route = engine.resolve_route()

    assert route is not None
    assert route.table_name == recorded
    assert route.dimension == 768


def test_the_default_graph_is_the_empty_string_not_none():
    registry = FakeRegistry(rows=[_route_row("kg_NodeEmbeddings", DEFAULT_GRAPH, None)])
    engine = engine_with(registry, embedding_dimension=384)

    assert engine.resolve_route(graph=None).graph_id == DEFAULT_GRAPH


def test_another_graphs_route_is_not_substituted():
    registry = FakeRegistry(
        rows=[_route_row(route_table_name(GRAPH_B, MODEL_A), GRAPH_B, MODEL_A)]
    )
    engine = engine_with(registry, embedding_dimension=384)

    assert engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A) is None


def test_another_models_route_is_not_substituted():
    registry = FakeRegistry(
        rows=[_route_row(route_table_name(GRAPH_A, MODEL_A), GRAPH_A, MODEL_A)]
    )
    engine = engine_with(registry, embedding_dimension=384)

    assert engine.resolve_route(graph=GRAPH_A, model_key=MODEL_B) is None


def test_an_undeclared_model_does_not_match_a_declared_one():
    """Spec 226's undeclared identity is its own route, not a wildcard."""
    registry = FakeRegistry(
        rows=[_route_row(route_table_name(GRAPH_A, MODEL_A), GRAPH_A, MODEL_A)]
    )
    engine = engine_with(registry, embedding_dimension=384)

    assert engine.resolve_route(graph=GRAPH_A, model_key=None) is None


def test_the_lookup_names_both_the_graph_and_the_model():
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=384)

    engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A)

    lookups = registry.statements_matching("FROM Graph_KG.embedding_registry")
    assert lookups, "resolve_route did not read the registry at all"
    sql, params = lookups[0]
    assert "graph_id" in sql
    assert "model_key" in sql
    assert GRAPH_A in params, "the graph is bound, never interpolated (FR-032)"


def test_the_legacy_table_wins_when_two_rows_answer_the_same_pair():
    """A 3.2.0 install has an adopted row for both embedding tables at
    `(graph_id='', model_key=NULL)`. Both answer the default pair, so the choice
    has to be deterministic and it has to be the table the data is in (FR-015)."""
    registry = FakeRegistry(
        rows=[
            _route_row("kg_NodeEmbeddings_optimized", DEFAULT_GRAPH, None),
            _route_row("kg_NodeEmbeddings", DEFAULT_GRAPH, None),
        ]
    )
    engine = engine_with(registry, embedding_dimension=384)

    assert engine.resolve_route().table_name == "kg_NodeEmbeddings"


def test_the_route_reports_the_index_state_the_registry_recorded():
    registry = FakeRegistry(
        rows=[
            _route_row(
                route_table_name(GRAPH_A, MODEL_A),
                GRAPH_A,
                MODEL_A,
                index_state="refused",
                index_error="HNSW index requires a single integer identity",
            )
        ]
    )
    engine = engine_with(registry, embedding_dimension=384)

    route = engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A)

    assert route.index_state == "refused"
    assert "identity" in route.index_error


# -------------------------------------------------------------------- creating


def test_creating_a_route_issues_the_table_and_the_registry_row():
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=384)

    route = engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A, create=True)

    expected = route_table_name(GRAPH_A, MODEL_A)
    assert route.table_name == expected
    assert any(expected in name for name in registry.created_tables)
    assert registry.row_for(expected, GRAPH_A) is not None


def test_a_created_route_declares_the_width_it_was_asked_for():
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=384)

    route = engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A, create=True, dimension=768)

    assert route.dimension == 768
    ddl = registry.statements_matching("CREATE TABLE")[0][0]
    assert "VECTOR(DOUBLE, 768)" in ddl
    assert registry.row_for(route.table_name, GRAPH_A)["dimension"] == 768


def test_creating_a_route_that_already_exists_returns_it_and_creates_nothing():
    existing = route_table_name(GRAPH_A, MODEL_A)
    registry = FakeRegistry(rows=[_route_row(existing, GRAPH_A, MODEL_A, dimension=384)])
    engine = engine_with(registry, embedding_dimension=384)

    route = engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A, create=True)

    assert route.table_name == existing
    assert registry.created_tables == []
    assert len(registry.rows) == 1


def test_two_models_in_one_graph_get_two_tables():
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=384)

    a = engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A, create=True, dimension=384)
    b = engine.resolve_route(graph=GRAPH_A, model_key=MODEL_B, create=True, dimension=768)

    assert a.table_name != b.table_name
    assert {a.dimension, b.dimension} == {384, 768}
    assert len(registry.created_tables) == 2


def test_one_model_in_two_graphs_gets_two_tables():
    """FR-037: a routed table holds one graph's vectors, so a scoped search over
    it does not need a graph predicate to be correct."""
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=384)

    a = engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A, create=True)
    b = engine.resolve_route(graph=GRAPH_B, model_key=MODEL_A, create=True)

    assert a.table_name != b.table_name


def test_a_refused_index_is_recorded_and_the_route_still_works():
    """FR-019: a build that will not index is not a caller error. The route
    exists, the search scans, and the refusal is written down verbatim."""
    refusal = "ERROR #5002: HNSW index not supported on this build"
    registry = FakeRegistry(index_error=refusal)
    engine = engine_with(registry, embedding_dimension=384)

    route = engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A, create=True)

    assert route.index_state == "refused"
    assert route.index_error == refusal
    assert route.table_name in registry.tables


def test_an_indexed_route_records_that_it_is_present():
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=384)

    route = engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A, create=True)

    assert route.index_state == "present"
    assert route.index_error is None
    assert any("HNSW" in sql for sql in registry.created_indexes)


def test_the_routed_table_is_keyed_for_an_index():
    """FR-018: the HNSW is only legal over a single integer identity, and
    `(graph_id, node_id)` has to be enforced separately."""
    registry = FakeRegistry()
    engine = engine_with(registry, embedding_dimension=384)

    engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A, create=True)

    ddl = registry.statements_matching("CREATE TABLE")[0][0]
    assert "emb_rowid BIGINT IDENTITY PRIMARY KEY" in ddl
    assert "UNIQUE (graph_id, node_id)" in ddl
    assert "REFERENCES" in ddl and "(graph_id, node_id)" in ddl


# -------------------------------------------------------------- the allowlist


def test_a_routed_name_is_admitted_by_the_allowlist():
    """`_t()` validates every name it is handed, so a routed table that the
    allowlist does not know is a `ValueError` at the first write rather than a
    degraded path (FR-012)."""
    name = route_table_name(GRAPH_A, MODEL_A)
    assert validate_table_name(name) == name
    assert validate_table_name(f"Graph_KG.{name}") == f"Graph_KG.{name}"


def test_something_merely_shaped_like_a_route_is_not_admitted():
    with pytest.raises(ValueError):
        validate_table_name(ROUTE_TABLE_PREFIX + "not-hex-at-all")
    with pytest.raises(ValueError):
        validate_table_name(ROUTE_TABLE_PREFIX + "0123")  # too short to be a digest
