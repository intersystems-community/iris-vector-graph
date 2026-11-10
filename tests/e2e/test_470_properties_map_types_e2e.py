"""4.7.0 row 10, live: `properties(n)` types each value as `n.<key>` reads it.

Measured on 4.6.5 (f1a442e7, IRIS 2026.3, IVG470CY): for a node written by
`create_node(..., properties={"s": "30", "i": 30, "f": 0.5, "b": True})`, `RETURN n.s` answered the string '30' and
`RETURN properties(n)` answered `{'s': 30, ...}`, the number. A Cypher `CREATE` of the same values answered the same.
`CY_PROPS_MAP` read any text that spells a number as a number (the `$Match` fallback after `CY_JSON_NUM`), where
`n.s` reads the stored type.

Expected answers are the values written: the string '30', the integer 30, the float 0.5, the boolean True, and the
node's `id` property (both writers store one: `create_node` writes the node ID as `id`, the `CREATE` names it).
`properties(n).s` already answered '30', natively on the default route; it is checked so it stays that way.

A node written the pre-4.6.4 way: 4.6.3's Python writers bound each number's text (`prop_text(30)`, the string
'30'), with no type marker, the same row a string written on purpose makes. KNOWN_ISSUES ("A property cannot tell a
number from a string that spells it") says `n.i` reads that row as the string; `properties(n)` must agree with `n.i`
on it. Before, it answered the number.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

LABEL = "T470PM"
VALUES = {"s": "30", "i": 30, "f": 0.5, "b": True}
API_ID, CY_ID, OLD_ID = "t470pm:api", "t470pm:cypher", "t470pm:old"
#: what 4.6.3's create_node bound for {"i": 30, "f": 0.5}: each number's text
OLD = {"i": "30", "f": "0.5"}


@pytest.fixture(scope="module")
def engines():
    conn = ns463.connect()
    engs = ns463.engines(conn)
    ns463.rows(engs["sql"], f"MATCH (n:{LABEL}) DETACH DELETE n")
    assert engs["sql"].create_node(API_ID, labels=[LABEL], properties=dict(VALUES))
    ns463.rows(engs["sql"], f"CREATE (:{LABEL} {{id: $id, s: '30', i: 30, f: 0.5, b: true}})", {"id": CY_ID})
    assert engs["sql"].create_node(OLD_ID, labels=[LABEL])
    cur = conn.cursor()
    for k, text in OLD.items():
        schema = engs["sql"]._ivg_schema
        cur.execute(f'INSERT INTO {schema}.rdf_props (graph_id, s, "key", val) '
                    f"SELECT graph_id, node_id, ?, ? FROM {schema}.nodes WHERE node_id = ?", [k, text, OLD_ID])
    conn.commit()
    yield engs
    ns463.rows(engs["sql"], f"MATCH (n:{LABEL}) DETACH DELETE n")
    conn.close()


def _map(value):
    return json.loads(value) if isinstance(value, str) else value


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("nid", [API_ID, CY_ID])
def test_properties_map_types_values_as_written(engines, route, nid):
    got = ns463.rows(engines[route], f"MATCH (n:{LABEL} {{id: $id}}) RETURN properties(n)", {"id": nid})
    assert len(got) == 1
    assert ns463.typed([[_map(got[0][0])]]) == ns463.typed([[{"id": nid, **VALUES}]])


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("nid", [API_ID, CY_ID])
def test_properties_map_agrees_with_each_key_read_alone(engines, route, nid):
    keys = list(VALUES)
    q = f"MATCH (n:{LABEL} {{id: $id}}) RETURN properties(n), {', '.join('n.' + k for k in keys)}"
    got = ns463.rows(engines[route], q, {"id": nid})
    m = _map(got[0][0])
    one_by_one = got[0][1:]
    assert ns463.typed([[m[k] for k in keys]]) == ns463.typed([one_by_one])
    assert ns463.typed([one_by_one]) == ns463.typed([[VALUES[k] for k in keys]])


def test_a_key_of_the_map_reads_the_string_natively(engines):
    native = engines["default"]
    before = getattr(native, "_native_answers", 0)
    got = ns463.rows(native, f"MATCH (n:{LABEL} {{id: $id}}) RETURN properties(n).s", {"id": CY_ID})
    assert ns463.typed(got) == [[("30", "str")]]
    assert getattr(native, "_native_answers", 0) == before + 1, "properties(n).s was not answered natively"
    assert ns463.typed(ns463.rows(engines["sql"], f"MATCH (n:{LABEL} {{id: $id}}) RETURN properties(n).s",
                                  {"id": CY_ID})) == [[("30", "str")]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_number_stored_as_text_reads_alike_in_the_map_and_alone(engines, route):
    got = ns463.rows(engines[route], f"MATCH (n:{LABEL} {{id: $id}}) RETURN properties(n), n.i, n.f", {"id": OLD_ID})
    m = _map(got[0][0])
    assert ns463.typed([[m["i"], m["f"]]]) == ns463.typed([got[0][1:]])
    assert ns463.typed([got[0][1:]]) == [[("30", "str"), ("0.5", "str")]]  # KNOWN_ISSUES: the text reads as text
