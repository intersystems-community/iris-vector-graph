"""4.6.0 R082: the translator's `ivg.vector.search` reads the table the engine routes the `USE GRAPH` graph to.

The live answers are in `tests/e2e/test_460_vector_search_translator_routes_e2e.py` (a seed node in a routed graph).
This pins the translation for the shapes that cannot run on the test container: a text query embedded by a named
`%Embedding.Config` (none exists there), a FLOAT route, and a graph with no route.
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


def _sql(query, routes, params=None):
    engine = _Engine(routes)
    return translate_to_sql(parse_query(query), params or {}, engine=engine).sql, engine.asked


ROUTED = {"g": ("Graph_KG.kg_emb_abc", "FLOAT"), "": ("Graph_KG.kg_NodeEmbeddings", "DOUBLE")}


def test_a_text_query_with_an_embedding_config_reads_the_route():
    sql, asked = _sql(
        "USE GRAPH 'g' CALL ivg.vector.search('L', 'emb', 'heart attack', 3, {embedding_config: 'cfg'}) "
        "YIELD node, score RETURN node, score",
        ROUTED,
    )
    assert asked == [("g", None)]
    assert "FROM Graph_KG.kg_emb_abc e" in sql and "kg_NodeEmbeddings" not in sql
    assert "EMBEDDING(?, ?)" in sql


def test_a_seed_query_reads_the_seed_from_the_route_too():
    sql, _ = _sql("USE GRAPH 'g' CALL ivg.vector.search('L', 'emb', 'n1', 3) YIELD node, score RETURN node, score",
                  ROUTED)
    assert "FROM Graph_KG.kg_emb_abc e" in sql and "SELECT e2.emb FROM Graph_KG.kg_emb_abc e2" in sql
    assert "kg_NodeEmbeddings" not in sql


def test_a_vector_query_is_converted_to_the_routes_dtype():
    sql, _ = _sql("MATCH (x) WITH x LIMIT 1 CALL ivg.vector.search('L', 'emb', [1.0, 0.0], 3) YIELD node, score "
                  "RETURN node, score", ROUTED)
    # no USE GRAPH: the default pair's table, at its dtype
    assert "FROM Graph_KG.kg_NodeEmbeddings e" in sql and "TO_VECTOR(?, DOUBLE)" in sql
    sql, _ = _sql("USE GRAPH 'g' MATCH (x) WITH x LIMIT 1 CALL ivg.vector.search('L', 'emb', [1.0, 0.0], 3) "
                  "YIELD node, score RETURN node, score", ROUTED)
    assert "FROM Graph_KG.kg_emb_abc e" in sql and "TO_VECTOR(?, FLOAT)" in sql


def test_a_graph_with_no_route_answers_no_rows():
    sql, _ = _sql("USE GRAPH 'nowhere' CALL ivg.vector.search('L', 'emb', 'n1', 3) YIELD node, score "
                  "RETURN node, score", ROUTED)
    assert "AND 1=0" in sql


def test_without_an_engine_the_translation_is_unchanged():
    sql = translate_to_sql(
        parse_query("USE GRAPH 'g' CALL ivg.vector.search('L', 'emb', 'n1', 3) YIELD node, score RETURN node, score"),
        {},
    ).sql
    assert "kg_NodeEmbeddings e" in sql and "1=0" not in sql
