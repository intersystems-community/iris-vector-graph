"""4.6.0 (NR8): which shortestPath RETURN clauses the engine shapes itself, and which it hands to the translator.

`_shape_shortest_path` answers a RETURN that is only `length(p)`, `nodes(p)` and `relationships(p)` from the paths the
search found, under the names the RETURN gives them. Anything else (the path itself, a list comprehension over its
nodes, a literal, an endpoint's property, ORDER BY / LIMIT / DISTINCT) is projected by the translator's own RETURN over
those same paths: the engine re-translates the query with the found paths in place of the search and runs it. Before
4.6.0 the engine built its own columns for the three functions and dropped every other item and every alias.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult
from iris_vector_graph.stores.iris_sql_store import _FULL_CAPABILITIES, IRISGraphStore

PATH = {"nodes": ["sp:A", "sp:F", "sp:E"], "rels": ["LINK", "LINK"], "length": 2}
MATCH = "MATCH p = shortestPath((a {node_id: $a})-[:LINK*..6]->(b {node_id: $b})) "
PARAMS = {"a": "sp:A", "b": "sp:E"}


class _Store(IRISGraphStore):
    """The real store's capabilities; the calls the shortestPath route makes answered here."""

    def __init__(self):
        self._schema_prefix = "Graph_KG"
        self.sql_calls: list = []
        self.projection_rows = [["projected"]]

    def capabilities(self):
        return dict(_FULL_CAPABILITIES)

    def execute_shortest_path(self, s, t, predicates, max_hops, direction, find_all):
        return IVGResult(columns=["path", "length"], rows=[[json.dumps(PATH), PATH["length"]]])

    def shortest_path_length(self, s, t, predicates, max_hops, direction):
        return PATH["length"]

    def execute_sql(self, sql, params=None):
        self.sql_calls.append((sql, list(params or [])))
        if "qualifiers" in sql and "JSON_TABLE" not in sql:
            return IVGResult(
                columns=["s", "p", "o_id", "qualifiers"],
                rows=[["sp:A", "LINK", "sp:F", '{"w": 1}'], ["sp:F", "LINK", "sp:E", None]],
            )
        return IVGResult(columns=["c"], rows=[list(r) for r in self.projection_rows])


def _run(cypher):
    store = _Store()
    engine = IRISGraphEngine(MagicMock(), store=store)
    return engine.execute_cypher(MATCH + cypher, PARAMS), store


def _projections(store):
    return [sql for sql, _ in store.sql_calls if "JSON_TABLE('" in sql]


def test_bare_path_functions_keep_their_aliases():
    result, store = _run("RETURN length(p) AS len, nodes(p) AS ns, relationships(p) AS rs")
    assert result.columns == ["len", "ns", "rs"]
    assert result.rows == [[2, ["sp:A", "sp:F", "sp:E"], ["LINK", "LINK"]]]
    assert _projections(store) == []


def test_a_bare_function_without_an_alias_is_named_as_written():
    result, store = _run("RETURN nodes(p)")
    assert result.columns == ["nodes(p)"]
    assert result.rows == [[["sp:A", "sp:F", "sp:E"]]]
    assert _projections(store) == []


@pytest.mark.parametrize(
    "ret",
    [
        "RETURN length(p) AS len, [n IN nodes(p) | n.name] AS names",
        "RETURN [n IN nodes(p) | n.name] AS names",
        "RETURN p",
        "RETURN nodes(p) AS ns, 1 AS one",
        "RETURN length(p) AS len, b.name AS target",
        "RETURN length(p) + 1 AS x",
        "RETURN length(p) AS len ORDER BY len",
        "RETURN length(p) AS len LIMIT 1",
        "RETURN DISTINCT nodes(p) AS ns",
    ],
)
def test_anything_more_is_projected_over_the_path_found(ret):
    result, store = _run(ret)
    projections = _projections(store)
    assert len(projections) == 1, store.sql_calls
    sql = projections[0]
    # the found path is the statement's path source; nothing walks the graph again
    assert '"t": "sp:E"' in sql and '\\"sp:F\\"' in sql
    assert "CY_VLP_PATHS" not in sql
    assert result.rows == [["projected"]]


def test_the_projected_relationships_carry_their_stored_properties():
    _, store = _run("RETURN relationships(p) AS rs, 1 AS one")
    sql = _projections(store)[0]
    literal = json.loads(sql.split("JSON_TABLE('", 1)[1].split("', '$[*]'", 1)[0].replace("''", "'"))
    assert [json.loads(p["r"]) for p in literal] == [
        [{"type": "LINK", "props": {"w": 1}}, {"type": "LINK", "props": {}}]
    ]
    assert [json.loads(p["n"]) for p in literal] == [["sp:F", "sp:E"]]
    assert [(p["s"], p["t"], p["l"]) for p in literal] == [("sp:A", "sp:E", 2)]


def test_no_path_found_projects_no_row():
    store = _Store()
    store.execute_shortest_path = lambda *a: IVGResult(columns=["path", "length"], rows=[])
    store.projection_rows = []
    engine = IRISGraphEngine(MagicMock(), store=store)
    result = engine.execute_cypher(MATCH + "RETURN [n IN nodes(p) | n.name] AS names", PARAMS)
    assert result.rows == []
    assert "JSON_TABLE('[]'" in _projections(store)[0]
