"""4.7.0 row 35, live: `SET n.p = toInteger(n.p)` (and toFloat, toString, toBoolean) writes the converted value.

Measured on 4.6.5 (f1a442e7, IRIS 2026.3, IVG470CY): `MATCH (n) SET n.p = toInteger(n.p)` raised GraphWriteError
(SQLCODE -23, "Label 'N0' is not listed among the applicable tables") and wrote nothing. The value was translated as
a RETURN would read it, through the MATCH's alias n0, which the UPDATE of rdf_props does not have.

Known answers, by openCypher: toInteger('30') is 30 and toInteger('x') is null; toFloat('2.5') is 2.5; toString(30)
is '30'; toBoolean('true') is true; and a SET to null removes the property, so `keys(n)` no longer lists it. Each
value is read back typed (`RETURN n.p, n.p + 1`: 30, 31), on both routes; a node without the property gets none.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

LABEL = "T470S"


@pytest.fixture()
def engines():
    conn = ns463.connect()
    engs = ns463.engines(conn)
    ns463.rows(engs["sql"], f"MATCH (n:{LABEL}) DETACH DELETE n")
    ns463.rows(engs["sql"], f"CREATE (:{LABEL} {{id: 'num', p: '30', f: '2.5', i: 30, t: 'true'}}), "
                            f"(:{LABEL} {{id: 'txt', p: 'x'}}), (:{LABEL} {{id: 'none'}})")
    conn.commit()
    yield engs
    ns463.rows(engs["sql"], f"MATCH (n:{LABEL}) DETACH DELETE n")
    conn.commit()
    conn.close()


def _read(engines, route, nid, ret):
    return ns463.typed(ns463.rows(engines[route], f"MATCH (n:{LABEL} {{id: $id}}) RETURN {ret}", {"id": nid}))


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_to_integer_of_a_numeric_string(engines, route):
    ns463.rows(engines["sql"], f"MATCH (n:{LABEL} {{id: 'num'}}) SET n.p = toInteger(n.p)")
    assert _read(engines, route, "num", "n.p, n.p + 1") == [[(30, "int"), (31, "int")]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_to_integer_of_text_removes_the_property(engines, route):
    ns463.rows(engines["sql"], f"MATCH (n:{LABEL} {{id: 'txt'}}) SET n.p = toInteger(n.p)")
    assert _read(engines, route, "txt", "n.p") == [[(None, "NoneType")]]
    assert ns463.rows(engines[route], f"MATCH (n:{LABEL} {{id: 'txt'}}) RETURN keys(n)") == [[["id"]]]


def test_every_matched_node_and_none_without_the_property(engines):
    ns463.rows(engines["sql"], f"MATCH (n:{LABEL}) SET n.p = toInteger(n.p)")
    assert _read(engines, "sql", "num", "n.p") == [[(30, "int")]]
    assert _read(engines, "sql", "txt", "n.p") == [[(None, "NoneType")]]
    assert ns463.rows(engines["sql"], f"MATCH (n:{LABEL} {{id: 'none'}}) RETURN keys(n)") == [[["id"]]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_to_float_to_string_and_to_boolean(engines, route):
    ns463.rows(engines["sql"], f"MATCH (n:{LABEL} {{id: 'num'}}) "
                               "SET n.f = toFloat(n.f), n.s = toString(n.i), n.t = toBoolean(n.t)")
    assert _read(engines, route, "num", "n.f, n.f * 2, n.s, n.t") == [
        [(2.5, "float"), (5.0, "float"), ("30", "str"), (True, "bool")]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_from_another_property_into_a_new_one(engines, route):
    ns463.rows(engines["sql"], f"MATCH (n:{LABEL} {{id: 'num'}}) SET n.q = toInteger(n.p)")
    assert _read(engines, route, "num", "n.q, n.p") == [[(30, "int"), ("30", "str")]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_in_place_conversions_retype_the_stored_value(engines, route):
    """The row is rewritten even where the new value has the text the row holds (IRIS files no such UPDATE)."""
    ns463.rows(engines["sql"], f"MATCH (n:{LABEL} {{id: 'num'}}) SET n.i = toString(n.i), n.f = toFloat(n.f)")
    assert _read(engines, route, "num", "n.i, n.f") == [[("30", "str"), (2.5, "float")]]
    ns463.rows(engines["sql"], f"MATCH (n:{LABEL} {{id: 'num'}}) SET n.i = toInteger(n.i)")
    assert _read(engines, route, "num", "n.i, n.i * 2") == [[(30, "int"), (60, "int")]]


def test_in_a_named_graph_only_that_graphs_node_changes(engines):
    e = engines["sql"]
    ns463.rows(e, f"USE GRAPH 't470s_g' CREATE (:{LABEL} {{id: 'num', p: '7'}})")
    try:
        ns463.rows(e, f"USE GRAPH 't470s_g' MATCH (n:{LABEL} {{id: 'num'}}) SET n.p = toInteger(n.p)")
        assert ns463.typed(ns463.rows(e, f"USE GRAPH 't470s_g' MATCH (n:{LABEL} {{id: 'num'}}) RETURN n.p")) == [
            [(7, "int")]]
        # the default graph's node of that id is not written (a read without USE GRAPH reads every graph)
        assert ns463.typed(ns463.rows(e, f"USE GRAPH '' MATCH (n:{LABEL} {{id: 'num'}}) RETURN n.p")) == [
            [("30", "str")]]
    finally:
        e.erase_graph("t470s_g")
