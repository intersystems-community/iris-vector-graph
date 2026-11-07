"""4.6.0 R136, live: the betweenness work globals are gone after a run, and so are those a killed run left.

Runs in the scratch namespace `IVG_NAMESPACE` names, where it builds one small graph (clearing its own graph's rows
first) and plants a stale work subtree under a process id that is not running. The scores are worked by hand:

    e -> a,  a -> b,  a -> d,  b -> c,  d -> c          (directed)

Pairs with a shortest path through an inner node: (e,b) and (e,d) go through a; (e,c) has two paths, e-a-b-c and
e-a-d-c, so a gets 1 and b and d 1/2 each; (a,c) has two paths, a-b-c and a-d-c, 1/2 each to b and d. So a = 3,
b = 1, d = 1, and c and e lie on no shortest path between two others.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS = os.environ.get("IVG_NAMESPACE", "")
GRAPH = "r136"
EDGES = [("e", "a"), ("a", "b"), ("a", "d"), ("b", "c"), ("d", "c")]
EXPECTED = {"a": 3.0, "b": 1.0, "d": 1.0}
WORK = ("^bcParSrc", "^bcParScore", "^bcBitAdj")
DEAD_PID = 999999937  # no process: IRIS pids are far smaller


@pytest.fixture(scope="module")
def engine():
    if not NS.startswith("IVG460"):
        pytest.skip("set IVG_NAMESPACE to a scratch IVG460* namespace")
    from iris_vector_graph import IRISGraphEngine

    conn = ns262.connect(NS)
    eng = IRISGraphEngine(conn, embedding_dimension=768)
    eng.initialize_schema()
    cur = conn.cursor()
    for table in ("rdf_edges", "rdf_labels", "rdf_props", "nodes"):
        cur.execute(f"DELETE FROM Graph_KG.{table}")  # this scratch namespace holds only this test's graph
    conn.commit()
    for n in "abcde":
        eng.create_node(f"{GRAPH}_{n}")
    for s, o in EDGES:
        eng.create_edge(f"{GRAPH}_{s}", "R", f"{GRAPH}_{o}")
    assert eng.sync() is True
    yield eng, ns262.native(conn)
    # the run's other modules share this namespace: leave none of this graph behind in its default graph
    for n in "abcde":
        eng.delete_node(f"{GRAPH}_{n}")
    conn.commit()
    eng.sync()
    conn.close()


def _scores(eng):
    return {r["id"].removeprefix(f"{GRAPH}_"): r["score"] for r in eng.betweenness_centrality(sample_size=5, top_k=0)}


def _left(nat):
    return {g: int(nat.isDefined(g)) for g in WORK if int(nat.isDefined(g))}


def test_the_hand_worked_scores(engine):
    eng, _nat = engine
    assert _scores(eng) == pytest.approx(EXPECTED)


def test_a_run_leaves_no_work_global(engine):
    eng, nat = engine
    _scores(eng)
    assert _left(nat) == {}


def test_a_killed_run_s_work_is_gone_after_the_next_run(engine):
    eng, nat = engine
    assert not int(nat.classMethodValue("%SYS.ProcessQuery", "%ExistsId", str(DEAD_PID)))
    nat.set("stale", "^bcParSrc", DEAD_PID, 0)
    nat.set(5, "^bcParScore", DEAD_PID, 1, 0)
    nat.set("x", "^bcBitAdj", DEAD_PID, 0)
    assert _scores(eng) == pytest.approx(EXPECTED)
    assert _left(nat) == {}


def test_a_live_process_s_work_is_left_alone(engine):
    """A concurrent run's areas belong to a process that is still running: the cleanup must not take them."""
    eng, nat = engine
    other = ns262.connect(NS)
    try:
        live_pid = int(ns262.native(other).classMethodValue("%SYSTEM.SYS", "ProcessID"))
        nat.set(7, "^bcParScore", live_pid, 1, 0)
        assert _scores(eng) == pytest.approx(EXPECTED)
        assert nat.get("^bcParScore", live_pid, 1, 0) == 7
    finally:
        nat.kill("^bcParScore", live_pid)
        other.close()
    assert _left(nat) == {}
