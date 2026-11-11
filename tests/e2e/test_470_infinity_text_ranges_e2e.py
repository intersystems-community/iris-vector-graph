"""4.7.0 (full E2E gate on ea5ced01), live: a range filter reads a stored infinity text as the infinity.

The Python API stores a float infinity as the text 'inf' (4.6.4), the same text a Cypher string 'inf' is stored with
(measured: one $LISTBUILD type, 5 / 1); a Cypher-bound infinity is a $DOUBLE. Row 24 (b9c12438) read the text as a
string, null against a number, so test_460_r307_value_matrix_e2e::test_filter[ge_param-node-*] answered false for
`inf >= $inf` where the documented answer, and 4.6.5's, is true. The texts 'inf', '-inf', 'Infinity' and '-Infinity'
now read as numbers there, as row 24's `=` already matched them; so does a Cypher string 'inf' (docs/KNOWN_ISSUES.md).
NaN's texts do not, and every comparison with them stays false.

Known answers, by hand, both routes: api_inf / api_minf / api_nan written by create_node; cy_str_inf ('inf') and
cy_str_nan ('NaN') Cypher strings; cy_num the number 5.
"""

from __future__ import annotations

import math

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

LABEL = "T470Inf"


@pytest.fixture()
def engines():
    conn = ns463.connect()
    engs = ns463.engines(conn)
    e = engs["sql"]
    ns463.rows(e, f"MATCH (n:{LABEL}) DETACH DELETE n")
    for nid, v in (("api_inf", math.inf), ("api_minf", -math.inf), ("api_nan", math.nan)):
        e.create_node(nid, labels=[LABEL], properties={"p": v})
    ns463.rows(e, f"CREATE (:{LABEL} {{id: 'cy_str_inf', p: 'inf'}}), (:{LABEL} {{id: 'cy_str_nan', p: 'NaN'}}), "
                  f"(:{LABEL} {{id: 'cy_num', p: 5}})")
    conn.commit()
    yield engs
    ns463.rows(e, f"MATCH (n:{LABEL}) DETACH DELETE n")
    conn.commit()
    conn.close()


CASES = [
    ("n.p >= $v", math.inf, ["api_inf", "cy_str_inf"]),
    ("n.p < $v", 0, ["api_minf"]),
    ("n.p > $v", -1e308, ["api_inf", "cy_num", "cy_str_inf"]),
    ("n.p <= $v", math.inf, ["api_inf", "api_minf", "cy_num", "cy_str_inf"]),
]


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("cond, v, want", CASES, ids=[c[0] + " " + str(c[1]) for c in CASES])
def test_a_range_filter_reads_infinity_texts(engines, route, cond, v, want):
    rows = ns463.rows(engines[route], f"MATCH (n:{LABEL}) WHERE {cond} RETURN n.id ORDER BY n.id", {"v": v})
    assert [r[0] for r in rows] == want


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("cond", ["n.p >= $v", "n.p < $v", "n.p > $v", "n.p <= $v"])
def test_nan_texts_match_no_range_filter(engines, route, cond):
    for v in (0, math.inf, -math.inf, -1e308):
        rows = ns463.rows(engines[route], f"MATCH (n:{LABEL}) WHERE {cond} AND n.id IN ['api_nan', 'cy_str_nan'] "
                                          f"RETURN n.id", {"v": v})
        assert rows == [], (cond, v, rows)
