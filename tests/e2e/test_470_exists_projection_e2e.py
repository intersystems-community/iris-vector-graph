"""4.7.0 row 34, live: `exists(x.p)` as a returned value answers true or false, beside other columns or alone.

Measured on 4.6.5 (f1a442e7, IRIS 2026.3, IVG470CY): `MATCH (n) RETURN n.id, exists(n.name)` failed with SQLCODE -12
("A term expected"), and so did `RETURN exists(n.name)` alone, a `WITH n, exists(n.name) AS e`, and `exists()` of a
relationship's or a map's key. The translation put `EXISTS(...)` in the SELECT list, where IRIS takes it only as a
condition. A WHERE `exists(n.name)` worked and still does.

The graph: T470X nodes a {name: 'a'} and b (no name), a -[:R470X {w: 2}]-> b, and c -[:R470X]-> b (no w). Known
answers by openCypher (`exists(x.p)` is `x.p IS NOT NULL`): a true, b false; a's relationship true, c's false; the
map {a: 1} true for key a, {b: 2} false.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

LABEL, REL = "T470X", "R470X"


@pytest.fixture(scope="module")
def engines():
    conn = ns463.connect()
    engs = ns463.engines(conn)
    ns463.rows(engs["sql"], f"MATCH (n:{LABEL}) DETACH DELETE n")
    ns463.rows(engs["sql"], f"CREATE (a:{LABEL} {{id: 't470x:a', name: 'a'}}), (b:{LABEL} {{id: 't470x:b'}}), "
                            f"(c:{LABEL} {{id: 't470x:c'}}), (a)-[:{REL} {{w: 2}}]->(b), (c)-[:{REL}]->(b)")
    conn.commit()
    yield engs
    ns463.rows(engs["sql"], f"MATCH (n:{LABEL}) DETACH DELETE n")
    conn.commit()
    conn.close()


def _q(engines, route, q):
    return ns463.typed(ns463.rows(engines[route], q))


A, B = ("t470x:a", "str"), ("t470x:b", "str")
T, F = (True, "bool"), (False, "bool")


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_exists_beside_a_column(engines, route):
    assert _q(engines, route, f"MATCH (n:{LABEL} {{id: 't470x:a'}}) RETURN n.id, exists(n.name)") == [[A, T]]
    assert _q(engines, route, f"MATCH (n:{LABEL} {{id: 't470x:b'}}) RETURN n.id, exists(n.name)") == [[B, F]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_exists_first_middle_and_last_of_three_columns(engines, route):
    m = f"MATCH (n:{LABEL}) WHERE n.id IN ['t470x:a', 't470x:b'] "
    assert _q(engines, route, m + "RETURN exists(n.name), n.id, n.name ORDER BY n.id") == [
        [T, A, ("a", "str")], [F, B, (None, "NoneType")]]
    assert _q(engines, route, m + "RETURN n.id, exists(n.name), n.name ORDER BY n.id") == [
        [A, T, ("a", "str")], [B, F, (None, "NoneType")]]
    assert _q(engines, route, m + "RETURN n.id, n.name, exists(n.name) ORDER BY n.id") == [
        [A, ("a", "str"), T], [B, (None, "NoneType"), F]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_exists_alone_and_through_a_with(engines, route):
    m = f"MATCH (n:{LABEL}) WHERE n.id IN ['t470x:a', 't470x:b'] "
    assert _q(engines, route, m + "RETURN exists(n.name) AS e ORDER BY n.id") == [[T], [F]]
    assert _q(engines, route, m + "WITH n, exists(n.name) AS e RETURN n.id, e ORDER BY n.id") == [[A, T], [B, F]]
    assert _q(engines, route, m + "RETURN n.id, NOT exists(n.name) ORDER BY n.id") == [[A, F], [B, T]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_exists_where_still_filters(engines, route):
    assert _q(engines, route, f"MATCH (n:{LABEL}) WHERE exists(n.name) RETURN n.id") == [[A]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_exists_of_a_relationship_key_and_a_map_key(engines, route):
    q = f"MATCH (s:{LABEL})-[r:{REL}]->() RETURN s.id, exists(r.w) ORDER BY s.id"
    assert _q(engines, route, q) == [[A, T], [("t470x:c", "str"), F]]
    assert _q(engines, route, "UNWIND [{a: 1}, {b: 2}] AS m RETURN exists(m.a) AS e") == [[T], [F]]
