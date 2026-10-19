"""`shortestPath((a {node_id: $x})-[...]-(b {node_id: $y}))` searches from $x to $y, and returns one path.

The translator read the endpoint key from `{id: ...}` only, so for `{node_id: ...}` the engine fell back
to the first string parameter in the dict: with the SNB bench's parameters (personId first) IC13 searched
from a person's sid, which is no node key, and answered zero rows on every run, locally and on AWS.
And `shortestPath` returned every shortest path (6 rows for one pair); Neo4j returns one, as
openCypher says; `allShortestPaths` keeps all of them.
"""

import json
from unittest.mock import MagicMock

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult

Q = "MATCH path = shortestPath((a {node_id: $personKey})-[:KNOWS*..6]-(b {node_id: $person2Key})) RETURN length(path)"
PARAMS = {"personId": "111", "person2Id": "222", "personKey": "Person:111", "person2Key": "Person:222"}


def _paths(n):
    return [[json.dumps({"nodes": ["Person:111", f"Person:m{i}", "Person:222"], "rels": ["KNOWS", "KNOWS"], "length": 2}), 2] for i in range(n)]


def _engine(paths):
    store = MagicMock()
    store.execute_shortest_path.return_value = IVGResult(columns=["path", "length"], rows=paths)
    return IRISGraphEngine(MagicMock(), store=store), store


def test_node_id_endpoints_are_the_search_endpoints():
    vl = translate_to_sql(parse_query(Q), PARAMS).var_length_paths[0]
    assert (vl["src_id_param"], vl["dst_id_param"]) == ("$personKey", "$person2Key")


def test_the_store_is_asked_for_the_keys_whatever_the_parameter_order():
    eng, store = _engine(_paths(1))
    eng.execute_cypher(Q, PARAMS)
    assert store.execute_shortest_path.call_args.args[:2] == ("Person:111", "Person:222")


def test_shortest_path_returns_one_path():
    eng, _ = _engine(_paths(6))
    assert eng.execute_cypher(Q, PARAMS).rows == [[2]]


def test_all_shortest_paths_returns_every_path():
    eng, _ = _engine(_paths(6))
    q = Q.replace("shortestPath(", "allShortestPaths(")
    assert len(eng.execute_cypher(q, PARAMS).rows) == 6
