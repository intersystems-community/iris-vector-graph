"""4.6.0 R057 (KNOWN_ISSUES triage N1): a variable-length path does not walk a relationship twice.

openCypher holds every path to relationship uniqueness: within one path a relationship is traversed once. The BFS
routes that answer a variable-length read from a start pinned by `node_id` walked the arriving edge back, so an
undirected walk from `c0` along the chain `c0-NEXT->c1-NEXT->c2-NEXT->c3` came back to `c0` at hop 2:

    MATCH (a {node_id: 'c0'})-[:NEXT*1..3]-(b) RETURN count(DISTINCT b)     -- 4, not 3
    MATCH (a {node_id: 'c0'})-[:NEXT*1..3]-(b) RETURN DISTINCT b.node_id    -- c1, c0, c2, c3
    MATCH (a {node_id: 'c0'})-[:NEXT*2..2]-(b) RETURN b.node_id             -- c0, c2

A node reached again over a *different* relationship is a real path and still counts: the triangle
`t0->t1->t2->t0` reaches `t0` at three hops, and the pair `p0->p1`, `p1->p0` reaches `p0` at two (two relationships).

Every expected answer below is worked out by hand from the graph drawn in each fixture, never from IVG's output. Each
query runs with the start as a literal and as a parameter: they took different routes (the literal the labeled route,
the parameter the id route), and both were wrong.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

PRED = "IVG460_NEXT"


class _Graph:
    def __init__(self, engine):
        self.engine = engine
        self.run = uuid.uuid4().hex[:8]
        self.ids: list = []

    def n(self, name: str) -> str:
        return f"ivg460ru_{self.run}_{name}"

    def build(self, edges):
        for s, o in edges:
            for x in (s, o):
                nid = self.n(x)
                if nid not in self.ids:
                    self.ids.append(nid)
                    self.engine.create_node(nid, labels=["Ivg460RU"])
        for s, o in edges:
            self.engine.create_edge(self.n(s), PRED, self.n(o))

    def wipe(self):
        for nid in self.ids:
            with contextlib.suppress(Exception):
                self.engine.delete_node(nid, graph=None)


@pytest.fixture(scope="module")
def g(iris_connection):
    if iris_connection is None:
        pytest.fail("no live IRIS connection: the defect is in the server-side routes")
    from iris_vector_graph import IRISGraphEngine

    graph = _Graph(IRISGraphEngine(iris_connection))
    graph.build(
        [
            ("c0", "c1"), ("c1", "c2"), ("c2", "c3"),  # chain, no cycle
            ("t0", "t1"), ("t1", "t2"), ("t2", "t0"),  # triangle
            ("p0", "p1"), ("p1", "p0"),  # two relationships between one pair
        ]
    )
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield graph
    graph.wipe()


def _ask(g, start: str, pattern: str, ret: str, literal: bool):
    """`MATCH (a {node_id: <start>})<pattern>(b) RETURN <ret>`, the start as a literal or as `$x`."""
    if literal:
        q = f"MATCH (a {{node_id: '{g.n(start)}'}}){pattern}(b) RETURN {ret}"
        result = g.engine.execute_cypher(q)
    else:
        q = f"MATCH (a {{node_id: $x}}){pattern}(b) RETURN {ret}"
        result = g.engine.execute_cypher(q, {"x": g.n(start)})
    assert not getattr(result, "error", None), (q, result.error)
    return q, result


def _names(g, result) -> list:
    """The first column's values, the run prefix stripped, sorted (row order is not part of the answer)."""
    prefix = g.n("")
    return sorted(str(r[0]).replace(prefix, "") for r in result.rows)


BOTH = [pytest.param(True, id="literal"), pytest.param(False, id="param")]


def _und(rng: str) -> str:
    return f"-[:{PRED}*{rng}]-"


@pytest.mark.parametrize("literal", BOTH)
class TestTheChainDoesNotComeBack:
    """c0-c1-c2-c3: undirected from c0, the ends of 1..3 hops are c1, c2, c3, each over one path."""

    def test_count_distinct(self, g, literal):
        q, r = _ask(g, "c0", _und("1..3"), "count(DISTINCT b)", literal)
        assert r.rows == [[3]], (q, r.rows)

    def test_distinct_ends(self, g, literal):
        q, r = _ask(g, "c0", _und("1..3"), "DISTINCT b.node_id", literal)
        assert _names(g, r) == ["c1", "c2", "c3"], (q, r.rows)

    def test_exactly_two_hops(self, g, literal):
        q, r = _ask(g, "c0", _und("2..2"), "b.node_id", literal)
        assert _names(g, r) == ["c2"], (q, r.rows)

    def test_exactly_two_hops_count(self, g, literal):
        q, r = _ask(g, "c0", _und("2..2"), "count(DISTINCT b)", literal)
        assert r.rows == [[1]], (q, r.rows)

    def test_one_row_per_path(self, g, literal):
        q, r = _ask(g, "c0", _und("1..3"), "b.node_id", literal)
        assert _names(g, r) == ["c1", "c2", "c3"], (q, r.rows)

    def test_from_the_middle(self, g, literal):
        # c1: c0 and c2 at one hop, c3 at two; c1 itself has no cycle
        q, r = _ask(g, "c1", _und("1..3"), "DISTINCT b.node_id", literal)
        assert _names(g, r) == ["c0", "c2", "c3"], (q, r.rows)

    def test_directed_is_unchanged(self, g, literal):
        q, r = _ask(g, "c0", f"-[:{PRED}*1..3]->", "DISTINCT b.node_id", literal)
        assert _names(g, r) == ["c1", "c2", "c3"], (q, r.rows)


@pytest.mark.parametrize("literal", BOTH)
class TestARealCycleStillCounts:
    """A node reached again over another relationship is a path's end."""

    def test_the_triangle_closes_at_three(self, g, literal):
        # t0-t1-t2-t0 uses three relationships: t0 is an end at 3 hops
        q, r = _ask(g, "t0", _und("1..3"), "DISTINCT b.node_id", literal)
        assert _names(g, r) == ["t0", "t1", "t2"], (q, r.rows)
        q, r = _ask(g, "t0", _und("1..3"), "count(DISTINCT b)", literal)
        assert r.rows == [[3]], (q, r.rows)

    def test_the_triangle_is_open_within_two(self, g, literal):
        q, r = _ask(g, "t0", _und("1..2"), "count(DISTINCT b)", literal)
        assert r.rows == [[2]], (q, r.rows)

    def test_the_triangle_at_exactly_two(self, g, literal):
        # t0-t1-t2 and t0-t2-t1; back to t0 in two would reuse a relationship
        q, r = _ask(g, "t0", _und("2..2"), "DISTINCT b.node_id", literal)
        assert _names(g, r) == ["t1", "t2"], (q, r.rows)

    def test_two_relationships_between_a_pair(self, g, literal):
        # p0-[p0->p1]-p1-[p1->p0]-p0: two relationships, so p0 is an end at two hops; p1 is not (no third edge)
        q, r = _ask(g, "p0", _und("2..2"), "DISTINCT b.node_id", literal)
        assert _names(g, r) == ["p0"], (q, r.rows)
        q, r = _ask(g, "p0", _und("1..2"), "count(DISTINCT b)", literal)
        assert r.rows == [[2]], (q, r.rows)
