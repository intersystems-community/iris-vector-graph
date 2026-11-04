"""4.6.0 R163: the centrality and community CALL procedures read the query's graph, or refuse `USE GRAPH`.

Before: every `CALL ivg.<centrality or community>` translated to its SQL function with no graph, so under
`USE GRAPH 'g'` it answered the default graph (degree, PPR) or every graph merged (the `^NKG` procedures), as if it
were g's answer.

Which ObjectScript methods take a graph (read in `iris_src/src/Graph/KG/`):

- `Centrality.DegreeCentralityJson(direction, predicate, topK, graph)` (R144) and `PageRank.RunJson(seeds, alpha,
  maxIter, bidir, revWeight, pGraph)` take one: `ivg.degreeCentrality` and `ivg.ppr` pass the `USE GRAPH` graph
  (`''`, the default graph, without one).
- `NKGAccel.BetweennessGlobal`, `ClosenessGlobal`, `EigenvectorGlobal` and `Communities.LeidenJsonAuto`,
  `TriangleCountJson`, `SCCJson`, `KCoreJson` read `^NKG`, which interns identifiers with no graph dimension
  (TraversalBuild: "graph-blind by construction"). They cannot answer one graph, so `USE GRAPH` with any of them,
  `''` included (that is one graph too), is an error naming the procedure and the reason.

The answers on two graphs are in tests/e2e/test_460_r163_centrality_call_graph_e2e.py; these pin the SQL.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.schema import GraphSchema


def _sql(query: str) -> str:
    return translate_to_sql(parse_query(query), {}).sql


@pytest.mark.parametrize(
    "prefix,graph",
    [("", "''"), ("USE GRAPH 'r163g' ", "'r163g'"), ("USE GRAPH '' ", "''"), ("USE GRAPH 'o\\'k' ", "'o''k'")],
)
def test_degree_centrality_passes_the_query_graph(prefix, graph):
    sql = _sql(f"{prefix}CALL ivg.degreeCentrality({{direction: 'in'}}) YIELD node, score, degree RETURN node, degree")
    assert re.search(rf"kg_DegreeCentrality\('in', '', 10000, {re.escape(graph)}\)", sql), sql


@pytest.mark.parametrize("prefix,graph", [("", "''"), ("USE GRAPH 'r163g' ", "'r163g'")])
def test_ppr_passes_the_query_graph(prefix, graph):
    sql = _sql(f"{prefix}CALL ivg.ppr(['r163:a'], 0.85, 20) YIELD node, score RETURN node, score")
    assert re.search(rf"kg_PPR\('\[\"r163:a\"\]', 0\.85, 20, 0, 1\.0, {re.escape(graph)}\)", sql), sql


GRAPH_BLIND = [
    "CALL ivg.betweenness({}) YIELD node, score RETURN node, score",
    "CALL ivg.closeness({}) YIELD node, score RETURN node, score",
    "CALL ivg.eigenvector({}) YIELD node, score RETURN node, score",
    "CALL ivg.leiden({}) YIELD node, community RETURN node, community",
    "CALL ivg.triangleCount({}) YIELD node, triangles RETURN node, triangles",
    "CALL ivg.scc({}) YIELD node, component RETURN node, component",
    "CALL ivg.kcore({}) YIELD node, coreness RETURN node, coreness",
]


@pytest.mark.parametrize("call", GRAPH_BLIND)
@pytest.mark.parametrize("graph", ["r163g", ""])
def test_a_graph_blind_procedure_refuses_use_graph(call, graph):
    name = call.split("(")[0].removeprefix("CALL ")
    with pytest.raises(ValueError) as raised:
        _sql(f"USE GRAPH '{graph}' {call}")
    text = str(raised.value)
    assert name in text and "USE GRAPH" in text and "^NKG" in text, text


@pytest.mark.parametrize("call", GRAPH_BLIND)
def test_a_graph_blind_procedure_still_answers_without_use_graph(call):
    fn = {
        "betweenness": "kg_Betweenness",
        "closeness": "kg_Closeness",
        "eigenvector": "kg_Eigenvector",
        "leiden": "kg_Leiden",
        "triangleCount": "kg_TriangleCount",
        "scc": "kg_SCC",
        "kcore": "kg_KCore",
    }[call.split("(")[0].removeprefix("CALL ivg.")]
    assert f"{fn}(" in _sql(call)


def _ddl(name: str) -> str:
    return next(s for s in GraphSchema.get_procedures_sql_list(table_schema="Graph_KG") if f".{name}(" in s)


def test_kg_degree_centrality_takes_the_graph_and_hands_it_to_the_class_method():
    ddl = _ddl("kg_DegreeCentrality")
    assert "graphId VARCHAR(256) DEFAULT ''" in ddl, ddl
    assert "DegreeCentralityJson(direction, predicate, topK, graphId)" in ddl, ddl


def test_kg_ppr_hands_its_graph_to_runjson():
    assert "RunJson(seedEntities, dampingFactor, maxIterations, bidirectional, reverseEdgeWeight, graphId)" in _ddl(
        "kg_PPR"
    )
