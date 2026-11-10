"""4.7.0 rows 19 and 20, live: a WITH-collected relationship list, and nodes(p) / relationships(p) of a path, answer
the relationships and nodes themselves.

Row 19: `MATCH ()-[r:R]->() WITH collect(r) AS rs RETURN rs` answered `['R', 'R']`, the types, where `RETURN collect(r)`
answers each relationship as IVG returns one, `{"type": "R", "props": {...}}`. `size(rs)`, `rs[0]` and a DELETE of an
element read the stage's types and edge ids, and still answer as before.

Row 20: `MATCH p = (a)-[:R]->(b) RETURN nodes(p), relationships(p)` answered `['a', 'b']` and `['R']`, the node ids and
the relationship types. Now the nodes as `RETURN [a, b]` spells them (`{"_id", "_labels", "_props"}`) and the
relationships as `RETURN [r]` does.

The graph: (x1:T470P {name: 'a'})-[:R470P {w: 1}]->(x2:T470P {name: 'b'})-[:R470P {w: 2}]->(x3:T470P {name: 'c'}),
on a label of its own, so the file shares the namespace with others. Both Cypher routes.

    IVG_NAMESPACE=IVG470TIME IVG_PORT=31972 IVG_TEST_CONTAINER=ivg-iris-enterprise \\
        pytest tests/e2e/test_470_rels_and_paths_e2e.py
"""

from __future__ import annotations

import json

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

R1 = {"type": "R470P", "props": {"w": 1}}
R2 = {"type": "R470P", "props": {"w": 2}}


@pytest.fixture(scope="module")
def engs():
    conn = ns463.connect()
    engs = ns463.engines(conn)
    q = engs["sql"]
    q.execute_cypher("MATCH (n:T470P) DETACH DELETE n", {}, raise_on_error=True)
    q.execute_cypher("CREATE (:T470P {id: 'x1', name: 'a'})-[:R470P {w: 1}]->(:T470P {id: 'x2', name: 'b'})"
                     "-[:R470P {w: 2}]->(:T470P {id: 'x3', name: 'c'})", {}, raise_on_error=True)
    yield engs
    q.execute_cypher("MATCH (n:T470P) DETACH DELETE n", {}, raise_on_error=True)
    conn.close()


def _rows(eng, q):
    return eng.execute_cypher(q, {}, raise_on_error=True).rows


def _key(r):
    return json.dumps(r, sort_keys=True)


def _node(nid, name):
    """A node as IVG spells one inside a list (`RETURN [a, b]`): its props as {"key", "value"} JSON texts."""
    return {"_id": nid, "_labels": ["T470P"],
            "_props": [json.dumps({"key": "id", "value": nid}, separators=(",", ":")),
                       json.dumps({"key": "name", "value": name}, separators=(",", ":"))]}


# ------------------------------------------------------------------------------------------------ row 19


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("query", [
    "MATCH (:T470P)-[r:R470P]->() WITH collect(r) AS rs RETURN rs",
    "MATCH (:T470P)-[r:R470P]->() WITH collect(r) AS rs WITH rs AS xs RETURN xs",
    "MATCH (:T470P)-[r:R470P]->() RETURN collect(r) AS rs",
])
def test_a_collected_relationship_list_answers_the_relationships(engs, route, query):
    [[rs]] = _rows(engs[route], query)
    assert sorted(rs, key=_key) == sorted([R1, R2], key=_key)


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_size_and_an_element_answer_as_before(engs, route):
    [[n, first]] = _rows(engs[route], "MATCH (:T470P)-[r:R470P]->() WITH collect(r) AS rs RETURN size(rs) AS n, "
                                      "rs[0] AS first")
    assert n == 2
    assert first in (R1, R2)  # the relationship itself since 4.7.0 row 20b (it was the type, 'R470P')


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_delete_of_an_element_deletes_that_relationship(engs, route):
    eng = engs[route]
    eng.execute_cypher("MATCH (:T470P {id: 'x1'})-[r:R470P]->() WITH collect(r) AS rs DELETE rs[0]", {},
                       raise_on_error=True)
    left = _rows(eng, "MATCH (:T470P)-[r:R470P]->() RETURN r.w AS w")
    eng.execute_cypher("MATCH (a:T470P {id: 'x1'}), (b:T470P {id: 'x2'}) CREATE (a)-[:R470P {w: 1}]->(b)", {},
                       raise_on_error=True)
    assert left == [[2]]


# ------------------------------------------------------------------------------------------------ row 20


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_nodes_and_relationships_of_a_path(engs, route):
    rows = _rows(engs[route], "MATCH p = (a:T470P {id: 'x1'})-[:R470P]->(b) RETURN nodes(p) AS ns, "
                              "relationships(p) AS rs")
    assert rows == [[[_node("x1", "a"), _node("x2", "b")], [R1]]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_they_match_the_shapes_of_a_list_of_the_variables(engs, route):
    path = _rows(engs[route], "MATCH p = (a:T470P {id: 'x1'})-[r:R470P]->(b)-[s:R470P]->(c) "
                              "RETURN nodes(p) AS ns, relationships(p) AS rs")
    lists = _rows(engs[route], "MATCH (a:T470P {id: 'x1'})-[r:R470P]->(b)-[s:R470P]->(c) "
                               "RETURN [a, b, c] AS ns, [r, s] AS rs")
    assert path == lists == [[[_node("x1", "a"), _node("x2", "b"), _node("x3", "c")], [R1, R2]]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_reads_through_the_path_answer_as_before(engs, route):
    [[names, ws, n, ids]] = _rows(engs[route], "MATCH p = (a:T470P {id: 'x1'})-[:R470P]->(b) "
                                               "RETURN [x IN nodes(p) | x.name] AS names, "
                                               "[r IN relationships(p) | r.w] AS ws, size(nodes(p)) AS n, "
                                               "[x IN nodes(p) | x.id] AS ids")
    assert (names, ws, n, ids) == (["a", "b"], [1], 2, ["x1", "x2"])
