"""`EraseGraph` and `EraseEdges` leave no stale ^NKG, and keep one nothing damaged.

`Graph.KG.Eraser:EraseGraph` read `tHadEdges = $Data(^KG("out", graphKey))` AFTER it had
deleted the `rdf_edges` rows. Since spec 252 the functional index on that table clears
`^KG("out", graph)` as the rows go, so the read was always 0 for a graph whose edges are
SQL rows, `InvalidateDerived(0)` never dropped ^NKG, and an index the erase had damaged
was left standing.

What it damages is not the adjacency (the per-row index removes `^NKG(-1)`, `(-2)` and
decrements `(-3)`) but what no per-row path can undo: `^NKG("$meta","edgeCount")` and the
HyperLogLog sketches `^NKG("$agg", s, p, "hll")` that `CountDistinctKHop` merges. A sketch
cannot have an element taken out of it. So, with the erased graph's node ids shared with a
surviving graph, `approx_count_distinct` answered with the erased graph's far ends.

These tests assert the state after the call, on the server:

* a row-backed graph erase drops ^NKG, and `CountDistinctKHop` / `approx_count_distinct`
  answer as a rebuild does;
* an adjacency-only graph (no rows) still drops it, when its ends are interned;
* a graph none of whose edges can be in ^NKG (no interned pair of ends) leaves it standing,
  the sentinel planted in it intact, and ^NKG still a subset of ^KG;
* a surviving graph that shares ids and a triple with the erased one keeps its edges:
  ^NKG, kept or rebuilt, never holds an edge ^KG lacks, and never lacks one ^KG has;
* `EraseEdges` of an adjacency-only edge (no row, so no row count to trip the drop) drops ^NKG.

`SKIP_IRIS_TESTS=true` fails rather than skips: a skipped test that asserts what a Kill
left behind is indistinguishable from a passing one.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.e2e.test_erase_nkg_kept_e2e import (
    SENTINEL,
    _interned,
    _kg_edges,
    _nkg,
    _nkg_edges,
    _require_iris,
)

pytestmark = [pytest.mark.e2e]

PRED = "IVGERASEGRAPH_LINK"


class World:
    """Two graphs over shared node ids.

    survivor: a -> b            (and b -> c when `share_triple`)
    erased:   b -> c, b -> d    (b, and c, are the survivor's ids too)

    `b` is the shared id: its sketch holds the erased graph's far ends c and d.
    """

    def __init__(self, engine, iris_obj, conn):
        tag = uuid.uuid4().hex[:8]
        self.engine, self.iris, self.conn = engine, iris_obj, conn
        self.survivor = f"ivgeg-s-{tag}"
        self.erased = f"ivgeg-e-{tag}"
        self.a, self.b, self.c, self.d = (f"ivgeg{tag}:{n}" for n in "abcd")

    def node(self, node_id: str, graph: str):
        self.engine.create_node(node_id, labels=["IvgEraseGraph"], properties={"k": node_id}, graph=graph)

    def build(self, share_triple: bool = False, build_nkg: bool = True):
        e = self.engine
        for n in (self.a, self.b):
            self.node(n, self.survivor)
        for n in (self.b, self.c, self.d):
            self.node(n, self.erased)
        if share_triple:
            self.node(self.c, self.survivor)
        e.create_edge(self.a, PRED, self.b, graph=self.survivor)
        if share_triple:
            e.create_edge(self.b, PRED, self.c, graph=self.survivor)
        e.create_edge(self.b, PRED, self.c, graph=self.erased)
        e.create_edge(self.b, PRED, self.d, graph=self.erased)
        e.conn.commit()
        if build_nkg:
            self.rebuild()
        self.iris.set("planted", "^NKG", *SENTINEL)
        return self

    def rebuild(self):
        self.iris.classMethodValue("Graph.KG.Traversal", "BuildNKG")

    def hll(self, src: str, hops: int = 2) -> int:
        raw = self.iris.classMethodValue("Graph.KG.NKGAccel", "CountDistinctKHop", src, "", hops, "out")
        return int(json.loads(raw)["estimate"])

    def approx(self, src: str) -> int:
        res = self.engine.execute_cypher(
            f"MATCH (s {{node_id: $s}})-[:{PRED}*1..2]->(m) RETURN approx_count_distinct(m) AS n",
            {"s": src},
        )
        assert not getattr(res, "error", None), res.error
        return int(res.rows[0][0])

    def reach(self, src: str) -> set:
        """The ids within two hops of src, as the engine answers (fast path or its fallback)."""
        res = self.engine.execute_cypher(
            f"MATCH (s {{node_id: $s}})-[:{PRED}*1..2]->(m) RETURN DISTINCT m.node_id AS id",
            {"s": src},
        )
        assert not getattr(res, "error", None), res.error
        return {r[0] for r in res.rows}

    def nkg_present(self) -> bool:
        return bool(self.iris.isDefined("^NKG", "$meta", "nodeCount"))


@pytest.fixture
def world(iris_connection, iris_master_cleanup):
    _require_iris(iris_connection)
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    return World(engine, engine._iris_obj(), iris_connection)


def _not_stale_and_not_lossy(w: World, graph: str) -> None:
    """A ^NKG that stands must hold exactly what ^KG holds; an absent one holds nothing."""
    if not w.nkg_present():
        return
    nkg, kg = _nkg_edges(w.iris), _kg_edges(w.iris)
    assert not (nkg - kg), f"^NKG holds edges no graph has: {sorted(nkg - kg)}"
    assert not (kg - nkg), f"^NKG lost edges a surviving graph has: {sorted(kg - nkg)}"


# --- the wrong answers ---------------------------------------------------------------


def test_sketch_of_an_erased_graph_does_not_answer_for_the_survivor(world):
    """The observable: the erased graph's far ends in a shared node's sketch.

    ^NKG may be absent after the erase (every reader falls back) or present; present, it
    must answer as a rebuild does. Before the fix it was present and counted c and d.
    """
    w = world.build()
    # Before the erase the sketch of b holds c and d, the erased graph's.
    stale_before = w.hll(w.a)
    assert stale_before >= 3, "setup: the sketch of the shared node should hold c and d"

    w.engine.erase_graph(w.erased)

    if w.nkg_present():
        kept = w.hll(w.a)
        w.rebuild()
        rebuilt = w.hll(w.a)
        assert kept == rebuilt, f"CountDistinctKHop answered {kept} from a sketch of erased edges (a rebuild says {rebuilt})"
        assert rebuilt < stale_before


def test_approx_count_distinct_after_erase_equals_a_rebuilds(world):
    w = world.build()
    w.engine.erase_graph(w.erased)

    after = w.approx(w.a)
    w.rebuild()
    rebuilt = w.approx(w.a)
    # What a reaches within two hops is b alone.
    assert rebuilt == 1
    assert after == rebuilt, f"approx_count_distinct said {after} after the erase, a rebuild says {rebuilt}"


# --- the state ----------------------------------------------------------------------


def test_row_backed_graph_erase_drops_nkg(world):
    w = world.build()
    assert w.nkg_present() and _interned(w.iris, w.b)
    version_before = int(w.iris.getString("^NKG", "$meta", "version"))

    removed = w.engine.erase_graph(w.erased)

    assert removed == 5  # 3 nodes + 2 edges of the erased graph
    assert not w.iris.isDefined("^NKG", *SENTINEL), "a row-backed graph erase left ^NKG standing"
    assert not w.nkg_present()
    # DropNKG keeps the version rising (4.1.1).
    assert int(w.iris.getString("^NKG", "$meta", "version")) > version_before


def test_survivors_edges_are_neither_lost_nor_joined_by_stale_ones(world):
    """A shared triple (b -> c) stays in ^NKG; the erased graph's own (b -> d) does not."""
    w = world.build(share_triple=True)
    assert (w.b, PRED, w.c) in _nkg_edges(w.iris)

    w.engine.erase_graph(w.erased)

    kg = _kg_edges(w.iris)
    assert kg == {(w.a, PRED, w.b), (w.b, PRED, w.c)}
    _not_stale_and_not_lossy(w, w.survivor)
    # And the engine answers as a rebuild does: b and c, not d.
    after = w.reach(w.a)
    w.rebuild()
    assert w.reach(w.a) == after == {w.b, w.c}


def test_survivor_answers_equal_a_rebuilds_across_the_hll_and_the_reach(world):
    w = world.build(share_triple=True)
    w.engine.erase_graph(w.erased)

    reach_after = w.reach(w.a)
    approx_after = w.approx(w.a)
    # A ^NKG that stands answers the raw sketch as a rebuild does; an absent one is a
    # state the engine falls back from, which the two engine-level answers above cover.
    hll_after = (w.hll(w.a, 2), w.hll(w.a, 3)) if w.nkg_present() else None
    w.rebuild()
    if hll_after is not None:
        assert (w.hll(w.a, 2), w.hll(w.a, 3)) == hll_after
    assert w.reach(w.a) == reach_after
    assert w.approx(w.a) == approx_after


def test_adjacency_only_graph_erase_with_interned_ends_still_drops_nkg(world):
    """No rows at all: ^KG("out", g) is there before the erase and gone after it."""
    w = world
    graph = f"ivgeg-adj-{uuid.uuid4().hex[:6]}"
    x, y = f"{graph}:x", f"{graph}:y"
    w.iris.classMethodValue("Graph.KG.Traversal", "InitNKGSkeleton")
    # ^NKG is live, so WriteAdjacency interns both ends.
    w.iris.classMethodVoid("Graph.KG.EdgeScan", "WriteAdjacency", x, PRED, y, "1.0", graph)
    w.iris.set("planted", "^NKG", *SENTINEL)
    assert _interned(w.iris, x) and _interned(w.iris, y)
    assert w.iris.isDefined("^KG", "out", graph)

    w.engine.erase_graph(graph)

    assert not w.iris.isDefined("^NKG", *SENTINEL)
    assert not w.nkg_present()


def test_graph_none_of_whose_edges_can_be_in_nkg_leaves_it_standing(world):
    """Ends never interned (the graph was written while ^NKG did not exist)."""
    w = world
    graph = f"ivgeg-un-{uuid.uuid4().hex[:6]}"
    x, y, z = f"{graph}:x", f"{graph}:y", f"{graph}:z"
    for n in (x, y, z):
        w.node(n, graph)
    w.engine.create_edge(x, PRED, y, graph=graph)
    w.engine.create_edge(y, PRED, z, graph=graph)
    w.conn.commit()
    # A second graph that ^NKG does hold.
    keep = f"ivgeg-keep-{uuid.uuid4().hex[:6]}"
    w.iris.classMethodValue("Graph.KG.Traversal", "InitNKGSkeleton")
    for n in (f"{keep}:p", f"{keep}:q"):
        w.node(n, keep)
    w.engine.create_edge(f"{keep}:p", PRED, f"{keep}:q", graph=keep)
    w.conn.commit()
    w.iris.set("planted", "^NKG", *SENTINEL)
    assert not any(_interned(w.iris, n) for n in (x, y, z))
    before = _nkg(w.iris)

    removed = w.engine.erase_graph(graph)

    assert removed == 5
    assert w.iris.isDefined("^NKG", *SENTINEL), "an erase of edges ^NKG never held dropped it"
    assert _nkg(w.iris) == before
    assert (f"{keep}:p", PRED, f"{keep}:q") in _nkg_edges(w.iris)
    assert not (_nkg_edges(w.iris) - _kg_edges(w.iris)), "the kept ^NKG holds an edge ^KG no longer has"


def test_adjacency_only_edge_erase_drops_nkg(world):
    """EraseEdges of an edge that has no row: nothing counts as removed, ^NKG still goes."""
    w = world
    graph = f"ivgeg-edge-{uuid.uuid4().hex[:6]}"
    x, y = f"{graph}:x", f"{graph}:y"
    w.iris.classMethodValue("Graph.KG.Traversal", "InitNKGSkeleton")
    w.iris.classMethodVoid("Graph.KG.EdgeScan", "WriteAdjacency", x, PRED, y, "1.0", graph)
    w.iris.set("planted", "^NKG", *SENTINEL)
    assert _interned(w.iris, x) and _interned(w.iris, y)

    removed = int(
        w.iris.classMethodValue("Graph.KG.Eraser", "EraseEdges", graph, json.dumps([{"s": x, "p": PRED, "o": y}]))
    )

    assert removed == 0  # no row
    assert not w.iris.isDefined("^KG", "out", graph, x)
    assert not w.iris.isDefined("^NKG", *SENTINEL), "the sketch and edgeCount of an erased edge survived"


def test_row_backed_edge_erase_drops_nkg(world):
    """EraseEdges counts rows, not an after-the-fact global: pinned so it stays so."""
    w = world.build()

    removed = int(
        w.iris.classMethodValue(
            "Graph.KG.Eraser", "EraseEdges", w.erased, json.dumps([{"s": w.b, "p": PRED, "o": w.c}])
        )
    )

    assert removed == 1
    assert not w.iris.isDefined("^NKG", *SENTINEL)


def test_default_graph_erase_decides_from_its_rows_too(world):
    """The default graph has two spellings in graph_id; the rows walk must see both."""
    w = world
    tag = uuid.uuid4().hex[:6]
    x, y = f"ivgeg-d{tag}:x", f"ivgeg-d{tag}:y"
    w.engine.create_node(x, labels=["IvgEraseGraph"])
    w.engine.create_node(y, labels=["IvgEraseGraph"])
    w.engine.create_edge(x, PRED, y)
    w.conn.commit()
    w.rebuild()
    w.iris.set("planted", "^NKG", *SENTINEL)
    assert _interned(w.iris, x) and _interned(w.iris, y)

    w.engine.erase_graph(None)

    assert not w.iris.isDefined("^NKG", *SENTINEL), "the default graph's erase left ^NKG standing"
