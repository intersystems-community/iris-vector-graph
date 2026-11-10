"""4.7.0 row 20b, live: a list of a path's nodes or relationships keeps its elements through reverse() and tail(), and
an element of a WITH-collected relationship list is the relationship.

- `RETURN reverse(relationships(p))` answered `']"V074R"['`: reverse() of the list's JSON text as a string, characters
  backwards. It answers the list reversed, its elements as `relationships(p)` spells them (4.7.0 row 20), and so do
  `reverse(nodes(p))` and `tail(nodes(p))` of a fixed-length path (`tail` answered the ids).
- `WITH collect(r) AS rs RETURN rs[0]` answered the type, `'R470Q'`, where `RETURN rs` answers the relationships
  (4.7.0 row 19); `rs[0]` is the first of them.

The graph: (x1:T470Q {name: 'a'})-[:R470Q {w: 1}]->(x2 {name: 'b'})-[:R470Q {w: 2}]->(x3 {name: 'c'}), on a label of
its own. Both Cypher routes.

    IVG_NAMESPACE=IVG470TIME2 IVG_PORT=31972 IVG_TEST_CONTAINER=ivg-iris-enterprise \\
        pytest tests/e2e/test_470_row20b_lists_e2e.py
"""

from __future__ import annotations

import json

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

R1 = {"type": "R470Q", "props": {"w": 1}}
R2 = {"type": "R470Q", "props": {"w": 2}}
PATH = "MATCH p = (a:T470Q {id: 'x1'})-[:R470Q]->(b)-[:R470Q]->(c) "
VLP = "MATCH p = (a:T470Q {id: 'x1'})-[:R470Q*2]->(c) "


@pytest.fixture(scope="module")
def engs():
    conn = ns463.connect()
    engs = ns463.engines(conn)
    q = engs["sql"]
    q.execute_cypher("MATCH (n:T470Q) DETACH DELETE n", {}, raise_on_error=True)
    q.execute_cypher("CREATE (:T470Q {id: 'x1', name: 'a'})-[:R470Q {w: 1}]->(:T470Q {id: 'x2', name: 'b'})"
                     "-[:R470Q {w: 2}]->(:T470Q {id: 'x3', name: 'c'})", {}, raise_on_error=True)
    yield engs
    q.execute_cypher("MATCH (n:T470Q) DETACH DELETE n", {}, raise_on_error=True)
    conn.close()


def _one(eng, q):
    rows = eng.execute_cypher(q, {}, raise_on_error=True).rows
    assert len(rows) == 1, rows
    return rows[0]


def _node(nid, name):
    """A node as IVG spells one inside a list (`RETURN [a, b]`)."""
    return {"_id": nid, "_labels": ["T470Q"],
            "_props": [json.dumps({"key": "id", "value": nid}, separators=(",", ":")),
                       json.dumps({"key": "name", "value": name}, separators=(",", ":"))]}


A, B, C = _node("x1", "a"), _node("x2", "b"), _node("x3", "c")


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_reverse_and_tail_of_a_fixed_length_paths_lists(engs, route):
    got = _one(engs[route], PATH + "RETURN reverse(relationships(p)) AS rr, reverse(nodes(p)) AS rn, "
                                   "tail(nodes(p)) AS tn, tail(relationships(p)) AS tr")
    assert got == [[R2, R1], [C, B, A], [B, C], [R2]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_they_match_the_same_functions_of_a_list_of_the_variables(engs, route):
    q = ("MATCH (a:T470Q {id: 'x1'})-[r:R470Q]->(b)-[s:R470Q]->(c) RETURN reverse([r, s]) AS rr, "
         "reverse([a, b, c]) AS rn, tail([a, b, c]) AS tn, tail([r, s]) AS tr")
    assert _one(engs[route], q) == [[R2, R1], [C, B, A], [B, C], [R2]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_reverse_of_a_variable_length_paths_lists(engs, route):
    """Its relationships are objects (4.6.0 R301) and its nodes ids (KNOWN_ISSUES): each list, reversed."""
    assert _one(engs[route], VLP + "RETURN reverse(relationships(p)) AS rr, reverse(nodes(p)) AS rn") == [
        [R2, R1], ["x3", "x2", "x1"]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_an_element_of_a_collected_relationship_list_is_the_relationship(engs, route):
    q = ("MATCH (:T470Q)-[r:R470Q]->() WITH r ORDER BY r.w WITH collect(r) AS rs "
         "RETURN rs[0] AS first, rs[-1] AS last, size(rs) AS n")
    assert _one(engs[route], q) == [R1, R2, 2]
