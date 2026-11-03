"""4.6.0 R068: `MATCH (n {node_id: $x})-[:R*2]->(m)` answers one row per path under the column RETURN names.

The `[:R*2]` shortcuts in `_try_khop_fast_path` (Graph.KG.Traversal.KHop2NeighborIds / KHop2CountExact) answered the
nodes first reached at distance two, each once, under the column `node_id`, and counted those. openCypher's `*2` is
every path of exactly two relationships, none used twice, one row per path, whatever else reaches the end sooner (the
same class as #80, fixed for `*1..K` in R058). The 1-hop shortcut named its column `node_id` too.

The graph, over R: a->b, a->c, a->e, a->a, b->c, b->d, b->b, c->a, e->d; and over S (not R, so never on a path):
a-S->x, x-R->y. Paths of two R relationships from a, worked by hand:

    a->b then b->c, b->d, b->b      ends c, d, b
    a->c then c->a                  end  a
    a->e then e->d                  end  d
    a->a then a->b, a->c, a->e      ends b, c, e   (a->a twice uses one relationship twice, so it is not a path)

Eight paths, ends a, b, b, c, c, d, d, e. The shortcut answered [d] and a count of 1: b, c and e are one hop from a,
and a is the start. One R hop from a: a, b, c, e.

The shortcut answers only when the default graph is the only one holding edges, so the test checks that first.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

ENDS = ["a", "b", "b", "c", "c", "d", "d", "e"]


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    r, s = f"IVG460KH_R_{run}", f"IVG460KH_S_{run}"
    ids = {k: f"ivg460kh_{run}_{k}" for k in "abcdexy"}
    for nid in ids.values():
        engine.create_node(nid, labels=["Ivg460KH"])
    for a, p, b in (("a", r, "b"), ("a", r, "c"), ("a", r, "e"), ("a", r, "a"), ("b", r, "c"), ("b", r, "d"),
                    ("b", r, "b"), ("c", r, "a"), ("e", r, "d"), ("a", s, "x"), ("x", r, "y")):
        engine.create_edge(ids[a], p, ids[b])
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield engine, ids, r
    for nid in ids.values():
        with contextlib.suppress(Exception):
            engine.delete_node(nid, graph=None)


def _ask(g, cypher: str):
    engine, ids, r = g
    assert engine._edge_graphs() in ([], [""]), "another graph holds edges: the shortcut would not be asked"
    result = engine.execute_cypher(cypher.replace("[:R", f"[:{r}", 1), {"x": ids["a"]})
    assert not getattr(result, "error", None), result.error
    inverse = {v: k for k, v in ids.items()}
    return list(result.columns), [[inverse.get(v, v) for v in row] for row in result.rows]


def test_two_hop_ids_one_row_per_path_under_the_return_column(g):
    cols, rows = _ask(g, "MATCH (n {node_id: $x})-[:R*2]->(m) RETURN m.node_id")
    assert cols == ["m.node_id"]
    assert sorted(row[0] for row in rows) == ENDS


def test_two_hop_ids_aliased(g):
    cols, rows = _ask(g, "MATCH (n {node_id: $x})-[:R*2]->(m) RETURN m.node_id AS id")
    assert cols == ["id"]
    assert sorted(row[0] for row in rows) == ENDS


def test_two_hop_ids_with_limit_are_three_of_the_paths(g):
    cols, rows = _ask(g, "MATCH (n {node_id: $x})-[:R*2]->(m) RETURN m.node_id LIMIT 3")
    assert cols == ["m.node_id"]
    ends = [row[0] for row in rows]
    assert len(ends) == 3 and all(ends.count(e) <= ENDS.count(e) for e in ends), ends


def test_two_hop_count_counts_paths(g):
    cols, rows = _ask(g, "MATCH (n {node_id: $x})-[:R*2]->(m) RETURN count(m) AS total")
    assert (cols, rows) == (["total"], [[8]])


def test_one_hop_ids_under_the_return_column(g):
    cols, rows = _ask(g, "MATCH (n {node_id: $x})-[:R]->(m) RETURN m.node_id")
    assert cols == ["m.node_id"]
    assert sorted(row[0] for row in rows) == ["a", "b", "c", "e"]


def test_one_hop_count(g):
    cols, rows = _ask(g, "MATCH (n {node_id: $x})-[:R]->(m) RETURN count(m) AS total")
    assert (cols, rows) == (["total"], [[4]])
