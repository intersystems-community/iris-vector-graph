"""4.6.0 R192: without USE GRAPH a CALL answers the union of each graph's own answer, or raises.

CONTEXT.md, All-graphs read: a Cypher read that names no graph answers the union of each graph's own answer, and a
node is its own graph's node. These pin how each procedure forms that union, offline, with a stand-in engine that
reports two graphs with edges (the default one and `g1`). The answers on a live namespace, worked by hand, are in
tests/e2e/test_460_r192_all_graphs_procedures_e2e.py.

- a procedure that takes a graph (degreeCentrality, ppr, shortestPath.weighted) runs once per graph with edges, its
  rows tagged with the graph (`graph_id`), and the CTE is a UNION ALL of the branches;
- ivg.neighbors reads each edge's graph and answers one row per (graph, neighbour); under USE GRAPH, that graph's
  edges only (it read every graph's whatever USE GRAPH said);
- ivg.vector.search reads each graph's route, k nearest per graph;
- ivg.bm25.search and ivg.ivf.search tag their rows with the graph the index was built for;
- whatever the query reads of a tagged row's node (labels, properties, edges) is read in the row's graph;
- the `^NKG` procedures, ivg.retrieve, and gds.pageRank/betweenness/shortestPath.dijkstra cannot answer per graph,
  and raise, saying so.
"""

from __future__ import annotations

import re
from unittest import mock

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.stores.lazy_kg import graphs_with_edges


class _Engine:
    """Two graphs with edges, nodes in both; BM25 index `bm` built over g1, IVF index `iv` of unknown graph."""

    _schema_prefix = "Graph_KG"

    def __init__(self, graphs=("", "g1"), several=True):
        self.graphs = list(graphs)
        self.several = several

    def _graphs_with_edges(self):
        return self.graphs

    def _more_than_one_graph(self):
        return self.several

    def _named_graph_with_edges(self):
        return next((g for g in self.graphs if g), None)

    def _knn_routes(self, model_key):
        return [("", "Graph_KG.kg_NodeEmbeddings", "DOUBLE", 4), ("g1", "Graph_KG.kg_emb_g1", "FLOAT", 4)]

    def _index_graph(self, name, index_type):
        return {"bm": "g1"}.get(name)


def _sql(query: str, engine=None, params=None) -> str:
    return translate_to_sql(parse_query(query), params or {}, engine=engine or _Engine()).sql


def _branches(sql: str, cte: str) -> str:
    return sql.split(f"{cte} AS (", 1)[1]


def test_graphs_with_edges_reads_the_graph_keys_of_kg_out():
    keys = iter([0, "g1", ""])
    host = mock.Mock()
    host.next_subscript.side_effect = lambda *a, **k: next(keys)
    with mock.patch("iris_vector_graph.stores.lazy_kg.host_for", return_value=host):
        assert graphs_with_edges(object()) == ["", "g1"]
    assert [c.args[:2] for c in host.next_subscript.call_args_list] == [("^KG", "out")] * 3


def test_degree_centrality_unions_each_graph_s_own_degrees():
    sql = _sql("CALL ivg.degreeCentrality({}) YIELD node, score RETURN node.name AS n, score")
    body = _branches(sql, "DegCent")
    assert re.search(r"kg_DegreeCentrality\('out', '', 10000, ''\).*UNION ALL.*kg_DegreeCentrality\('out', '', "
                     r"10000, 'g1'\)", body, re.S), sql
    assert "'' AS graph_id" in body and "'g1' AS graph_id" in body
    # the name is read in the row's graph
    assert re.search(r"p0\.s = DegCent\.node_id AND p0\.graph_id = COALESCE\(DegCent\.graph_id, ''\)", sql), sql


def test_degree_centrality_under_use_graph_is_one_branch():
    sql = _sql("USE GRAPH 'g1' CALL ivg.degreeCentrality({}) YIELD node, score RETURN node, score")
    assert "UNION ALL" not in sql and "kg_DegreeCentrality('out', '', 10000, 'g1')" in sql


def test_ppr_walks_each_graph_and_a_hop_after_it_stays_in_the_row_s_graph():
    sql = _sql("CALL ivg.ppr(['a']) YIELD node, score MATCH (node)-[:R]->(m) RETURN node, m.name")
    body = _branches(sql, "PPR")
    assert body.count("kg_PPR(") == 2 and ", '')" in body and ", 'g1')" in body, sql
    assert re.search(r"e\d+\.s = PPR\.node_id AND e\d+\.graph_id = COALESCE\(PPR\.graph_id, ''\)", sql), sql
    # `RETURN node` reads its labels and properties in the row's graph
    assert "WHERE s = node AND graph_id = PPR.graph_id" in sql, sql


def test_neighbors_answer_one_row_per_graph_and_neighbour():
    sql = _sql("CALL ivg.neighbors(['a'], 'R', 'both') YIELD neighbor RETURN neighbor.name")
    assert sql.count("COALESCE(e.graph_id, '') AS graph_id") == 2, sql
    assert re.search(r"p0\.s = Neighbors\.node_id AND p0\.graph_id = COALESCE\(Neighbors\.graph_id, ''\)", sql)


def test_neighbors_under_use_graph_read_that_graph_s_edges():
    sql = _sql("USE GRAPH 'g1' CALL ivg.neighbors(['a']) YIELD neighbor RETURN neighbor")
    neighbors = _branches(sql, "Neighbors").split("\n)")[0]
    assert "AND COALESCE(e.graph_id, '') = 'g1'" in neighbors, sql


def test_weighted_shortest_path_searches_each_graph():
    sql = _sql("CALL ivg.shortestPath.weighted('a', 'c') YIELD path, totalCost RETURN totalCost ORDER BY totalCost")
    body = _branches(sql, "WS")
    assert body.count("DijkstraPath(") == 2 and "UNION ALL" in body, sql
    assert re.search(r"DijkstraPath\([^)]*, ''\)", body) and re.search(r"DijkstraPath\([^)]*, 'g1'\)", body)


def test_vector_search_reads_each_graph_s_route():
    sql = _sql(
        "CALL ivg.vector.search('L', 'emb', [1.0, 0.0, 0.0, 0.0], 2) YIELD node, score RETURN node.name, score"
    )
    body = _branches(sql, "VecSearch")
    assert "FROM Graph_KG.kg_NodeEmbeddings e" in body and "FROM Graph_KG.kg_emb_g1 e" in body, sql
    assert "TO_VECTOR(?, FLOAT)" in body and body.count("SELECT TOP 2") == 2 and "UNION ALL" in body, sql
    assert "COALESCE(e.graph_id, '') = COALESCE('g1', '')" in body
    assert re.search(r"p0\.graph_id = COALESCE\(VecSearch\.graph_id, ''\)", sql), sql


def test_bm25_rows_carry_the_index_s_graph():
    sql = _sql("CALL ivg.bm25.search('bm', 'apple', 5) YIELD node, score RETURN node.name")
    assert "'g1' AS graph_id" in _branches(sql, "BM25"), sql
    assert re.search(r"p0\.graph_id = COALESCE\(BM25\.graph_id, ''\)", sql), sql


def test_an_index_of_unknown_graph_raises_on_a_namespace_of_several_graphs():
    with pytest.raises(ValueError, match="union of each graph's own answer.*records no graph"):
        _sql("CALL ivg.ivf.search('iv', [1.0, 0.0], 5, 1) YIELD node, score RETURN node, score")
    # one graph: nothing to tell apart
    _sql("CALL ivg.ivf.search('iv', [1.0, 0.0], 5, 1) YIELD node, score RETURN node, score", _Engine([""], False))


def test_retrieve_raises_without_use_graph_on_a_namespace_of_several_graphs():
    with pytest.raises(ValueError, match="union of each graph's own answer.*USE GRAPH"):
        _sql("CALL ivg.retrieve('apple', 3, 'bm') YIELD node, rrf_score RETURN node, rrf_score")


@pytest.mark.parametrize(
    "call",
    [
        "ivg.betweenness({}) YIELD node, score RETURN node, score",
        "ivg.closeness({}) YIELD node, score RETURN node, score",
        "ivg.eigenvector({}) YIELD node, score RETURN node, score",
        "ivg.leiden({}) YIELD node, community RETURN node, community",
        "ivg.triangleCount({}) YIELD node, triangles RETURN node, triangles",
        "ivg.scc({}) YIELD node, component RETURN node, component",
        "ivg.kcore({}) YIELD node, coreness RETURN node, coreness",
    ],
)
def test_an_nkg_procedure_states_the_rule_when_it_raises(call):
    with pytest.raises(ValueError, match="union of each graph's own answer.*'g1'"):
        _sql(f"CALL {call}")


def test_without_an_engine_a_procedure_answers_the_default_graph_alone():
    sql = translate_to_sql(parse_query("CALL ivg.ppr(['a']) YIELD node, score RETURN node, score"), {}).sql
    assert "UNION ALL" not in sql and "'' AS graph_id" in sql and ", 0, 1.0, '')" in sql


@pytest.mark.parametrize(
    "call",
    [
        "gds.pageRank.stream('g') YIELD nodeId, score",
        "gds.betweenness.stream('g') YIELD nodeId, score",
        "gds.shortestPath.dijkstra.stream('g', {sourceNode: 'a', targetNode: 'c'}) YIELD totalCost",
    ],
)
def test_a_gds_procedure_raises_without_use_graph_once_a_named_graph_has_edges(call):
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine._named_graph_with_edges = lambda: "g1"
    proc = parse_query(f"CALL {call}").procedure_call
    with pytest.raises(ValueError, match="union of each graph's own answer.*'g1'.*USE GRAPH ''"):
        engine._gds_arguments(proc, {}, None)
    # USE GRAPH '' asks for the default graph's answer, and is not refused
    engine._gds_arguments(proc, {}, "")
