"""4.6.0 R304, live: a relationship property a Cypher CREATE or MERGE writes as a list is stored as a JSON list, as
create_edge stores it, and reads back as that list.

Found by fixes50 on the release head (6f9e732f, ivg-iris-enterprise IVG460FX50A, 2026-11-05): `CREATE (:Z304 {name:
'a'})-[:R304 {l: [1, 2]}]->(:Z304 {name: 'b'})` stored the qualifiers `{"l": "[Literal(value=1), Literal(value=2)]"}`,
the parse tree's text, and `r.l` answered that string. So did the MERGE of a whole pattern.

By hand: each relationship below holds l [1, 2], s ['a', 'b'] and w 7; `r.l` is [1, 2], `properties(r)` {l: [1, 2],
s: ['a', 'b'], w: 7}, and the stored qualifiers are the JSON {"l": [1, 2], "s": ["a", "b"], "w": "7"}: the list a JSON
list, the number its text as a Cypher write stores every number (unchanged; the reader types it).

The namespace is `IVG_NAMESPACE` (an IVG460*/IVG262* scratch namespace with IVG set up); this file's label is removed
before and after.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

PROPS = "{l: [1, 2], s: ['a', 'b'], w: 7}"
WRITES = {
    "create": f"CREATE (:Z304 {{name: 'c1'}})-[:R304 {PROPS}]->(:Z304 {{name: 'c2'}})",
    "create_ids": f"CREATE (:Z304 {{id: 'r304i1', name: 'i1'}})-[:R304 {PROPS}]->(:Z304 {{id: 'r304i2', name: 'i2'}})",
    "merge": f"MERGE (:Z304 {{id: 'r304m1', name: 'm1'}})-[:R304 {PROPS}]->(:Z304 {{id: 'r304m2', name: 'm2'}})",
}


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    eng = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema)
    eng.execute_cypher("MATCH (n:Z304) DETACH DELETE n", raise_on_error=True)
    for q in WRITES.values():
        eng.execute_cypher(q, raise_on_error=True)
    yield eng, conn, schema
    try:
        eng.execute_cypher("MATCH (n:Z304) DETACH DELETE n", raise_on_error=True)
    finally:
        conn.close()


def _js(v):
    return json.loads(v) if isinstance(v, str) else v


@pytest.mark.parametrize("start", ["c1", "i1", "m1"])
def test_a_written_list_reads_back_as_the_list(engine, start):
    eng, _, _ = engine
    r = eng.execute_cypher("MATCH (a:Z304 {name: $n})-[r:R304]->() RETURN r.l, properties(r)", {"n": start},
                           raise_on_error=True)
    assert len(r.rows) == 1, r.rows
    assert _js(r.rows[0][0]) == [1, 2]
    assert _js(r.rows[0][1]) == {"l": [1, 2], "s": ["a", "b"], "w": 7}


def test_the_stored_qualifiers_are_json_lists(engine):
    _, conn, schema = engine
    cur = conn.cursor()
    cur.execute(
        f"SELECT e.qualifiers FROM {schema}.rdf_edges e JOIN {schema}.rdf_labels l ON l.s = e.s AND l.label = 'Z304' "
        "WHERE e.p = 'R304'"
    )
    stored = [json.loads(row[0]) for row in cur.fetchall()]
    cur.close()
    assert stored == [{"l": [1, 2], "s": ["a", "b"], "w": "7"}] * 3, stored
