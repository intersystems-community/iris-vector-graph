"""4.7.2, live: a number filter in an OPTIONAL MATCH's WHERE binds one value per marker.

TCK MatchWhere6 [3], `MATCH (n:Single) OPTIONAL MATCH (n)-[r]-(m) WHERE m.num = 42 RETURN m`: the stored-number test of
4.7.0 row 24 names the property read three to five times, each copy with a `"key" = ?` marker and one parameter, and
IRIS answered <ARGUMENT ERROR> Incorrect number of parameters. Known answers: the node `A` holds the number 42; `B`
the number 7; `S` the string '42'; the Single node is joined to all three.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

SINGLE = "T472OSingle"
OTHER = "T472OOther"


@pytest.fixture(scope="module")
def engs():
    conn = ns463.connect()
    engines = ns463.engines(conn)
    sql = engines["sql"]
    for label in (SINGLE, OTHER):
        ns463.rows(sql, f"MATCH (n:{label}) DETACH DELETE n")
    ns463.rows(sql, f"CREATE (:{SINGLE} {{id: 's'}})")
    for nid, value in (("A", "42"), ("B", "7"), ("S", "'42'")):
        ns463.rows(sql, f"CREATE (:{OTHER} {{id: '{nid}', num: {value}}})")
        ns463.rows(sql, f"MATCH (s:{SINGLE}), (o:{OTHER} {{id: '{nid}'}}) CREATE (s)-[:R472O]->(o)")
    conn.commit()
    yield engines
    for label in (SINGLE, OTHER):
        ns463.rows(sql, f"MATCH (n:{label}) DETACH DELETE n")
    conn.commit()
    conn.close()


def _ids(engine, query, params=None):
    return sorted(str(r[0]) for r in ns463.rows(engine, query, params))


CASES = [
    ("= 42", {}, ["A"]),
    ("<> 42", {}, ["B", "S"]),
    ("> 7", {}, ["A"]),
    (">= 7", {}, ["A", "B"]),
    ("< 42", {}, ["B"]),
    ("<= 42", {}, ["A", "B"]),
    ("= $v", {"v": 42}, ["A"]),
    ("> $v", {"v": 7}, ["A"]),
]


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("flt,params,expected", CASES)
def test_an_optional_where_number_filter_answers_the_stored_numbers(engs, route, flt, params, expected):
    query = f"MATCH (n:{SINGLE}) OPTIONAL MATCH (n)-[r]-(m:{OTHER}) WHERE m.num {flt} RETURN m.id"
    assert _ids(engs[route], query, params) == expected, (route, flt)


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_the_tck_shape_returns_the_node(engs, route):
    """MatchWhere6 [3] as the suite writes it: one row, the node `m` itself (RETURN m reads as its id, labels and
    properties), and it is A, the one holding 42."""
    [[node_id, labels, props]] = ns463.rows(
        engs[route], f"MATCH (n:{SINGLE}) OPTIONAL MATCH (n)-[r]-(m) WHERE m.num = 42 RETURN m")
    assert node_id == "A", node_id
    assert OTHER in labels, labels
    assert "num" in props and "42" in props, props


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_no_node_keeps_the_row_with_a_null(engs, route):
    query = f"MATCH (n:{SINGLE}) OPTIONAL MATCH (n)-[r]-(m:{OTHER}) WHERE m.num = 99 RETURN m.id"
    assert ns463.rows(engs[route], query) == [[None]], route
