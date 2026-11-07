"""4.6.0 R305, live: a CREATE or MERGE between matched endpoints writes every relationship property, a list or a map
too, and `SET r.k = <list>` stores a JSON list.

Found by fixes50 on the release head (ivg-iris-enterprise IVG460FX50A, 2026-11-05): `MATCH (a), (b) CREATE
(a)-[:T {l: [1, 2], w: 7}]->(b)` wrote the relationship with no properties (`properties(r)` {}): w lost too. So did a
map value (`{m: {a: 1}, w: 7}`), the MERGE of such a pattern, and a property computed from the row (`{w: a.id}`,
which R305 does not cover: KNOWN_ISSUES). `SET r.m = [3, 4]` stored the JSON string "[3, 4]", so `r.m` read the text.

By hand: each relationship holds l [1, 2], m {k: 'x'} and w 7; `properties(r)` is {l: [1, 2], m: {k: 'x'}, w: 7} and
the stored qualifiers are {"l": [1, 2], "m": {"k": "x"}, "w": "7"} (a number keeps the text a Cypher write stores; the
reader types it). A MERGE of the pattern that exists writes nothing (TCK Merge5 [15], "Matching using list
property"). After `SET r.n = [3, 4]` the stored n is the JSON list [3, 4] and `r.n` is [3, 4]; `SET r.t = '[3, 4]'`
still stores the string, which `r.t` reads back as the list (KNOWN_ISSUES, "A stored string that spells a boolean, a
list or a map reads as one": a strict xfail here, so the fix shows).

The namespace is `IVG_NAMESPACE` (an IVG460*/IVG262* scratch namespace with IVG set up); this file's label is removed
before and after.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

PROPS = "{l: [1, 2], m: {k: 'x'}, w: 7}"
WANT = {"l": [1, 2], "m": {"k": "x"}, "w": 7}
STORED = {"l": [1, 2], "m": {"k": "x"}, "w": "7"}


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    eng = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema)
    eng.execute_cypher("MATCH (n:Z305) DETACH DELETE n", raise_on_error=True)
    eng.execute_cypher("CREATE (:Z305 {name: 'a'}), (:Z305 {name: 'b'}), (:Z305 {name: 'c'})", raise_on_error=True)
    m = "MATCH (a:Z305 {name: 'a'}), (b:Z305 {name: $b}) "
    eng.execute_cypher(m + f"CREATE (a)-[:C305 {PROPS}]->(b)", {"b": "b"}, raise_on_error=True)
    eng.execute_cypher(m + f"MERGE (a)-[:M305 {PROPS}]->(b)", {"b": "c"}, raise_on_error=True)
    yield eng, conn, schema
    try:
        eng.execute_cypher("MATCH (n:Z305) DETACH DELETE n", raise_on_error=True)
    finally:
        conn.close()


def _js(v):
    return json.loads(v) if isinstance(v, str) else v


def _stored(engine, rel_type):
    _, conn, schema = engine
    cur = conn.cursor()
    cur.execute(f"SELECT qualifiers FROM {schema}.rdf_edges WHERE p = ?", [rel_type])
    rows = [json.loads(r[0]) if r[0] else None for r in cur.fetchall()]
    cur.close()
    return rows


@pytest.mark.parametrize("rel_type", ["C305", "M305"])
def test_every_property_between_matched_endpoints_is_written(engine, rel_type):
    eng = engine[0]
    r = eng.execute_cypher(f"MATCH (:Z305)-[r:{rel_type}]->() RETURN properties(r), r.l", raise_on_error=True)
    assert [(_js(row[0]), _js(row[1])) for row in r.rows] == [(WANT, [1, 2])]
    assert _stored(engine, rel_type) == [STORED]


def test_a_merge_of_the_existing_pattern_writes_nothing(engine):
    eng = engine[0]
    r = eng.execute_cypher(
        f"MATCH (a:Z305 {{name: 'a'}}), (b:Z305 {{name: 'b'}}) MERGE (a)-[r:C305 {PROPS}]->(b) RETURN count(*)",
        raise_on_error=True,
    )
    assert r.rows == [[1]]
    assert _stored(engine, "C305") == [STORED]


def _set_n_and_t(eng):
    eng.execute_cypher("MATCH (:Z305)-[r:C305]->() SET r.n = [3, 4], r.t = '[3, 4]'", raise_on_error=True)
    return eng.execute_cypher("MATCH (:Z305)-[r:C305]->() RETURN r.n, r.t", raise_on_error=True)


def test_a_set_of_a_list_stores_a_json_list(engine):
    eng = engine[0]
    r = _set_n_and_t(eng)
    assert [_js(row[0]) for row in r.rows] == [[3, 4]]
    stored = _stored(engine, "C305")[0]
    assert (stored["n"], stored["t"]) == ([3, 4], "[3, 4]"), stored


@pytest.mark.xfail(
    strict=True,
    reason=(
        "docs/KNOWN_ISSUES.md, 'A stored string that spells a boolean, a list or a map reads as one': the string "
        "'[3, 4]' is stored as text (the test above) but r.t reads back as the list [3, 4]"
    ),
)
def test_a_set_of_a_string_that_spells_a_list_reads_back_the_string(engine):
    eng = engine[0]
    r = _set_n_and_t(eng)
    assert [(_js(row[0]), row[1]) for row in r.rows] == [([3, 4], "[3, 4]")]
