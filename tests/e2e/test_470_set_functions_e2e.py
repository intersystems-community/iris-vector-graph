"""4.7.0 row 35b, live: `SET n.p = <a function, CASE, list read or predicate over the node>` writes the value.

Measured on f12bee92 (IRIS 2026.3, IVG470CY2): every one of these failed with GraphWriteError (SQLCODE -23, "Label
'N0' is not listed among the applicable tables"), nothing written, except `n.i ^ 2` (SQLCODE -1: IRIS SQL has no ^).
The UPDATE of rdf_props was given the value as a RETURN translates it, through the MATCH's alias n0 and its property
joins, which the UPDATE does not have. Row 35 fixed toInteger, toFloat, toString and toBoolean alone.

Each value is now read per node from its own MATCH row ahead of the writes, as `WITH n, <value> AS v SET n.p = v`
reads it. Known answers, by hand from openCypher, for node f1 {s: 'ab', i: 4, f: 2.5, l: [7, 8]} labelled T470F and
node f2 {s: 'cd'}: toUpper('ab') 'AB', substring('ab', 1) 'b', replace('ab', 'a', 'z') 'zb', size('ab') 2,
coalesce(null, 4) 4, [7, 8][1] 8, head 7, last 8, size of the list 2, CASE 'big', labels ['T470F'], id 'f1',
keys {id, s, i, f, l}, IS NULL true / false, exists true, 4 ^ 2 16.0, sqrt(4) 2.0, abs(4 - 10) 6, round(2.5) 3.0, and
toUpper of f2's s 'CD'. Each is read back typed on both routes. A null value removes the key.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

LABEL = "T470F"
KEYS = {"id", "s", "i", "f", "l"}

CASES = [
    ("toUpper(n.s)", ("AB", "str")),
    ("substring(n.s, 1)", ("b", "str")),
    ("replace(n.s, 'a', 'z')", ("zb", "str")),
    ("size(n.s)", (2, "int")),
    ("coalesce(n.missing, n.i)", (4, "int")),
    ("n.l[1]", (8, "int")),
    ("head(n.l)", (7, "int")),
    ("last(n.l)", (8, "int")),
    ("size(n.l)", (2, "int")),
    ("CASE WHEN n.i > 1 THEN 'big' ELSE 'small' END", ("big", "str")),
    ("labels(n)", [("T470F", "str")]),
    ("id(n)", ("f1", "str")),
    ("n.missing IS NULL", (True, "bool")),
    ("n.s IS NULL", (False, "bool")),
    ("exists(n.s)", (True, "bool")),
    ("n.i ^ 2", (16.0, "float")),
    ("sqrt(n.i)", (2.0, "float")),
    ("abs(n.i - 10)", (6, "int")),
    ("round(n.f)", (3.0, "float")),
]


@pytest.fixture()
def engines():
    conn = ns463.connect()
    engs = ns463.engines(conn)
    ns463.rows(engs["sql"], f"MATCH (n:{LABEL}) DETACH DELETE n")
    ns463.rows(engs["sql"], f"CREATE (:{LABEL} {{id: 'f1', s: 'ab', i: 4, f: 2.5, l: [7, 8]}}), "
                            f"(:{LABEL} {{id: 'f2', s: 'cd'}})")
    conn.commit()
    yield engs
    ns463.rows(engs["sql"], f"MATCH (n:{LABEL}) DETACH DELETE n")
    conn.commit()
    conn.close()


def _read(engines, route, ret, nid="f1"):
    return ns463.typed(ns463.rows(engines[route], f"MATCH (n:{LABEL} {{id: '{nid}'}}) RETURN {ret}"))


@pytest.mark.parametrize("value, want", CASES, ids=[c[0] for c in CASES])
def test_the_value_is_written(engines, value, want):
    ns463.rows(engines["sql"], f"MATCH (n:{LABEL} {{id: 'f1'}}) SET n.p = {value}")
    for route in ns463.ROUTES:
        assert _read(engines, route, "n.p") == [[want]], route


def test_keys(engines):
    ns463.rows(engines["sql"], f"MATCH (n:{LABEL} {{id: 'f1'}}) SET n.p = keys(n)")
    got = ns463.rows(engines["sql"], f"MATCH (n:{LABEL} {{id: 'f1'}}) RETURN n.p")[0][0]
    assert isinstance(got, list) and set(got) == KEYS


@pytest.mark.xfail(strict=True, reason="reduce(s = 0, x IN <list> | s + x) fails SQLCODE -23 in a RETURN too "
                                       "(the translation names re<N>.x.x); listed for 4.7.1")
def test_reduce(engines):
    ns463.rows(engines["sql"], f"MATCH (n:{LABEL} {{id: 'f1'}}) SET n.p = reduce(s = 0, x IN n.l | s + x)")
    assert _read(engines, "sql", "n.p") == [[(15, "int")]]


def test_two_values_in_one_set(engines):
    ns463.rows(engines["sql"], f"MATCH (n:{LABEL} {{id: 'f1'}}) SET n.a = toUpper(n.s), n.b = size(n.l), n.c = n.i + 1")
    assert _read(engines, "sql", "n.a, n.b, n.c") == [[("AB", "str"), (2, "int"), (5, "int")]]


def test_every_matched_node_its_own_value(engines):
    ns463.rows(engines["sql"], f"MATCH (n:{LABEL}) SET n.p = toUpper(n.s)")
    assert _read(engines, "sql", "n.p", "f1") == [[("AB", "str")]]
    assert _read(engines, "sql", "n.p", "f2") == [[("CD", "str")]]


def test_a_value_of_another_variable(engines):
    ns463.rows(engines["sql"], f"MATCH (n:{LABEL} {{id: 'f1'}}), (m:{LABEL} {{id: 'f2'}}) SET n.p = toUpper(m.s)")
    assert _read(engines, "sql", "n.p") == [[("CD", "str")]]


def test_a_null_value_removes_the_key(engines):
    ns463.rows(engines["sql"], f"MATCH (n:{LABEL} {{id: 'f1'}}) SET n.s = toUpper(n.missing)")
    got = ns463.rows(engines["sql"], f"MATCH (n:{LABEL} {{id: 'f1'}}) RETURN n.s, keys(n)")
    assert got[0][0] is None and "s" not in got[0][1]


def test_in_a_named_graph_only_that_graphs_node(engines):
    e = engines["sql"]
    ns463.rows(e, f"USE GRAPH 't470f_g' CREATE (:{LABEL} {{id: 'f1', s: 'gh'}})")
    try:
        ns463.rows(e, f"USE GRAPH 't470f_g' MATCH (n:{LABEL} {{id: 'f1'}}) SET n.p = toUpper(n.s)")
        assert ns463.typed(ns463.rows(e, f"USE GRAPH 't470f_g' MATCH (n:{LABEL} {{id: 'f1'}}) RETURN n.p")) == [
            [("GH", "str")]]
        assert ns463.rows(e, f"USE GRAPH '' MATCH (n:{LABEL} {{id: 'f1'}}) RETURN n.p") == [[None]]
    finally:
        e.erase_graph("t470f_g")


def test_with_no_use_graph_each_graphs_node_reads_its_own_row(engines):
    e = engines["sql"]
    ns463.rows(e, f"USE GRAPH 't470f_h' CREATE (:{LABEL} {{id: 'f1', s: 'gh'}})")
    try:
        ns463.rows(e, f"MATCH (n:{LABEL} {{id: 'f1'}}) SET n.p = toUpper(n.s)")
        assert ns463.rows(e, f"USE GRAPH '' MATCH (n:{LABEL} {{id: 'f1'}}) RETURN n.p") == [["AB"]]
        assert ns463.rows(e, f"USE GRAPH 't470f_h' MATCH (n:{LABEL} {{id: 'f1'}}) RETURN n.p") == [["GH"]]
    finally:
        e.erase_graph("t470f_h")
