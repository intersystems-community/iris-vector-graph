"""length(shortestPath(...)) asks the store for the distance only.

Only the length is visible to such a query, so the engine skips building the paths: Graph.KG.TraversalPaths
.ShortestLength searches from both ends at once, where ShortestPathJson searches from the source alone and builds
every shortest path (IC13 at SF1: 11 to 474 ms). A query that reads the path, its nodes or its relationships, an
allShortestPaths, or a direction ShortestLength does not take keeps the path routine.
"""

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult


def _run(cypher, length=3):
    e = IRISGraphEngine(MagicMock(), native_cypher=False)
    e._store = MagicMock()
    e._store.shortest_path_length.return_value = length
    e._store.execute_shortest_path.return_value = IVGResult(columns=["path", "length"], rows=[['{"nodes":["a","b"],"rels":["R"]}', 1]])
    sq = translate_to_sql(parse_query(cypher), {"a": "a", "b": "b"}, engine=e)
    return e, e._execute_shortest_path_cypher(sq, {"a": "a", "b": "b"})


def test_a_length_alone_takes_the_distance_search():
    e, r = _run("MATCH p = shortestPath((x {id: $a})-[:R*..6]-(y {id: $b})) RETURN length(p)")
    e._store.shortest_path_length.assert_called_once()
    e._store.execute_shortest_path.assert_not_called()
    assert r.rows == [[3]]


def test_no_path_is_no_row():
    e, r = _run("MATCH p = shortestPath((x {id: $a})-[:R*..6]-(y {id: $b})) RETURN length(p)", length=-1)
    assert r.rows == []


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH p = shortestPath((x {id: $a})-[:R*..6]-(y {id: $b})) RETURN p",
        "MATCH p = shortestPath((x {id: $a})-[:R*..6]-(y {id: $b})) RETURN nodes(p)",
        "MATCH p = allShortestPaths((x {id: $a})-[:R*..6]-(y {id: $b})) RETURN length(p)",
        "MATCH p = shortestPath((x {id: $a})<-[:R*..6]-(y {id: $b})) RETURN length(p)",
    ],
)
def test_anything_more_keeps_the_path_routine(cypher):
    e, _ = _run(cypher)
    e._store.shortest_path_length.assert_not_called()
    e._store.execute_shortest_path.assert_called()


def test_classes_deployed_before_the_distance_search_keep_the_path_routine():
    e = IRISGraphEngine(MagicMock(), native_cypher=False)
    e._store = MagicMock()
    e._store.shortest_path_length.side_effect = RuntimeError("<METHOD DOES NOT EXIST> *ShortestLength,Graph.KG.TraversalPaths")
    e._store.execute_shortest_path.return_value = IVGResult(columns=["path", "length"], rows=[['{"nodes":["a","b"]}', 1]])
    sq = translate_to_sql(parse_query("MATCH p = shortestPath((x {id: $a})-[:R*..6]-(y {id: $b})) RETURN length(p)"), {"a": "a", "b": "b"}, engine=e)
    assert e._execute_shortest_path_cypher(sq, {"a": "a", "b": "b"}).rows == [[1]]


def test_any_other_error_is_raised():
    e = IRISGraphEngine(MagicMock(), native_cypher=False)
    e._store = MagicMock()
    e._store.shortest_path_length.side_effect = RuntimeError("<COMMUNICATION LINK ERROR>")
    sq = translate_to_sql(parse_query("MATCH p = shortestPath((x {id: $a})-[:R*..6]-(y {id: $b})) RETURN length(p)"), {"a": "a", "b": "b"}, engine=e)
    with pytest.raises(RuntimeError, match="LINK"):
        e._execute_shortest_path_cypher(sq, {"a": "a", "b": "b"})
