"""4.7.0 newcomer walkthrough, live: a MERGE's ON CREATE / ON MATCH SET of a computed value stores it with its type.

Measured on 4.7.0 (b9d779bc, IVG470MRG, both routes): `MERGE (n:T {id: 'c'}) ON CREATE SET n.count = 1 + 1 RETURN
n.count` answered '2', and a fresh `MATCH (n:T {id: 'c'}) RETURN n.count, n.count + 1, n.count > 1` answered ['2', 3,
null]. `1.5 + 1` stored '2.5', `'a' + 'b'` 'ab' (right by luck), `1 < 2` the text '1', `ON MATCH SET n.v = 2 * 3` '6';
on a MERGE without an id key the same; on a relationship, `ON CREATE SET r.w = 1 + 1` stored the text
"FunctionCall(function_name='__arith_+', ...)". `MERGE (n {id: 'mt1'}) ON MATCH SET n.count = n.count + 1` raised
GraphWriteError (SQLCODE -23, "Label 'N0' is not listed among the applicable tables") and wrote nothing.

Known answers, by openCypher, worked by hand: 1 + 1 is the integer 2, so `n.count > 1` is true and `n.count + 1` is 3;
1.5 + 1 is the float 2.5; 'a' + 'b' the string 'ab'; 1 < 2 the boolean true; 2 * 3 the integer 6; on a node holding
count 1, `n.count + 1` is the integer 2 and `n.count * 10` the integer 10. Each is read back by a fresh MATCH and by
get_node(), not only from the MERGE's own RETURN.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

LABEL = "T470M"


@pytest.fixture()
def engines():
    conn = ns463.connect()
    engs = ns463.engines(conn)
    ns463.rows(engs["sql"], f"MATCH (n:{LABEL}) DETACH DELETE n")
    conn.commit()
    yield engs
    ns463.rows(engs["sql"], f"MATCH (n:{LABEL}) DETACH DELETE n")
    conn.commit()
    conn.close()


def _id(route, name):
    return f"t470m-{route}-{name}"


def _read(engine, nid, ret):
    return ns463.typed(ns463.rows(engine, f"MATCH (n:{LABEL} {{id: $id}}) RETURN {ret}", {"id": nid}))


def _node_prop(engine, nid, key):
    node = engine.get_node(nid)
    assert node is not None, nid
    return ns463.typed([[node.get(key)]])[0][0]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_on_create_set_of_an_integer_sum_stores_the_integer(engines, route):
    e, nid = engines[route], _id(route, "c")
    assert ns463.typed(ns463.rows(e, f"MERGE (n:{LABEL} {{id: $id}}) ON CREATE SET n.count = 1 + 1 RETURN n.count",
                                  {"id": nid})) == [[(2, "int")]]
    assert _read(e, nid, "n.count, n.count + 1, n.count > 1") == [[(2, "int"), (3, "int"), (True, "bool")]]
    assert _node_prop(e, nid, "count") == (2, "int")


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_on_create_set_of_a_float_a_string_and_a_boolean_expression(engines, route):
    e, nid = engines[route], _id(route, "f")
    ns463.rows(e, f"MERGE (n:{LABEL} {{id: $id}}) ON CREATE SET n.a = 1.5 + 1, n.s = 'a' + 'b', n.b = 1 < 2",
               {"id": nid})
    assert _read(e, nid, "n.a, n.a * 2, n.s, n.b, n.b = true") == [
        [(2.5, "float"), (5.0, "float"), ("ab", "str"), (True, "bool"), (True, "bool")]]
    assert [_node_prop(e, nid, k) for k in ("a", "s", "b")] == [(2.5, "float"), ("ab", "str"), (True, "bool")]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_on_match_set_of_a_product_on_an_existing_node(engines, route):
    e, nid = engines[route], _id(route, "m")
    ns463.rows(e, f"CREATE (:{LABEL} {{id: $id}})", {"id": nid})
    ns463.rows(e, f"MERGE (n:{LABEL} {{id: $id}}) ON MATCH SET n.v = 2 * 3 ON CREATE SET n.w = 1 + 1", {"id": nid})
    assert _read(e, nid, "n.v, n.v > 5, n.w") == [[(6, "int"), (True, "bool"), (None, "NoneType")]]
    assert _node_prop(e, nid, "v") == (6, "int")


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_on_match_does_not_fire_on_create(engines, route):
    e, nid = engines[route], _id(route, "n")
    ns463.rows(e, f"MERGE (n:{LABEL} {{id: $id}}) ON MATCH SET n.v = 2 * 3", {"id": nid})
    assert ns463.rows(e, f"MATCH (n:{LABEL} {{id: $id}}) RETURN keys(n)", {"id": nid}) == [[["id"]]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_merge_without_an_id_key_stores_the_computed_value(engines, route):
    e, name = engines[route], _id(route, "q")
    ns463.rows(e, f"MERGE (n:{LABEL} {{name: $name}}) ON CREATE SET n.count = 1 + 1", {"name": name})
    assert ns463.typed(ns463.rows(e, f"MATCH (n:{LABEL} {{name: $name}}) RETURN n.count, n.count > 1",
                                  {"name": name})) == [[(2, "int"), (True, "bool")]]
    ns463.rows(e, f"MERGE (n:{LABEL} {{name: $name}}) ON MATCH SET n.count = n.count + 1", {"name": name})
    assert ns463.typed(ns463.rows(e, f"MATCH (n:{LABEL} {{name: $name}}) RETURN n.count",
                                  {"name": name})) == [[(3, "int")]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_on_create_set_of_a_relationship_stores_the_computed_value(engines, route):
    e, a, b = engines[route], _id(route, "ra"), _id(route, "rb")
    ns463.rows(e, f"CREATE (:{LABEL} {{id: $a}}), (:{LABEL} {{id: $b}})", {"a": a, "b": b})
    ns463.rows(e, f"MATCH (a:{LABEL} {{id: $a}}), (b:{LABEL} {{id: $b}}) MERGE (a)-[r:R470M]->(b) "
                  "ON CREATE SET r.w = 1 + 1, r.f = 1.5 + 1, r.s = 'a' + 'b'", {"a": a, "b": b})
    assert ns463.typed(ns463.rows(e, f"MATCH (:{LABEL} {{id: $a}})-[r:R470M]->() RETURN r.w, r.w > 1, r.f, r.s",
                                  {"a": a})) == [[(2, "int"), (True, "bool"), (2.5, "float"), ("ab", "str")]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_on_match_set_reading_the_node_increments_it(engines, route):
    e, nid = engines[route], _id(route, "mt1")
    ns463.rows(e, f"CREATE (:{LABEL} {{id: $id, count: 1}})", {"id": nid})
    assert ns463.typed(ns463.rows(e, f"MERGE (n:{LABEL} {{id: $id}}) ON MATCH SET n.count = n.count + 1 "
                                     "RETURN n.count", {"id": nid})) == [[(2, "int")]]
    assert _read(e, nid, "n.count, n.count > 1") == [[(2, "int"), (True, "bool")]]
    assert _node_prop(e, nid, "count") == (2, "int")


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_on_match_set_of_another_property_from_the_node(engines, route):
    e, nid = engines[route], _id(route, "mt2")
    ns463.rows(e, f"CREATE (:{LABEL} {{id: $id, count: 1}})", {"id": nid})
    ns463.rows(e, f"MERGE (n:{LABEL} {{id: $id}}) ON MATCH SET n.z = n.count * 10", {"id": nid})
    assert _read(e, nid, "n.count, n.z, n.z + 1") == [[(1, "int"), (10, "int"), (11, "int")]]
    assert _node_prop(e, nid, "z") == (10, "int")


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_on_create_set_reading_the_new_node(engines, route):
    e, nid = engines[route], _id(route, "oc")
    ns463.rows(e, f"MERGE (n:{LABEL} {{id: $id}}) ON CREATE SET n.tag = n.id + '-x'", {"id": nid})
    assert _read(e, nid, "n.tag") == [[(nid + "-x", "str")]]
