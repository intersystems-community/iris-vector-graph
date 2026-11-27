"""4.7.2, live: a number filter on a node property matches stored numbers only, wherever the node comes from.

4.7.0 row 24: with 0.95 on one node and the strings '.95' and '0.95' on others, `WHERE n.p = 0.95` matched all three.
The fix covered a property joined in a MATCH (`pN.val`, and MERGE's `_mpN.val`, 4.7.1); a node reached another way kept
the old spelling rule, so `x.p = 0.95` over an unwound path element, an unwound collected node or a list
comprehension's element still matched the strings. Known answers: n1 holds the number 0.95, n2 the string '.95', n3 the
string '0.95', n4 the number 5 and n5 the string '5'.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

LABEL = "T472N"
REL = "R472N"


@pytest.fixture(scope="module")
def engs():
    conn = ns463.connect()
    engines = ns463.engines(conn)
    ns463.rows(engines["sql"], f"MATCH (n:{LABEL}) DETACH DELETE n")
    for nid, value in (("n1", "0.95"), ("n2", "'.95'"), ("n3", "'0.95'"), ("n4", "5"), ("n5", "'5'")):
        ns463.rows(engines["sql"], f"CREATE (:{LABEL} {{id: '{nid}', p: {value}}})")
    for a, b in (("n1", "n2"), ("n2", "n3"), ("n3", "n4"), ("n4", "n5")):
        ns463.rows(engines["sql"],
                   f"MATCH (a:{LABEL} {{id: '{a}'}}), (b:{LABEL} {{id: '{b}'}}) CREATE (a)-[:{REL}]->(b)")
    conn.commit()
    # the stored types, as the comparison reads them: the answers below are of these values, not of the writer
    [[n1, n2, n3, n4, n5]] = ns463.rows(
        engines["sql"],
        f"MATCH (a:{LABEL} {{id: 'n1'}}), (b:{LABEL} {{id: 'n2'}}), (c:{LABEL} {{id: 'n3'}}), (d:{LABEL} {{id: 'n4'}}), "
        f"(e:{LABEL} {{id: 'n5'}}) RETURN a.p, b.p, c.p, d.p, e.p",
    )
    assert (n1, n2, n3, n4, n5) == (0.95, ".95", "0.95", 5, "5")
    yield engines
    ns463.rows(engines["sql"], f"MATCH (n:{LABEL}) DETACH DELETE n")
    conn.commit()
    conn.close()


def _ids(engine, query):
    return sorted(r[0] for r in ns463.rows(engine, query))


# each shape yields the nodes n1..n5 as `x`, in a different way; the filter after it is the same
SHAPES = {
    "match-with": f"MATCH (x:{LABEL})",
    "with-carried": f"MATCH (n:{LABEL}) WITH n AS x",
    "unwound-collect": f"MATCH (n:{LABEL}) WITH collect(n) AS ns UNWIND ns AS x",
}
FILTERS = {
    "= 0.95": ["n1"],
    "<> 0.95": ["n2", "n3", "n4", "n5"],
    ">= 0.95": ["n1", "n4"],
    "<= 0.95": ["n1"],
    "= 5": ["n4"],
    "<> 5": ["n1", "n2", "n3", "n5"],
}


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("flt", FILTERS)
def test_a_number_filter_matches_stored_numbers_only(engs, route, shape, flt):
    query = f"{SHAPES[shape]} WITH x WHERE x.p {flt} RETURN DISTINCT x.id"
    assert _ids(engs[route], query) == FILTERS[flt], (route, shape, flt)


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("flt", FILTERS)
def test_a_match_where_number_filter_matches_stored_numbers_only(engs, route, flt):
    """The shape 4.7.0 covered: the filter in the MATCH's own WHERE."""
    assert _ids(engs[route], f"MATCH (x:{LABEL}) WHERE x.p {flt} RETURN x.id") == FILTERS[flt], (route, flt)


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("flt", FILTERS)
def test_a_list_comprehension_over_collected_nodes_matches_stored_numbers_only(engs, route, flt):
    query = f"MATCH (n:{LABEL}) WITH collect(n) AS ns RETURN [x IN ns WHERE x.p {flt} | x.id] AS ids"
    [[ids]] = ns463.rows(engs[route], query)
    assert sorted(ids) == FILTERS[flt], (route, flt)


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("flt", FILTERS)
def test_a_list_comprehension_over_a_paths_nodes_matches_stored_numbers_only(engs, route, flt):
    # (`UNWIND nodes(p) AS x WITH x ...` fails with SQLCODE -149 whatever the filter: loud, and not this rule's)
    query = (f"MATCH p = (:{LABEL} {{id: 'n1'}})-[:{REL}*4]->() "
             f"RETURN [x IN nodes(p) WHERE x.p {flt} | x.id] AS ids")
    [[ids]] = ns463.rows(engs[route], query)
    assert sorted(ids) == FILTERS[flt], (route, flt)


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_path_predicate_matches_stored_numbers_only(engs, route):
    # the paths from n1 with a node holding the number 0.95: only n1 does, so every path (n1 is on each) qualifies;
    # from n2, no path does (n2 and n3 hold the strings '.95' and '0.95')
    query = (f"MATCH p = (:{LABEL} {{id: 'n2'}})-[:{REL}*1..2]->() "
             "WHERE any(x IN nodes(p) WHERE x.p = 0.95) RETURN count(p)")
    assert ns463.rows(engs[route], query) == [[0]]
