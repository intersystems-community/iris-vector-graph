"""Relationship property writes and parameterized list slices through Cypher."""

import uuid

import pytest

from iris_vector_graph import IRISGraphEngine
from iris_vector_graph.errors import GraphWriteError

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize(("prefix", "list_expression"), [
    ("", "$items"),
    ("WITH $items AS items ", "items"),
])
def test_slice_with_bound_limits(iris_connection, prefix, list_expression):
    engine = IRISGraphEngine(iris_connection)
    query = f"{prefix}RETURN {list_expression}[$start..$end] AS page"
    params = {"items": [1, 2, 3, 4], "start": 1, "end": 3}
    assert engine.execute_cypher(query, params, raise_on_error=True).rows == [[[2, 3]]]
    assert engine.execute_cypher(query, {**params, "start": None}, raise_on_error=True).rows == [[None]]


def test_a_known_slice_of_mixed_values_keeps_each_type(iris_connection):
    engine = IRISGraphEngine(iris_connection)
    values = ["ignored", "2", True, 3.5, ["nested"], {"k": "v"}]
    rows = engine.execute_cypher("RETURN $items[$start..] AS page",
                                 {"items": values, "start": -5}, raise_on_error=True).rows
    assert rows == [[values[1:]]]
    assert rows[0][0][1] is True


@pytest.mark.parametrize("multigraph", [False, True])
def test_create_relationship_keeps_parameter_and_expression_types(iris_connection, multigraph):
    engine = IRISGraphEngine(iris_connection)
    graph = f"cypher_params_{uuid.uuid4().hex}"
    use = f"USE GRAPH '{graph}' "
    engine.set_multigraph(graph, multigraph)
    make = "CREATE (:N {id: 'a'})-[:R %s]->(:N {id: 'b'})"
    try:
        with pytest.raises(GraphWriteError, match="`bad`"):
            engine.execute_cypher(use + make % "{bad: rand()}", raise_on_error=True)
        assert engine.execute_cypher(use + "MATCH (n) RETURN count(n)", raise_on_error=True).rows == [[0]]
        engine.execute_cypher(
            use + make % "{lit: 'x', score: $score, flag: $flag, tags: $tags, upper: toUpper('a'), gone: null}",
            {"score": 2.5, "flag": True, "tags": ["k", "v"]},
            raise_on_error=True,
        )
        rows = engine.execute_cypher(
            use + "MATCH ()-[r:R]->() RETURN r.lit, r.score, r.flag, r.tags, r.upper, r.gone",
            raise_on_error=True,
        ).rows
        assert rows == [["x", 2.5, True, ["k", "v"], "A", None]]
        assert rows[0][2] is True

        engine.execute_cypher(
            use + "MATCH (a {id: 'a'}), (b {id: 'b'}) "
            "WITH a, b, split('n,s', ',') AS tags CREATE (a)-[:S {tags: tags, weight: $score}]->(b)",
            {"score": 7}, raise_on_error=True,
        )
        rows = engine.execute_cypher(
            use + "MATCH ()-[r:S]->() RETURN r.tags, r.weight", raise_on_error=True,
        ).rows
        assert rows == [[["n", "s"], 7]]
    finally:
        engine.drop_graph(graph)
