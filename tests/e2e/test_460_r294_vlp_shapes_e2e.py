"""4.6.0 R294, live: the engine's variable-length routes answer what the SQL route answers.

Three gaps fixes44 left after R291 (measured 2026-11-05 on 2026.3.0AI, IVG460FZ2, driver 5.4.0, SQL path):

- A leading OPTIONAL MATCH over a variable-length hop went to the engine's routes, which read nothing of an aggregate
  or an expression: `OPTIONAL MATCH p = (a:L)-[:knows*1..2]->(b) RETURN max(length(p)) AS m` answered a column `b`
  of far ends, `RETURN count(r)` counted far ends. It is now translated as the MATCH it is when there is a match
  (R273's rule for fixed-length patterns), with the null row when there is none, so SQL (CY_VLP_PATHS) answers it.
- `RETURN r` of a named path's hop from a start pinned by id went to the engine's labeled route, which built
  `[[":knows"], ...]` (and `RETURN b.name AS n, r` answered `r` alone): the RETURN names the hop's relationship
  variable, which the route does not read, so it expands in SQL. Where the labeled route still answers `RETURN r` (a
  relationship property map SQL does not check, `{w: 1.5}`), it gives SQL's shape: a JSON list of {"type", "props"}.
- With two parallel relationships of one type, the path-function route read each hop's qualifiers by its ends and
  type, so both paths showed the first one's: each hop now reads the row it walked.

The graph, worked by hand (label R294V), in the default graph in multigraph mode for the test (spec 234; set back
after): a -knows {tag: 'one'}-> b, a -knows {tag: 'seven'}-> b, b -knows {tag: 'two'}-> c. The knows
trails of one or two hops: from a, a-b by either edge and a-b-c by either then the tag two edge; from b, b-c. So five paths, two
ending at b and three at c, of lengths 1, 1, 2, 2, 1. And R294F: f1 -hop {w: 1.5}-> f2 -hop {w: 1.5}-> f3, whose
paths from f1 (the one R294F node; f2 and f3 are R294G) whose every hop holds w 1.5 are f1-f2 and f1-f2-f3.

The namespace is `IVG_NAMESPACE` (an IVG460*/IVG262* scratch namespace with IVG set up); the engine runs with
native_cypher=False (the SQL path); the nodes are removed after.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e import ns460alt as alt

def _js(v):
    """A list, map or path value: the Python value (4.6.0 R311), or the JSON text the variable-length BFS routes
    (`_execute_var_length_labeled*`) still build for their own rows."""
    return json.loads(v) if isinstance(v, str) else v


pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

NODES = {"r294a": ("R294V", "a"), "r294b": ("R294V", "b"), "r294c": ("R294V", "c"),
         "r294f1": ("R294F", "f1"), "r294f2": ("R294G", "f2"), "r294f3": ("R294G", "f3")}


def _rel(w, t="knows"):
    return {"type": t, "props": {"w": w}}


def _tag(tag):
    return {"type": "knows", "props": {"tag": tag}}


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    eng = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema, native_cypher=False)
    for nid in NODES:
        eng.delete_node(nid)
    was_multigraph = eng.is_multigraph(None)
    eng.set_multigraph(None, True)  # the default graph may hold two knows edges from a to b (spec 234)
    for nid, (label, name) in NODES.items():
        eng.create_node(nid, labels=[label], properties={"name": name})
    eng.create_edge("r294a", "knows", "r294b", qualifiers={"tag": "one"})
    eng.create_edge("r294b", "knows", "r294c", qualifiers={"tag": "two"})
    # create_edge keeps one edge per (s, p, o) even in multigraph mode; Cypher's CREATE adds the parallel one (with a
    # string: CREATE stores a number in qualifiers as its text, which the SQL route's `r` then shows as text)
    eng.execute_cypher("MATCH (a {node_id: 'r294a'}), (b {node_id: 'r294b'}) CREATE (a)-[:knows {tag: 'seven'}]->(b)",
                       raise_on_error=True)
    eng.create_edge("r294f1", "hop", "r294f2", qualifiers={"w": 1.5})
    eng.create_edge("r294f2", "hop", "r294f3", qualifiers={"w": 1.5})
    eng.sync()
    yield eng
    try:
        for nid in NODES:
            eng.delete_node(nid)
        eng.set_multigraph(None, was_multigraph)
    finally:
        conn.close()


def _run(engine, q):
    r = engine.execute_cypher(q)
    assert getattr(r, "error", None) in (None, ""), (q, r.error)
    return list(r.columns), [list(row) for row in r.rows]


def _bag(rows):
    return sorted(rows, key=repr)


# ------------------------------------------------------------------ a leading OPTIONAL MATCH: aggregates, expressions

OPT = "OPTIONAL MATCH p = (a:R294V)-[:knows*1..2]->(b) "
OPT_R = "OPTIONAL MATCH (a:R294V)-[r:knows*1..2]->(b) "
OPT_ID = "OPTIONAL MATCH p = (a {node_id: 'r294a'})-[:knows*1..2]->(b) "


@pytest.mark.parametrize("ret, cols, rows", [
    ("RETURN max(length(p)) AS m", ["m"], [[2]]),
    ("RETURN count(p) AS c", ["c"], [[5]]),
    ("RETURN length(p) + 1 AS x", ["x"], [[2], [2], [2], [3], [3]]),
    ("RETURN b.name AS n, count(*) AS c", ["n", "c"], [["b", 2], ["c", 3]]),
    ("RETURN a.name AS s, max(length(p)) AS m", ["s", "m"], [["a", 2], ["b", 1]]),
])
def test_a_leading_optional_match_answers_the_aggregate_or_expression(engine, ret, cols, rows):
    got = _run(engine, OPT + ret)
    assert (got[0], _bag(got[1])) == (cols, _bag(rows))
    # the same as the MATCH it is when there is a match
    plain = _run(engine, OPT.replace("OPTIONAL ", "", 1) + ret)
    assert (plain[0], _bag(plain[1])) == (cols, _bag(rows))


def test_a_leading_optional_match_counts_the_relationship_lists(engine):
    assert _run(engine, OPT_R + "RETURN count(r) AS c") == (["c"], [[5]])


def test_a_leading_optional_match_from_an_id_answers_the_aggregate_and_expression(engine):
    assert _run(engine, OPT_ID + "RETURN max(length(p)) AS m") == (["m"], [[2]])
    cols, rows = _run(engine, OPT_ID + "RETURN length(p) + 1 AS x")
    assert (cols, _bag(rows)) == (["x"], [[2], [2], [3], [3]])


def test_a_leading_optional_match_without_a_match_answers_the_null_row(engine):
    none = "OPTIONAL MATCH p = (a:R294V {name: 'c'})-[:knows*1..2]->(b) "
    assert _run(engine, none + "RETURN length(p) AS l") == (["l"], [[None]])
    assert _run(engine, none + "RETURN count(p) AS c") == (["c"], [[0]])


# ------------------------------------------------------------------ RETURN r

BY_ID_R = "MATCH p = (a {node_id: 'r294a'})-[r:knows*1..2]->(b) "
BY_SQL_R = "MATCH p = (a:R294V)-[r:knows*1..2]->(b) WHERE a.name = 'a' "
PATHS_FROM_A = [[_tag("one")], [_tag("seven")], [_tag("one"), _tag("two")], [_tag("seven"), _tag("two")]]


def test_return_r_of_a_named_path_from_an_id_is_the_relationship_list(engine):
    for match in (BY_ID_R, BY_SQL_R):
        cols, rows = _run(engine, match + "RETURN r")
        assert cols == ["r"]
        assert _bag([_js(r[0]) for r in rows]) == _bag(PATHS_FROM_A), match


def test_return_r_beside_a_property_keeps_both_columns(engine):
    want = _bag([["b", p] if len(p) == 1 else ["c", p] for p in PATHS_FROM_A])
    for match in (BY_ID_R, BY_SQL_R):
        cols, rows = _run(engine, match + "RETURN b.name AS n, r")
        assert cols == ["n", "r"], match
        assert _bag([[n, _js(r)] for n, r in rows]) == want, match


def test_the_labeled_route_answers_return_r_in_the_sql_shape(engine):
    """`{w: 1.5}` is a property map CY_VLP_PATHS does not check, so the engine's labeled route answers."""
    cols, rows = _run(engine, "MATCH (a:R294F)-[r:hop*1..2 {w: 1.5}]->(b) RETURN r")
    assert cols == ["r"]
    assert _bag([_js(r[0]) for r in rows]) == _bag([[_rel(1.5, "hop")], [_rel(1.5, "hop"), _rel(1.5, "hop")]])


# ------------------------------------------------------------------ parallel relationships on the path-function route

def test_each_hop_reads_the_relationship_it_walked(engine):
    """`(a {node_id: ...})` with relationships(p) is the engine's path-function route (R291); by label, SQL."""
    want = _bag([["b", p] if len(p) == 1 else ["c", p] for p in PATHS_FROM_A])
    for match in ("MATCH p = (a {node_id: 'r294a'})-[:knows*1..2]->(b) ",
                  "MATCH p = (a:R294V)-[:knows*1..2]->(b) WHERE a.name = 'a' "):
        cols, rows = _run(engine, match + "RETURN b.name AS n, relationships(p) AS r")
        assert cols == ["n", "r"]
        assert _bag([[n, _js(r)] for n, r in rows]) == want, match
