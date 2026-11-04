"""4.6.0 R101: `kg_WCC(max_iterations=n)` is a safety limit on the default store path too.

The store answered WCC with `Graph.KG.Algorithms.WCCJson()` and no limit, so `max_iterations` reached the Owner only
when the store did not answer; the documented `WCC did not converge in n rounds` never came.

Worked by hand on the chain w1 -> w2 -> w3 -> w4 -> w5 -> w6 (direction ignored). Every round, all at once, a node takes
the smallest label among itself and its neighbours. After round r node wi holds w(max(1, i - r)), so round 5 is the
last to change a label (w6 takes w1) and round 6 changes none and stops. `WCCJson` checks the limit before each round:
with 5 it refuses to start round 6, so it raises; with 6 it answers every node `w1`.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.e2e]

CHAIN = [f"w{i}" for i in range(1, 7)]


@pytest.fixture
def chain(iris_connection, iris_master_cleanup):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    for nid in CHAIN:
        engine.create_node(nid)
    for a, b in zip(CHAIN, CHAIN[1:]):
        engine.create_edge(a, "NEXT", b)
    return engine


def test_a_limit_below_the_rounds_needed_raises(chain):
    with pytest.raises(Exception, match="WCC did not converge in 5 rounds"):
        chain.kg_WCC(max_iterations=5)


def test_a_limit_that_covers_the_rounds_answers_the_component(chain):
    assert chain.kg_WCC(max_iterations=6) == {nid: "w1" for nid in CHAIN}


def test_no_limit_answers_the_component(chain):
    assert chain.kg_WCC() == {nid: "w1" for nid in CHAIN}
