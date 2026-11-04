"""4.6.0 R158: without `USE GRAPH`, `vector_distance()` scores each row with its own graph's route.

R140 read the `USE GRAPH` graph's route; with no `USE GRAPH` it read the default pair's table for every row, so in an
all-graphs read a named graph's node was scored against the default graph's vector of that id (or none: NULL). The
translation now picks, per row, the route of the row's `graph_id` (`EmbeddingsMixin._knn_routes`), each at its own
element type; a graph with no route has no vector, NULL. The live answers are in
`tests/e2e/test_460_vector_distance_all_graphs_e2e.py`.
"""

from __future__ import annotations

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


class _Engine:
    _schema_prefix = "Graph_KG"

    def __init__(self, routes):
        self.routes = routes

    def _knn_route(self, graph_id, model_key):
        for g, table, dtype, _ in self.routes:
            if g == graph_id:
                return table, dtype
        return None, None

    def _knn_routes(self, model_key):
        return list(self.routes)

    def get_table_mapping(self, label):
        return None


#: graph, table, element type, width: three routes 2 wide, one 768 wide (another model's space)
ROUTES = [("", "Graph_KG.kg_NodeEmbeddings", "DOUBLE", 2), ("g1", "Graph_KG.kg_emb_g1", "FLOAT", 2),
          ("o'k", "Graph_KG.kg_emb_ok", "DOUBLE", None), ("wide", "Graph_KG.kg_emb_wide", "DOUBLE", 768)]


def _sql(query, params=None):
    return translate_to_sql(parse_query(query), params or {}, engine=_Engine(ROUTES)).sql


def test_a_route_of_another_width_is_not_named():
    # IRIS refuses a statement naming a vector column of another width than the query's at Open (-257), whether or
    # not a row reaches it; a node of that graph has no score against this vector
    sql = _sql("MATCH (n:L) RETURN vector_distance(n, [1.0, 0.0]) AS d")
    assert "kg_emb_wide" not in sql and "WHEN 'wide'" not in sql


def test_each_graph_is_scored_from_its_own_route():
    sql = _sql("MATCH (n:L) RETURN vector_distance(n, [1.0, 0.0]) AS d")
    assert "CASE COALESCE(n0.graph_id, '')" in sql, sql
    assert (
        "WHEN 'g1' THEN (SELECT 1 - VECTOR_COSINE(emb, TO_VECTOR('1.0,0.0', FLOAT)) FROM Graph_KG.kg_emb_g1 "
        "WHERE node_id = n0.node_id AND COALESCE(graph_id, '') = 'g1')"
    ) in sql, sql
    assert "WHEN '' THEN (SELECT 1 - VECTOR_COSINE(emb, TO_VECTOR('1.0,0.0', DOUBLE)) FROM Graph_KG.kg_NodeEmbeddings" in sql
    assert "WHEN 'o''k' THEN" in sql and "COALESCE(graph_id, '') = 'o''k')" in sql
    assert "ELSE CAST(NULL AS DOUBLE) END" in sql


def test_a_text_vector_parameter_is_inlined_not_bound():
    # the `?` these functions emitted for a text `$v` went into a bind list it was never added to, so it took the
    # label's value; the elements are read as numbers and inlined, as a list's are
    for routes, use in ((ROUTES, ""), ([], "USE GRAPH 'g1' ")):
        q = translate_to_sql(
            parse_query(f"{use}MATCH (n:L) RETURN vector_similarity(n, $v) AS s"),
            {"v": "[1.0, 0]"},
            engine=_Engine(routes or ROUTES),
        )
        assert "TO_VECTOR(?" not in q.sql and "TO_VECTOR('1.0,0.0', " in q.sql, q.sql
        assert q.parameters == [["L"]]


def test_a_vector_that_is_not_numbers_is_refused():
    import pytest

    with pytest.raises(ValueError, match="list of numbers"):
        _sql("MATCH (n:L) RETURN vector_similarity(n, $v) AS s", {"v": "[1.0, 'x']"})


def test_use_graph_still_reads_that_graphs_route_alone():
    sql = _sql("USE GRAPH 'g1' MATCH (n:L) RETURN vector_distance(n, [1.0, 0.0]) AS d")
    assert "CASE" not in sql and "FROM Graph_KG.kg_emb_g1 WHERE node_id = " in sql


def _routes_engine(rows=None, error=None):
    from unittest.mock import MagicMock

    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = MagicMock()
    engine._schema_prefix = "Graph_KG"
    engine._t = lambda name: f"Graph_KG.{name}"
    engine._registry_table = lambda: "Graph_KG.embedding_registry"
    # a declared model, so the default graph has no legacy-table fallback to probe
    engine._offered_embedding_identity = lambda config=None: type("I", (), {"model_key": "m"})()
    cursor = engine.conn.cursor.return_value
    if error is not None:
        cursor.execute.side_effect = error
    cursor.fetchall.return_value = rows or []
    return engine


def test_the_route_list_is_one_preferred_row_per_graph():
    rows = [("g1", "kg_emb_a", "float", 3), ("", "kg_emb_b", None, "768"), ("g1", "kg_emb_c", "DOUBLE", 3)]
    assert _routes_engine(rows)._knn_routes(None) == [
        ("", "Graph_KG.kg_emb_b", "DOUBLE", 768),
        ("g1", "Graph_KG.kg_emb_a", "FLOAT", 3),
    ]


def test_no_registry_is_no_route_list_and_any_other_failure_raises():
    import pytest

    absent = RuntimeError("[SQLCODE: <-30>:<Table or view not found>]")
    assert _routes_engine(error=absent)._knn_routes(None) is None
    with pytest.raises(RuntimeError, match="-99"):
        _routes_engine(error=RuntimeError("[SQLCODE: <-99>:<Privilege violation>]"))._knn_routes(None)
