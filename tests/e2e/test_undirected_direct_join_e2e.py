"""An undirected hop from a bound node, answered by one rdf_edges join, returns what the UNION ALL CTE returned.

The fixture has both directions, a self-loop, a second edge type, an isolated node and an edge that does not touch the
hub. Each answer is checked against the rows the graph holds, not against SQL text.
"""

from __future__ import annotations

import contextlib
import os
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

LABEL = "IvgUndDirect"


@pytest.fixture
def g(iris_connection):
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true" or iris_connection is None:
        pytest.fail("needs a live IRIS: start ivg-iris-enterprise with scripts/enterprise-container.sh up")
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    ids = {k: f"und_{run}_{k}" for k in ("hub", "out", "in", "liked", "lonely")}
    for nid in ids.values():
        engine.create_node(nid, labels=[LABEL])
    engine.create_edge(ids["hub"], "KNOWS", ids["out"])
    engine.create_edge(ids["in"], "KNOWS", ids["hub"])
    engine.create_edge(ids["hub"], "KNOWS", ids["hub"])  # self-loop
    engine.create_edge(ids["hub"], "LIKES", ids["liked"])
    engine.create_edge(ids["out"], "KNOWS", ids["in"])  # does not touch the hub
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield engine, ids
    cur = iris_connection.cursor()
    for nid in ids.values():
        for sql, params in (
            ("DELETE FROM Graph_KG.rdf_edges WHERE s=? OR o_id=?", (nid, nid)),
            ("DELETE FROM Graph_KG.rdf_props WHERE s=?", (nid,)),
            ("DELETE FROM Graph_KG.rdf_labels WHERE s=?", (nid,)),
            ("DELETE FROM Graph_KG.nodes WHERE node_id=?", (nid,)),
        ):
            with contextlib.suppress(Exception):
                cur.execute(sql, params)
    with contextlib.suppress(Exception):
        iris_connection.commit()


def _col(result):
    assert not getattr(result, "error", None), result.error
    rows = result.rows if hasattr(result, "rows") else result["rows"]
    return sorted(r[0] for r in rows)


def test_both_directions_and_the_self_loop_once(g):
    engine, ids = g
    got = _col(engine.execute_cypher(f"MATCH (x:{LABEL})-[:KNOWS]-(y) WHERE x.id = $id RETURN y.id", {"id": ids["hub"]}))
    assert got == sorted([ids["out"], ids["in"], ids["hub"]])


def test_two_edge_types(g):
    engine, ids = g
    got = _col(engine.execute_cypher(f"MATCH (x:{LABEL})-[:KNOWS|LIKES]-(y) WHERE x.id = $id RETURN y.id", {"id": ids["hub"]}))
    assert got == sorted([ids["out"], ids["in"], ids["hub"], ids["liked"]])


def test_any_type(g):
    engine, ids = g
    got = _col(engine.execute_cypher(f"MATCH (x:{LABEL})--(y) WHERE x.id = $id RETURN y.id", {"id": ids["liked"]}))
    assert got == [ids["hub"]]


def test_optional_hop_from_an_isolated_node_keeps_its_row(g):
    engine, ids = g
    r = engine.execute_cypher(f"MATCH (x:{LABEL}) WHERE x.id = $id OPTIONAL MATCH (x)-[:KNOWS]-(y) RETURN x.id, y.id", {"id": ids["lonely"]})
    assert not getattr(r, "error", None), r.error
    assert [list(row) for row in r.rows] == [[ids["lonely"], None]]


def test_hop_to_a_bound_target_either_way_round(g):
    engine, ids = g
    for a, b in ((ids["hub"], ids["in"]), (ids["in"], ids["hub"])):
        r = engine.execute_cypher(
            f"MATCH (x:{LABEL}), (y:{LABEL}) WHERE x.id = $a AND y.id = $b MATCH (x)-[:KNOWS]-(y) RETURN count(*)", {"a": a, "b": b}
        )
        assert _col(r) == [1], (a, b)


def test_two_undirected_hops_do_not_reuse_an_edge(g):
    engine, ids = g
    got = _col(engine.execute_cypher(
        f"MATCH (x:{LABEL})-[:KNOWS]-(m)-[:KNOWS]-(z) WHERE x.id = $id RETURN z.id", {"id": ids["out"]}))
    # out-hub-in, out-hub-hub (self-loop), out-in-hub; never back over the edge just used
    assert got == sorted([ids["in"], ids["hub"], ids["hub"]])
