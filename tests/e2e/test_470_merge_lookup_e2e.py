"""4.7.0 (lead, after row 2), live: `MERGE (n:L {k: v}) SET ...` finds the node it should, and its cost per MERGE does
not grow with the graph.

Measured on 470-env (release-4.7.0 + row 2's hints) in a new namespace, 3,000 `MERGE (n:TM {id: $id}) SET n.v = $v`:
7.6, 11.9, 13.5 s per 1,000. The growth was two statements, the INSERT and the UPDATE of the SET's property row,
each `... WHERE s IN (<the MERGE's lookup>)`: EXPLAIN read the whole extent of rdf_props / nodes and ran the lookup
for each row. 4.4.1 and 4.6.5 took 62, 420, 1,100 to 1,230 s per 1,000 (row 2's guards as well).

Known answers, by hand from openCypher (MERGE matches the existing node by its label and properties, else creates
one): a string id, a numeric id (`{id: 42}`, stored typed), two ids that share their first 128 characters (the
indexed prefix of a value, so the value itself still decides), a node whose `id` was SET to another value later (4.6.5 and
release-4.7.0 made a second node as well), a key that is not `id`, a key 50 nodes share, and an UNWIND batch. Each on both routes.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

LABEL = "T470Mg"
LONG = "x" * 128


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


def _count(engines, where=""):
    return ns463.rows(engines["sql"], f"MATCH (n:{LABEL}) {where} RETURN count(n)")[0][0]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_string_id_merges_onto_one_node(engines, route):
    e = engines[route]
    ns463.rows(e, f"MERGE (n:{LABEL} {{id: $id}}) SET n.v = $v", {"id": "m1", "v": 1})
    ns463.rows(e, f"MERGE (n:{LABEL} {{id: $id}}) SET n.v = $v", {"id": "m1", "v": 2})
    assert _count(engines) == 1
    assert ns463.typed(ns463.rows(e, f"MATCH (n:{LABEL} {{id: 'm1'}}) RETURN n.v")) == [[(2, "int")]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_numeric_id_merges_onto_one_node(engines, route):
    e = engines[route]
    ns463.rows(e, f"MERGE (n:{LABEL} {{id: 42}}) SET n.v = 1")
    ns463.rows(e, f"MERGE (n:{LABEL} {{id: 42}}) SET n.v = 2")
    assert _count(engines) == 1
    assert ns463.typed(ns463.rows(e, f"MATCH (n:{LABEL}) RETURN n.id, n.v")) == [[(42, "int"), (2, "int")]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_ids_sharing_their_first_128_characters_are_two_nodes(engines, route):
    e = engines[route]
    a, b = LONG + "a", LONG + "b"
    for v, i in ((1, a), (2, b), (3, a)):
        ns463.rows(e, f"MERGE (n:{LABEL} {{id: $id}}) SET n.v = $v", {"id": i, "v": v})
    assert _count(engines) == 2
    got = ns463.rows(e, f"MATCH (n:{LABEL}) RETURN n.id, n.v ORDER BY n.v")
    assert got == [[b, 2], [a, 3]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_node_whose_id_was_set_later_is_found_by_its_new_id(engines, route):
    e = engines[route]
    ns463.rows(e, f"CREATE (:{LABEL} {{id: 'old', w: 1}})")
    ns463.rows(e, f"MATCH (n:{LABEL} {{w: 1}}) SET n.id = 'new'")
    ns463.rows(e, f"MERGE (n:{LABEL} {{id: 'new'}}) SET n.v = 7")
    assert _count(engines) == 1
    assert ns463.rows(e, f"MATCH (n:{LABEL}) RETURN n.id, n.w, n.v") == [["new", 1, 7]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_key_that_is_not_id(engines, route):
    e = engines[route]
    for v in (1, 2):
        ns463.rows(e, f"MERGE (n:{LABEL} {{name: $name}}) SET n.v = $v", {"name": "zed", "v": v})
    ns463.rows(e, f"MERGE (n:{LABEL} {{name: $name}}) SET n.v = $v", {"name": "zee", "v": 5})
    assert _count(engines) == 2
    assert ns463.rows(e, f"MATCH (n:{LABEL}) RETURN n.name, n.v ORDER BY n.name") == [["zed", 2], ["zee", 5]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_merge_matching_many_nodes_sets_each(engines, route):
    """A key 50 nodes share: the MERGE matches all of them and the SET writes each (and makes none)."""
    e = engines[route]
    ns463.rows(engines["sql"], f"UNWIND range(1, 50) AS i CREATE (:{LABEL} {{id: 'many' + toString(i), p: 'same'}})")
    ns463.rows(e, f"MERGE (n:{LABEL} {{p: 'same'}}) SET n.v = 3")
    assert _count(engines) == 50
    assert ns463.rows(e, f"MATCH (n:{LABEL}) WHERE n.v = 3 RETURN count(n)") == [[50]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_an_unwind_merge_batch(engines, route):
    """What loaders send: one statement, a row each, two rows naming one id (one node, the last row's value)."""
    e = engines[route]
    rows = [{"id": f"u{i}", "v": i} for i in range(20)] + [{"id": "u3", "v": 99}]
    ns463.rows(e, f"UNWIND $rows AS r MERGE (n:{LABEL} {{id: r.id}}) SET n.v = r.v", {"rows": rows})
    assert _count(engines) == 20
    assert ns463.rows(e, f"MATCH (n:{LABEL} {{id: 'u3'}}) RETURN n.v") == [[99]]
    assert ns463.rows(e, f"MATCH (n:{LABEL} {{id: 'u7'}}) RETURN n.v") == [[7]]
