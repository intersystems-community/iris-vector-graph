"""4.6.0 R140: `vector_distance()` / `vector_similarity()` read the vector from the graph's route.

They read `kg_NodeEmbeddings` whatever the graph; since spec 227 a named graph's vectors are in its routed `kg_emb_*`
table, so under `USE GRAPH` the subquery found no vector and every distance was NULL (the R082 defect on another
function). The live answers are in `tests/e2e/test_460_vector_distance_route_e2e.py`; this pins the translation.
"""

from __future__ import annotations

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


class _Engine:
    """The two things the translation asks of an engine here: its schema and its route reader."""

    _schema_prefix = "Graph_KG"

    def __init__(self, routes):
        self.routes = routes
        self.asked = []

    def _knn_route(self, graph_id, model_key):
        self.asked.append((graph_id, model_key))
        return self.routes.get(graph_id, (None, None))

    def get_table_mapping(self, label):
        return None  # no label is mapped to a table of its own


ROUTES = {"g": ("Graph_KG.kg_emb_g", "FLOAT"), "h": ("Graph_KG.kg_emb_h", "DOUBLE"),
          "": ("Graph_KG.kg_NodeEmbeddings", "DOUBLE")}


def _sql(query, params=None, routes=ROUTES):
    engine = _Engine(routes)
    return translate_to_sql(parse_query(query), params or {}, engine=engine).sql, engine.asked


def test_a_named_graph_reads_its_route_at_its_dtype():
    sql, asked = _sql("USE GRAPH 'g' MATCH (n:L) RETURN n.node_id, vector_distance(n, [1.0, 0.0]) AS d")
    assert ("g", None) in asked
    assert "FROM Graph_KG.kg_emb_g WHERE node_id = " in sql and "kg_NodeEmbeddings" not in sql
    assert "TO_VECTOR('1.0,0.0', FLOAT)" in sql
    assert "VECTOR_COSINE((SELECT" not in sql  # IRIS will not compile a subquery as its argument


def test_another_graph_reads_its_own_route():
    sql, _ = _sql("USE GRAPH 'h' MATCH (n:L) RETURN vector_similarity(n, $v) AS s", {"v": [1.0, 0.0]})
    assert "FROM Graph_KG.kg_emb_h WHERE node_id = " in sql and "kg_emb_g" not in sql
    assert "TO_VECTOR('1.0,0.0', DOUBLE)" in sql


def test_a_graph_with_no_route_has_no_vector():
    sql, _ = _sql("USE GRAPH 'none' MATCH (n:L) RETURN vector_distance(n, [1.0, 0.0]) AS d")
    assert "kg_NodeEmbeddings" not in sql and "kg_emb_" not in sql
    assert "CAST(NULL AS DOUBLE)" in sql


def test_no_use_graph_keeps_the_default_table():
    sql, _ = _sql("MATCH (n:L) RETURN vector_distance(n, [1.0, 0.0]) AS d")
    assert "FROM Graph_KG.kg_NodeEmbeddings WHERE node_id = " in sql


def test_no_engine_keeps_the_legacy_table():
    sql = translate_to_sql(parse_query("USE GRAPH 'g' MATCH (n:L) RETURN vector_distance(n, [1.0]) AS d"), {}).sql
    assert "kg_NodeEmbeddings WHERE node_id = " in sql
