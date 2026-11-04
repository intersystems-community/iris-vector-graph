"""4.6.0 R167: `YIELD <column> AS <name>` answers under the name, on a procedure of each translation shape.

`CALL ivg.ppr(['a']) YIELD node AS n RETURN n` raised `SyntaxError: Undefined variable: n`, and a MATCH on the alias
(`YIELD node AS x MATCH (x)-[:R]->(m)`) matched every node instead of the yielded one. Three procedures stand for the
three ways a procedure is translated: `ivg.ppr` (a CTE over `JSON_TABLE`), `ivg.neighbors` (a CTE over a `UNION` of
two edge reads) and `ivg.ivf.search` (a derived table). Every procedure's translation is checked offline in
`tests/unit/test_460_proc_yield_alias.py`.

The graph, worked by hand: nodes a, b, c labelled L167 with `name` A, B, C, and edges a -R167-> b -R167-> c. Vectors at
the default route's width, zero after the first two: a = [1, 0], b = [1, 1], c = [0, 1].

- `ivg.ppr([a])` yields a, b and c: a is the seed, b and c are reached from it, so every score is above zero.
- `ivg.neighbors([a], 'R167', 'out')` yields b, a's one R167 neighbour.
- `ivg.ivf.search(<one-cell index over a, b, c>, [1, 0, ...], 2, 1)` yields a (dot product 1) and b (1); c is 0. One
  cell is exact.

Then `MATCH (x:L167)-[:R167]->(m) RETURN x.name, m.name` answers, for the yielded nodes, their R167 edges: (A, B) for a
and (B, C) for b.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

NAMES = {"a": "A", "b": "B", "c": "C"}
HEAD = {"a": [1.0, 0.0], "b": [1.0, 1.0], "c": [0.0, 1.0]}
EDGES = {"a": ("A", "B"), "b": ("B", "C")}

#: procedure -> (the call, its node column, its value column or None, the nodes of a, b, c it yields)
PROCS = {
    "ppr": ("CALL ivg.ppr($a, 0.85, 20)", "node", "score", "abc"),
    "neighbors": ("CALL ivg.neighbors($a, 'R167', 'out')", "neighbor", None, "b"),
    "ivf.search": ("CALL ivg.ivf.search('{ivf}', {qlit}, 2, 1)", "node", "score", "ab"),
}


def _width(conn) -> int:
    from iris_vector_graph.schema import GraphSchema

    cursor = conn.cursor()
    try:
        width = GraphSchema.get_embedding_dimension(cursor, "Graph_KG.kg_NodeEmbeddings")
    finally:
        cursor.close()
    assert width, "Graph_KG.kg_NodeEmbeddings declares no vector width"
    return int(width)


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    width = _width(iris_connection)
    run = uuid.uuid4().hex[:8]
    ids = {k: f"ivg460ya_{run}_{k}" for k in "abc"}
    for k, nid in ids.items():
        assert engine.create_node(nid, labels=["L167"], properties={"name": NAMES[k]})
        assert engine.store_embedding(nid, HEAD[k] + [0.0] * (width - 2))
    assert engine.create_edge(ids["a"], "R167", ids["b"])
    assert engine.create_edge(ids["b"], "R167", ids["c"])
    with contextlib.suppress(Exception):
        iris_connection.commit()
    ivf = f"ivg460ya_ivf_{run}"
    engine.ivf_build(ivf, nlist=1, node_ids=list(ids.values()))
    q = [1.0] + [0.0] * (width - 1)
    yield engine, ids, ivf, q
    with contextlib.suppress(Exception):
        engine.ivf_drop(ivf)
    for nid in ids.values():
        engine.delete_node(nid)


def _call(g, proc):
    _, _, ivf, q = g
    call, node_col, value_col, yields = PROCS[proc]
    return call.format(ivf=ivf, qlit="[" + ", ".join(str(x) for x in q) + "]"), node_col, value_col, yields


def _rows(g, query):
    engine, ids, _, _ = g
    r = engine.execute_cypher(query, {"a": [ids["a"]]})
    assert r.error is None, (query, r.error)
    return r


@pytest.mark.parametrize("proc", sorted(PROCS))
def test_the_node_under_its_alias(g, proc):
    _, ids, _, _ = g
    call, node_col, _, yields = _call(g, proc)
    r = _rows(g, f"{call} YIELD {node_col} AS x RETURN x.name AS name")
    by_name = {NAMES[k]: k for k in "abc"}
    got = sorted(by_name[row[0]] for row in r.rows if row[0] in by_name)
    assert got == sorted(yields), r.rows


@pytest.mark.parametrize("proc", sorted(p for p, v in PROCS.items() if v[2]))
def test_the_value_under_its_alias(g, proc):
    call, node_col, value_col, yields = _call(g, proc)
    r = _rows(g, f"{call} YIELD {node_col} AS x, {value_col} AS sc RETURN x.name AS name, sc ORDER BY name")
    rows = [row for row in r.rows if row[0] in NAMES.values()]
    assert [row[0] for row in rows] == sorted(NAMES[k] for k in yields), r.rows
    assert all(isinstance(row[1], (int, float)) and row[1] > 0 for row in rows), rows


@pytest.mark.parametrize("proc", sorted(PROCS))
def test_a_match_on_the_alias_is_the_yielded_node(g, proc):
    call, node_col, _, yields = _call(g, proc)
    r = _rows(g, f"{call} YIELD {node_col} AS x MATCH (x:L167)-[:R167]->(m) RETURN x.name AS n, m.name AS m")
    assert sorted(tuple(row) for row in r.rows) == sorted(EDGES[k] for k in yields if k in EDGES)
