"""4.7.1 H1: `MERGE (n:L {v: 6})` against a stored string '6' makes a new node, as `MATCH` says it must.

4.7.0 row 24 made a number filter on a node property match stored numbers only: `MATCH (n:L {v: 6})` does not find
the string '6'. MERGE's existence check (the `NOT EXISTS` sub-SELECT that decides whether to create) kept 4.6.5's
spelling rule, so it found '6' and created nothing, while MERGE's binding join (the SELECT that answers the rows)
used row 24's typed rule and found nothing either: no rows and no node (4.7.0, live, IVG470SPN).

Known answers, by hand from openCypher (MERGE matches a node with the label and an equal property, else creates
one; the string '6' is not equal to the number 6):

- '6' stored: MERGE answers one row, the number 6, on a new node; two `:L` nodes; the '6' node unchanged.
- the same MERGE again: the node it made, nothing written (still two).
- the number 6 stored: MERGE answers that node, one `:L` node.
- the same for a `$v` parameter, ON CREATE SET / ON MATCH SET, and `UNWIND [6] AS x MERGE (n:L {v: x})`.
- a string MERGE `{v: '6'}` against stored '6' matches it (no new node).

Each on both routes.

    IVG_NAMESPACE=IVG470H1 pytest tests/e2e/test_471_merge_number_e2e.py
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

LABEL = "T471Mn"

# (name, query, params): each answers n.v first
FORMS = {
    "literal": (f"MERGE (n:{LABEL} {{v: 6}}) RETURN n.v, n.id", {}),
    "param": (f"MERGE (n:{LABEL} {{v: $v}}) RETURN n.v, n.id", {"v": 6}),
    "unwind": (f"UNWIND [6] AS x MERGE (n:{LABEL} {{v: x}}) RETURN n.v, n.id", {}),
    "on_set": (f"MERGE (n:{LABEL} {{v: 6}}) ON CREATE SET n.c = 1 ON MATCH SET n.m = 1 RETURN n.v, n.id, n.c, n.m",
               {}),
}


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


def _count(engines) -> int:
    return ns463.rows(engines["sql"], f"MATCH (n:{LABEL}) RETURN count(n)")[0][0]


def _stored_string_node(engines):
    """The '6' node's value and its sorted keys: unchanged means [[('6', 'str'), ['id', 'v']]]."""
    got = ns463.rows(engines["sql"], f"MATCH (n:{LABEL} {{id: 's6'}}) RETURN n.v, keys(n)")
    return [[ns463.typed([[v]])[0][0], sorted(keys)] for v, keys in got]


@pytest.mark.parametrize("form", list(FORMS))
@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_number_merge_makes_a_node_beside_a_stored_string(engines, route, form):
    e = engines[route]
    query, params = FORMS[form]
    ns463.rows(engines["sql"], f"CREATE (:{LABEL} {{id: 's6', v: '6'}})")
    first = ns463.rows(e, query, params)
    assert len(first) == 1
    assert ns463.typed([first[0][:1]]) == [[(6, "int")]]
    assert first[0][1] != "s6"
    if form == "on_set":
        assert list(first[0][2:]) == [1, None]  # ON CREATE ran, ON MATCH did not
    assert _count(engines) == 2
    assert _stored_string_node(engines) == [[("6", "str"), ["id", "v"]]]

    again = ns463.rows(e, query, params)
    assert len(again) == 1
    assert ns463.typed([again[0][:1]]) == [[(6, "int")]]
    assert again[0][1] == first[0][1]
    if form == "on_set":
        assert list(again[0][2:]) == [1, 1]  # the node it made, matched this time
    assert _count(engines) == 2
    assert _stored_string_node(engines) == [[("6", "str"), ["id", "v"]]]


@pytest.mark.parametrize("form", list(FORMS))
@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_number_merge_matches_a_stored_number(engines, route, form):
    e = engines[route]
    query, params = FORMS[form]
    ns463.rows(engines["sql"], f"CREATE (:{LABEL} {{id: 'n6', v: 6}})")
    got = ns463.rows(e, query, params)
    assert len(got) == 1
    assert ns463.typed([got[0][:2]]) == [[(6, "int"), ("n6", "str")]]
    if form == "on_set":
        assert list(got[0][2:]) == [None, 1]  # ON MATCH ran, ON CREATE did not
    assert _count(engines) == 1


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_string_merge_matches_a_stored_string(engines, route):
    e = engines[route]
    ns463.rows(engines["sql"], f"CREATE (:{LABEL} {{id: 's6', v: '6'}})")
    got = ns463.rows(e, f"MERGE (n:{LABEL} {{v: '6'}}) RETURN n.id, n.v")
    assert ns463.typed(got) == [[("s6", "str"), ("6", "str")]]
    assert _count(engines) == 1


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_long_unwind_merge_makes_its_six_beside_a_stored_string(engines, route):
    """250 values (the size 4.6.0 R310's long-list cases use): each is a new node, 6 included, so 251 `:L` nodes."""
    e = engines[route]
    ns463.rows(engines["sql"], f"CREATE (:{LABEL} {{id: 's6', v: '6'}})")
    ns463.rows(e, f"UNWIND $xs AS x MERGE (n:{LABEL} {{v: x}})", {"xs": list(range(1, 251))})
    assert _count(engines) == 251
    sixes = ns463.rows(engines["sql"], f"MATCH (n:{LABEL}) WHERE n.v = 6 RETURN n.id, n.v")
    assert len(sixes) == 1 and sixes[0][0] != "s6" and ns463.typed([sixes[0][1:]]) == [[(6, "int")]]
    assert _stored_string_node(engines) == [[("6", "str"), ["id", "v"]]]
