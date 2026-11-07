"""4.6.0 R298, live: fixes46's leftovers, each on the SQL path and the native one, under whichever driver runs this.

Measured on the release head (IVG460C48, Community 2026.1, drivers 5.4.0 and 5.4.1, 2026-11-05):

- the null row of a leading OPTIONAL MATCH with no match held NULL for every column: `OPTIONAL MATCH (a:L {name:
  'c'})-[:knows]->(b) RETURN b.name, count(*)` answered [null, null], and `count(b)` and `collect(b.name)` null; with
  the aggregates alone (`RETURN count(*), count(b)`) it answered [0, 0]. The same over a variable-length hop.
- a relationship number a Cypher CREATE stored as text (qualifiers {"w": "7"}) read as text in the variable-length
  routes' relationship lists: `RETURN t.name, r` and `relationships(p)` on the SQL route (CY_VLP_PATHS) and on the
  engine's path-function route gave {"w": "7"}, where `properties(r)` and `r.w` answer 7.
- `[r*1..2 {w: 1.5}]` (a property map that is not a string or an integer) went to the engine's labeled route, which
  ignored the start's property map, answered the column r alone for `RETURN t.name, r`, and kept a far end when any
  relationship into it matched; from an id-bound start it answered no rows under the columns t_name, b_labels,
  b_props. `{w: 7.0}` matched nothing where `{w: 7}` matched (openCypher: 7 = 7.0). A boolean map (`{f: true}`)
  matched nothing at all (the route compared str(True) with the stored 'true').

The graph, worked by hand (label L298, every relationship `knows`, created by Cypher, so each w is stored as text):
a -{w: 1.5}-> b -{w: 1.5}-> c, a -{w: 7}-> d -{w: 1.5}-> e, and x -{w: 1.5}-> y. c has no outgoing relationship.
Beside them, of type `flag`: b -{f: true}-> g -{f: false}-> h, g -{f: 1}-> k, x -{f: true}-> k.

- OPTIONAL MATCH from c: one null row, so count(*) is 1, count(b) 0, collect(b.name) [] (as a MATCH of c followed by
  an OPTIONAL MATCH answers); from a: b and d, one each.
- the paths from a of length 1..2: b [1.5], c [1.5, 1.5], d [7], e [7, 1.5]; 7 an integer, 1.5 a float.
- every relationship w = 1.5, from a: b [1.5] and c [1.5, 1.5] (not e, whose first relationship is 7; not y, whose
  start is x). w = 7 or 7.0: d.
- f = true from b: g (not h: false; not k: 1 is no boolean). f = false from g: h; from b nothing. f = 1 from g: k;
  from b nothing (true is not 1).

The namespace is `IVG_NAMESPACE` (an IVG460*/IVG262* scratch namespace with IVG set up); the label is this file's own.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]


@pytest.fixture(scope="module")
def engines():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    sql = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema, native_cypher=False)
    native = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema, native_cypher=True)
    sql.execute_cypher("MATCH (n:L298) DETACH DELETE n", raise_on_error=True)
    sql.execute_cypher(
        "CREATE (a:L298 {name: 'a'}), (b:L298 {name: 'b'}), (c:L298 {name: 'c'}), (d:L298 {name: 'd'}), "
        "(e:L298 {name: 'e'}), (x:L298 {name: 'x'}), (y:L298 {name: 'y'}), "
        "(a)-[:knows {w: 1.5}]->(b), (b)-[:knows {w: 1.5}]->(c), (a)-[:knows {w: 7}]->(d), "
        "(d)-[:knows {w: 1.5}]->(e), (x)-[:knows {w: 1.5}]->(y), "
        "(g:L298 {name: 'g'}), (h:L298 {name: 'h'}), (k:L298 {name: 'k'}), "
        "(b)-[:flag {f: true}]->(g), (g)-[:flag {f: false}]->(h), (x)-[:flag {f: true}]->(k), (g)-[:flag {f: 1}]->(k)",
        raise_on_error=True,
    )
    a_id = sql.execute_cypher("MATCH (a:L298 {name: 'a'}) RETURN id(a)", raise_on_error=True).rows[0][0]
    yield sql, native, {"id": a_id}
    try:
        sql.execute_cypher("MATCH (n:L298) DETACH DELETE n", raise_on_error=True)
    finally:
        conn.close()


def _same(engines, q):
    """The rows both engines answer, sorted; both must answer without error and agree."""
    sql, native, params = engines
    out = []
    for eng in (sql, native):
        r = eng.execute_cypher(q, params)
        assert getattr(r, "error", None) in (None, ""), (q, r.error)
        out.append(sorted((list(row) for row in r.rows), key=repr))
    assert out[0] == out[1], (q, out)
    return out[0]


def _ws(rels):
    """A relationship list's w values, each with its type: [(1.5, 'float'), (7, 'int')]."""
    return [(r["props"]["w"], type(r["props"]["w"]).__name__) for r in rels]


# ── the null row's aggregates ────────────────────────────────────────────────


def test_the_null_row_counts_itself_and_no_value(engines):
    assert _same(engines, "OPTIONAL MATCH (a:L298 {name: 'c'})-[:knows]->(b) RETURN b.name, count(*)") == [[None, 1]]
    assert _same(engines, "OPTIONAL MATCH (a:L298 {name: 'c'})-[:knows]->(b) RETURN b.name, count(b), "
                          "collect(b.name), count(DISTINCT b)") == [[None, 0, [], 0]]  # 4.6.0 R311: the list
    assert _same(engines, "OPTIONAL MATCH (a:L298 {name: 'c'})-[:knows]->(b) RETURN count(*), count(b)") == [[1, 0]]


def test_the_null_row_of_a_variable_length_hop_too(engines):
    q = "OPTIONAL MATCH (a:L298 {name: 'c'})-[:knows*1..2]->(b) RETURN b.name, count(*)"
    assert _same(engines, q) == [[None, 1]]
    assert _same(engines, "OPTIONAL MATCH (a:L298 {name: 'c'})-[:knows*1..2]->(b) RETURN count(*), count(b)") == [
        [1, 0]]


def test_a_match_still_counts_its_rows(engines):
    assert _same(engines, "OPTIONAL MATCH (a:L298 {name: 'a'})-[:knows]->(b) RETURN b.name, count(*)") == [
        ["b", 1], ["d", 1]]
    assert _same(engines, "OPTIONAL MATCH (a:L298 {name: 'a'})-[:knows]->(b) RETURN count(*), count(b)") == [[2, 2]]
    assert _same(engines, "MATCH (a:L298 {name: 'c'}) OPTIONAL MATCH (a)-[:knows]->(b) "
                          "RETURN a.name, count(b), count(*), collect(b.name)") == [["c", 0, 1, []]]


# ── a relationship number stored as text reads as the number ─────────────────

PATHS_FROM_A = {
    "b": [(1.5, "float")],
    "c": [(1.5, "float"), (1.5, "float")],
    "d": [(7, "int")],
    "e": [(7, "int"), (1.5, "float")],
}


@pytest.mark.parametrize(
    "q",
    [
        "MATCH (a:L298 {name: 'a'})-[r:knows*1..2]->(t) RETURN t.name, r",
        "MATCH p = (a:L298 {name: 'a'})-[:knows*1..2]->(t) RETURN t.name, relationships(p)",
        "MATCH p = (a {node_id: $id})-[:knows*1..2]->(t) RETURN t.name, relationships(p)",
        "MATCH (a {node_id: $id})-[r:knows*1..2]->(t) RETURN t.name, r",
    ],
)
def test_a_relationship_list_holds_the_stored_numbers_as_numbers(engines, q):
    rows = _same(engines, q)
    assert {name: _ws(rels) for name, rels in rows} == PATHS_FROM_A, rows
    assert len(rows) == 4, rows


# ── a property map on a variable-length hop holds every relationship ───────


@pytest.mark.parametrize(
    "q",
    [
        "MATCH (a:L298 {name: 'a'})-[r*1..2 {w: 1.5}]->(t) RETURN t.name, r",
        "MATCH (a:L298 {name: 'a'})-[r:knows*1..2 {w: 1.5}]->(t) RETURN t.name, r",
        "MATCH (a {node_id: $id})-[r:knows*1..2 {w: 1.5}]->(t) RETURN t.name, r",
    ],
)
def test_a_float_property_map_reads_the_start_and_every_relationship(engines, q):
    rows = _same(engines, q)
    assert [[name, _ws(rels)] for name, rels in rows] == [
        ["b", [(1.5, "float")]], ["c", [(1.5, "float"), (1.5, "float")]]], rows


def test_a_float_property_map_s_far_ends_and_count(engines):
    assert _same(engines, "MATCH (a:L298 {name: 'a'})-[:knows*1..2 {w: 1.5}]->(t) RETURN t.name") == [["b"], ["c"]]
    assert _same(engines, "MATCH (a:L298 {name: 'a'})-[:knows*1..2 {w: 1.5}]->(t) RETURN count(t)") == [[2]]


@pytest.mark.parametrize("w", ["7", "7.0"])
def test_an_integer_equals_its_float(engines, w):
    assert _same(engines, f"MATCH (a:L298 {{name: 'a'}})-[:knows*1..2 {{w: {w}}}]->(t) RETURN t.name") == [["d"]]


def test_a_boolean_property_map_is_a_boolean_not_one(engines):
    # b -flag {f: true}-> g -flag {f: false}-> h, g -flag {f: 1}-> k, x -flag {f: true}-> k
    assert _same(engines, "MATCH (s:L298 {name: 'b'})-[:flag*1..2 {f: true}]->(t) RETURN t.name") == [["g"]]
    rows = _same(engines, "MATCH (s:L298 {name: 'b'})-[r*1..2 {f: true}]->(t) RETURN t.name, r")
    assert [[n, [x["props"]["f"] for x in r]] for n, r in rows] == [["g", [True]]], rows
    assert _same(engines, "MATCH (s:L298 {name: 'g'})-[:flag*1..2 {f: false}]->(t) RETURN t.name") == [["h"]]
    assert _same(engines, "MATCH (s:L298 {name: 'b'})-[:flag*1..2 {f: false}]->(t) RETURN t.name") == []
    assert _same(engines, "MATCH (s:L298 {name: 'g'})-[:flag*1..2 {f: 1}]->(t) RETURN t.name") == [["k"]]
    assert _same(engines, "MATCH (s:L298 {name: 'b'})-[:flag*1..2 {f: 1}]->(t) RETURN t.name") == []  # true is not 1
