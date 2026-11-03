"""4.6.0 R058 (KNOWN_ISSUES #80): an unaliased RETURN from a `node_id`-pinned variable-length read.

On `v0->v1->v2` plus `v0->v2`, `MATCH (s {node_id: $x})-[*1..2]->(m) RETURN m.id` answered the BFS route's own
columns `id, hops, pred`, one row per node per depth; with the start as a literal it answered `m.id` but one row per
node (`v1, v2`). openCypher answers the column the expression names, `m.id`, and one row per matched path: v0->v1,
v0->v2 and v0->v1->v2, so `v1, v2, v2`. `RETURN m.node_id AS i` (aliased) expanded in SQL and was right; the
unaliased forms must agree with it.

The same routes answered a RETURN that reads the start (`s`) from the far end's walk: `count(DISTINCT s)` counted the
ends, `RETURN DISTINCT s.node_id` answered the ends' ids under the column `s`. The start is one node, `v0`.

Expected answers are worked out from the graph drawn here, not taken from IVG.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

PRED = "IVG460_VL"


@pytest.fixture(scope="module")
def g(iris_connection):
    if iris_connection is None:
        pytest.fail("no live IRIS connection: the defect is in the server-side routes")
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    ids = {k: f"ivg460vl_{run}_{k}" for k in ("v0", "v1", "v2")}
    for nid in ids.values():
        engine.create_node(nid, labels=["Ivg460VL"])  # writes the `id` property beside node_id
    for s, o in (("v0", "v1"), ("v1", "v2"), ("v0", "v2")):
        engine.create_edge(ids[s], PRED, ids[o])
    with contextlib.suppress(Exception):
        iris_connection.commit()

    class G:
        pass

    graph = G()
    graph.engine, graph.ids, graph.prefix = engine, ids, f"ivg460vl_{run}_"
    yield graph
    for nid in ids.values():
        with contextlib.suppress(Exception):
            engine.delete_node(nid, graph=None)


BOTH = [pytest.param(True, id="literal"), pytest.param(False, id="param")]


def _ask(g, ret: str, literal: bool):
    if literal:
        q = f"MATCH (s {{node_id: '{g.ids['v0']}'}})-[*1..2]->(m) RETURN {ret}"
        r = g.engine.execute_cypher(q)
    else:
        q = f"MATCH (s {{node_id: $x}})-[*1..2]->(m) RETURN {ret}"
        r = g.engine.execute_cypher(q, {"x": g.ids["v0"]})
    assert not getattr(r, "error", None), (q, r.error)
    return q, r


def _vals(g, r) -> list:
    return sorted([str(v).replace(g.prefix, "") for v in row] for row in r.rows)


@pytest.mark.parametrize("literal", BOTH)
class TestTheColumnAndOneRowPerPath:
    @pytest.mark.parametrize("prop", ["id", "node_id"])
    def test_unaliased_property(self, g, literal, prop):
        q, r = _ask(g, f"m.{prop}", literal)
        assert list(r.columns) == [f"m.{prop}"], (q, r.columns)
        assert _vals(g, r) == [["v1"], ["v2"], ["v2"]], (q, r.rows)

    @pytest.mark.parametrize("prop", ["id", "node_id"])
    def test_distinct_unaliased_property(self, g, literal, prop):
        q, r = _ask(g, f"DISTINCT m.{prop}", literal)
        assert list(r.columns) == [f"m.{prop}"], (q, r.columns)
        assert _vals(g, r) == [["v1"], ["v2"]], (q, r.rows)

    def test_aliased_is_unchanged(self, g, literal):
        q, r = _ask(g, "m.node_id AS i", literal)
        assert list(r.columns) == ["i"], (q, r.columns)
        assert _vals(g, r) == [["v1"], ["v2"], ["v2"]], (q, r.rows)

    def test_the_node_once_per_path(self, g, literal):
        q, r = _ask(g, "m", literal)
        assert len(r.rows) == 3, (q, r.columns, r.rows)


@pytest.mark.parametrize("literal", BOTH)
class TestTheStartIsOneNode:
    def test_count_distinct_start(self, g, literal):
        q, r = _ask(g, "count(DISTINCT s)", literal)
        assert r.rows == [[1]], (q, r.rows)

    def test_distinct_start_id(self, g, literal):
        q, r = _ask(g, "DISTINCT s.node_id", literal)
        assert list(r.columns) == ["s.node_id"], (q, r.columns)
        assert _vals(g, r) == [["v0"]], (q, r.rows)

    def test_both_ends(self, g, literal):
        q, r = _ask(g, "DISTINCT s.node_id, m.node_id", literal)
        assert list(r.columns) == ["s.node_id", "m.node_id"], (q, r.columns)
        assert _vals(g, r) == [["v0", "v1"], ["v0", "v2"]], (q, r.rows)
