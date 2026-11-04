"""4.6.0 R156: every `CALL ivg.* YIELD <node>` keeps answering when a MATCH, a WITH or a RETURN of the node follows.

`CALL ivg.ppr(['a']) YIELD node, score MATCH (node)-[:R]->(m) RETURN m` joined `PPR.node_id`, a column the CTE does
not have, and IRIS refused it (SQLCODE -29): the query answered no rows. R118 fixed the edge join for
`ivg.vector.search` alone; a label or a property on its yielded node read `VecSearch.node_id` too.

The graph, worked by hand, in the default graph (the centrality procedures read the default graph's ^KG): nodes a, b,
c labelled L156 with `name` A, B, C and `text` "apple", "apple banana", "cherry"; edges a -R156-> b -R156-> c. Vectors
at the default route's width, zero after the first two: a = [1, 0], b = [1, 1], c = [0, 1].

What each procedure yields, of a, b, c:
- `ivg.neighbors($a, 'R156', 'out')`: b, a's one R156 neighbour.
- `ivg.ppr($a)`: a, b and c (a is the seed; b and c are reached from it, so their scores are above zero).
- `ivg.vector.search('L156', 'embedding', [1, 0, ...], 2)`: a (cosine 1) and b (1/sqrt(2)); c is 0.
- `ivg.bm25.search(<index over text>, 'apple', 5)`: a and b, the two documents holding "apple".
- `ivg.ivf.search(<one-cell index over a, b, c>, [1, 0, ...], 2, 1)`: a and b, as the vector search (one cell is
  exact).
- `ivg.retrieve('apple', 10, <bm25 index>, 'L156')`: a, b (BM25 and vector arms) and c (the vector arm, label L156,
  embedded by a stub that answers [1, 0, ...]).
- `ivg.betweenness({})`: b alone. b lies on the one shortest path between two other nodes (a to c), so its score is
  1; a and c lie on none, score 0, and the procedure leaves zero scores out (`BetweennessGlobalParallel`).
- the other centrality and community procedures: every node in the default graph, so a, b and c.

Then `MATCH (v:L156)-[:R156]->(m) RETURN v.name, m.name` answers, for the yielded nodes, their R156 edges: (A, B) for
a, (B, C) for b, none for c. The same after `WITH v`. `RETURN v` answers the yielded nodes, each with its own name.
"""

from __future__ import annotations

import contextlib
import json
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

NAMES = {"a": "A", "b": "B", "c": "C"}
TEXTS = {"a": "apple", "b": "apple banana", "c": "cherry"}
HEAD = {"a": [1.0, 0.0], "b": [1.0, 1.0], "c": [0.0, 1.0]}

#: procedure, the yielded node variable, and the nodes of a, b, c it yields
PROCS = {
    "neighbors": ("CALL ivg.neighbors($a, 'R156', 'out') YIELD neighbor", "neighbor", "b"),
    "ppr": ("CALL ivg.ppr($a, 0.85, 20) YIELD node, score", "node", "abc"),
    "vector.search": ("CALL ivg.vector.search('L156', 'embedding', $q, 2) YIELD node, score", "node", "ab"),
    "bm25.search": ("CALL ivg.bm25.search('{bm25}', 'apple', 5) YIELD node, score", "node", "ab"),
    "ivf.search": ("CALL ivg.ivf.search('{ivf}', {qlit}, 2, 1) YIELD node, score", "node", "ab"),
    "retrieve": ("CALL ivg.retrieve('apple', 10, '{bm25}', 'L156') YIELD node, rrf_score", "node", "abc"),
    "degreeCentrality": ("CALL ivg.degreeCentrality({{direction: 'out'}}) YIELD node, score", "node", "abc"),
    "betweenness": ("CALL ivg.betweenness({{}}) YIELD node, score", "node", "b"),
    "closeness": ("CALL ivg.closeness({{}}) YIELD node, score", "node", "abc"),
    "eigenvector": ("CALL ivg.eigenvector({{}}) YIELD node, score", "node", "abc"),
    "leiden": ("CALL ivg.leiden({{}}) YIELD node, community", "node", "abc"),
    "triangleCount": ("CALL ivg.triangleCount({{}}) YIELD node, triangles", "node", "abc"),
    "scc": ("CALL ivg.scc({{}}) YIELD node, component", "node", "abc"),
    "kcore": ("CALL ivg.kcore({{}}) YIELD node, coreness", "node", "abc"),
}

EDGES = {"a": ("A", "B"), "b": ("B", "C")}


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
    ids = {k: f"ivg460y_{run}_{k}" for k in "abc"}
    vec = {k: HEAD[k] + [0.0] * (width - 2) for k in "abc"}
    q = [1.0] + [0.0] * (width - 1)
    engine.embedder = lambda _text: q
    for k, nid in ids.items():
        assert engine.create_node(nid, labels=["L156"], properties={"name": NAMES[k], "text": TEXTS[k]})
        assert engine.store_embedding(nid, vec[k])
    engine.create_edge(ids["a"], "R156", ids["b"])
    engine.create_edge(ids["b"], "R156", ids["c"])
    engine.sync()  # the centrality and community procedures read ^NKG
    with contextlib.suppress(Exception):
        iris_connection.commit()
    bm25, ivf = f"ivg460y_bm25_{run}", f"ivg460y_ivf_{run}"
    engine.bm25_build(bm25, ["text"])
    engine.ivf_build(ivf, nlist=1, node_ids=list(ids.values()))
    yield engine, ids, q, bm25, ivf
    with contextlib.suppress(Exception):
        engine.bm25_drop(bm25)
    with contextlib.suppress(Exception):
        engine.ivf_drop(ivf)
    for nid in ids.values():
        engine.delete_node(nid)


def _call(g, proc):
    _, ids, q, bm25, ivf = g
    call, var, yields = PROCS[proc]
    qlit = "[" + ", ".join(str(x) for x in q) + "]"
    return call.format(bm25=bm25, ivf=ivf, qlit=qlit), var, yields


def _run(g, query):
    engine, ids, q, _, _ = g
    r = engine.execute_cypher(query, {"a": ids["a"], "q": q})
    assert r.error is None, r.error
    return r


@pytest.mark.parametrize("proc", sorted(PROCS))
def test_a_match_on_the_yielded_node(g, proc):
    call, v, yields = _call(g, proc)
    r = _run(g, f"{call} MATCH ({v}:L156)-[:R156]->(m) RETURN {v}.name AS n, m.name AS m")
    assert sorted(tuple(row) for row in r.rows) == sorted(EDGES[k] for k in yields if k in EDGES)


@pytest.mark.parametrize("proc", sorted(PROCS))
def test_a_with_then_a_match_on_the_yielded_node(g, proc):
    call, v, yields = _call(g, proc)
    r = _run(g, f"{call} WITH {v} MATCH ({v}:L156)-[:R156]->(m) RETURN {v}.name AS n, m.name AS m")
    assert sorted(tuple(row) for row in r.rows) == sorted(EDGES[k] for k in yields if k in EDGES)


@pytest.mark.parametrize("proc", sorted(PROCS))
def test_a_return_of_the_yielded_node(g, proc):
    # a node comes back as its id, labels and properties: `<v>_id`, `<v>_labels`, `<v>_props` (as `MATCH (n) RETURN n`)
    _, ids, _, _, _ = g
    call, v, yields = _call(g, proc)
    r = _run(g, f"{call} RETURN {v}")
    at_id, at_props = r.columns.index(f"{v}_id"), r.columns.index(f"{v}_props")
    by_id = {nid: k for k, nid in ids.items()}
    names = {}
    for row in r.rows:
        if row[at_id] in by_id:
            props = {p["key"]: p["value"] for p in map(json.loads, json.loads(row[at_props]))}
            names[by_id[row[at_id]]] = props.get("name")
    assert names == {k: NAMES[k] for k in yields}
