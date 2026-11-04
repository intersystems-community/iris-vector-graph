"""4.6.0 R142: `kg_KNN_VEC` converts the query vector to the route's element type, not the engine's.

Found with R142's live test: a graph whose route is VECTOR(FLOAT, 3), searched by an engine whose own vectors are DOUBLE,
failed with SQLCODE -259 ("vectors of different datatypes"); the server-side search fell back to the client-side one,
which answered []. `_knn_route` (what the store and the translator read) already answered the route's dtype.
"""

from __future__ import annotations

from iris_vector_graph.routing import route_table_name
from tests.unit.route_fakes_227 import FakeRegistry, engine_with

DIM = 3


def _row(table_name, graph_id, dtype):
    return {
        "table_name": table_name, "graph_id": graph_id, "mechanism": "iris-embedding-config", "model_key": "m",
        "declared_config": "m", "dimension": DIM, "dtype": dtype, "index_state": "present", "index_error": None,
        "kind": "node",
    }


def _searches(route_dtype, **kwargs):
    table = route_table_name("g", "m")
    registry = FakeRegistry(rows=[_row(table, "g", route_dtype)], tables=[table], dimension_for={table: DIM})
    engine = engine_with(registry, embedding_dimension=DIM)
    engine.kg_KNN_VEC("[1.0,0.0,0.0]", k=2, graph="g", model_key="m", **kwargs)
    return [sql for sql, _ in registry.statements if "VECTOR_COSINE" in sql]


def test_a_float_route_is_searched_at_float():
    searched = _searches("FLOAT")
    assert searched and all("TO_VECTOR(?, FLOAT)" in sql for sql in searched), searched


def test_a_double_route_is_searched_at_double():
    searched = _searches("DOUBLE")
    assert searched and all("TO_VECTOR(?, DOUBLE)" in sql for sql in searched), searched


def test_an_explicit_dtype_still_wins():
    searched = _searches("FLOAT", dtype="DOUBLE")
    assert searched and all("TO_VECTOR(?, DOUBLE)" in sql for sql in searched), searched
