"""4.6.0 R301, live: a variable-length relationship list read element by element, a fixed-length relationship's
properties with their types, and a list-valued property map on a variable-length hop read from the start it names.

Measured on the release head (6f9e732f, ivg-iris-enterprise IVG460FX50A, driver 5.4.0, 2026-11-05), over the graph
below:

- `[x IN rs | x.w]` answered `[null]` and `[null,null]`: each element is a `{"type", "props"}` object, and `x.w` read
  `$.w` of it, not `$.props.w`;
- `UNWIND rs AS x RETURN x.w, properties(x)` answered null and `{}` for every row (properties read x as a node id);
- `size(rs)` answered the JSON text's length (33 and 90);
- `MATCH (a {name: 'a'})-[r:R301]->(b) RETURN r` answered `{"type":"R301","props":{"w": "7"}}`, the stored text, where
  `properties(r)` and `r.w` answer the number 7 (R298 typed the variable-length route's `r`; the hard rule: what is
  stored keeps its form, a reader types it);
- `MATCH (a:V301L {name: 'x'})-[r:R301L*1..2 {l: [1]}]->(b) RETURN b.name` answered t and u, reached from s, and
  `RETURN a.name, b.name` only one column: the engine's labeled route read the start by its labels alone.

What openCypher says, worked by hand (no TCK scenario reads a variable-length relationship list's elements; openCypher 9
binds `rs` of `-[rs*1..2]->` to the list of relationships walked, each a relationship as `r` of `-[r]->` is):

- from a over 1..2 R301 hops: b with rs = [a->b] and c with rs = [a->b, b->c];
- `[x IN rs | x.w]`: b [7], c [7, 8]; `size(rs)`: 1, 2;
- `UNWIND rs AS x RETURN b.name, x.w, properties(x)`: (b, 7, {w: 7}), (c, 7, {w: 7}), (c, 8, {w: 8});
- `RETURN r` of a -R301-> b: the relationship of type R301 with w 7, a number;
- relationships whose l is [1]: s->t and t->u; x->s holds l [2]. From x no path's every relationship has l [1]; from s:
  t and u; over every V301L start: (s, t), (s, u), (t, u).

The graph: (:V301 {name: 'a'})-[:R301 {w: 7}]->(:V301 {name: 'b'})-[:R301 {w: 8}]->(:V301 {name: 'c'}) by Cypher
CREATE (which stores 7 as the text '7'); r301s, r301t, r301u, r301x labelled V301L, named s, t, u, x, and their
R301L relationships, by create_node and create_edge (a Cypher CREATE stores a list-valued relationship property as the
text of its parse tree, a defect R301 leaves). Each read runs through an SQL-path engine and a native one
(native_cypher=True), which must agree. The namespace is `IVG_NAMESPACE` (an IVG460*/IVG262* scratch namespace with IVG
set up); this file's nodes are removed before and after.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

LIST_NODES = (("r301s", "s"), ("r301t", "t"), ("r301u", "u"), ("r301x", "x"))
FROM_A = "MATCH (a:V301 {name: 'a'})-[rs:R301*1..2]->(b) "


@pytest.fixture(scope="module")
def engines():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    sql = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema, native_cypher=False)
    native = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema, native_cypher=True)

    def clean():
        sql.execute_cypher("MATCH (n:V301) DETACH DELETE n", raise_on_error=True)
        for nid, _ in LIST_NODES:
            sql.delete_node(nid)

    clean()
    sql.execute_cypher(
        "CREATE (:V301 {name: 'a'})-[:R301 {w: 7}]->(:V301 {name: 'b'})-[:R301 {w: 8}]->(:V301 {name: 'c'})",
        raise_on_error=True,
    )
    for nid, name in LIST_NODES:
        sql.create_node(nid, labels=["V301L"], properties={"name": name})
    sql.create_edge("r301s", "R301L", "r301t", qualifiers={"l": [1]})
    sql.create_edge("r301t", "R301L", "r301u", qualifiers={"l": [1]})
    sql.create_edge("r301x", "R301L", "r301s", qualifiers={"l": [2]})
    sql.sync()
    yield sql, native
    try:
        clean()
    finally:
        conn.close()


def _rows(engines, q, params=None):
    """The rows both engines answer, sorted; both must answer without error and agree."""
    out = []
    for eng in engines:
        r = eng.execute_cypher(q, params or {})
        assert getattr(r, "error", None) in (None, ""), (q, r.error)
        out.append(sorted((list(row) for row in r.rows), key=repr))
    assert out[0] == out[1], (q, out)
    return out[0]


def _js(v):
    return json.loads(v) if isinstance(v, str) else v


def test_a_comprehension_reads_each_relationship_s_property(engines):
    rows = _rows(engines, FROM_A + "RETURN b.name, [x IN rs | x.w]")
    assert [(r[0], _js(r[1])) for r in rows] == [("b", [7]), ("c", [7, 8])]


def test_an_unwound_relationship_reads_its_properties(engines):
    rows = _rows(engines, FROM_A + "UNWIND rs AS x RETURN b.name, x.w, properties(x)")
    assert sorted((r[0], r[1], _js(r[2])) for r in rows) == [("b", 7, {"w": 7}), ("c", 7, {"w": 7}),
                                                             ("c", 8, {"w": 8})]


def test_the_size_of_a_relationship_list_counts_relationships(engines):
    assert _rows(engines, FROM_A + "RETURN b.name, size(rs)") == [["b", 1], ["c", 2]]


def test_a_fixed_length_relationship_shows_its_typed_properties(engines):
    rows = _rows(engines, "MATCH (a:V301 {name: 'a'})-[r:R301]->(b) RETURN r")
    assert [_js(r[0]) for r in rows] == [{"type": "R301", "props": {"w": 7}}]


def test_a_list_valued_property_map_reads_the_start_it_names(engines):
    q = "MATCH (a:V301L {name: $n})-[r:R301L*1..2 {l: [1]}]->(b) RETURN b.name"
    assert _rows(engines, q, {"n": "x"}) == []
    assert _rows(engines, q, {"n": "s"}) == [["t"], ["u"]]
    assert _rows(engines, "MATCH (a:V301L)-[r:R301L*1..2 {l: [1]}]->(b) RETURN a.name, b.name") == [
        ["s", "t"], ["s", "u"], ["t", "u"]]
