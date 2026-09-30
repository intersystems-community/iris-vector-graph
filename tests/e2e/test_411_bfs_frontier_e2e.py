"""4.1.1 — the ObjectScript BFS answers each reachable node once per path length.

Found by the upgrade stage of scripts/quickstart_e2e.py (2026-09-30). On a stock
container, where Arno is absent, `execute_bfs` goes to `BFSFastJsonSorted`. Its
frontier was never cleared, so on the chain n0->n1->n2->n3 a 3-hop walk returned
n1 at hops 1, 2 and 3. See tests/unit/test_411_bfs_frontier.py.

The Arno path is switched off here so the ObjectScript path is the one measured.
"""

from __future__ import annotations

import os
import time

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def engine(iris_connection):
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true" or iris_connection is None:
        pytest.fail(
            "the BFS runs inside IRIS; start ivg-iris-enterprise with "
            "scripts/enterprise-container.sh up."
        )
    from iris_vector_graph.engine import IRISGraphEngine

    eng = IRISGraphEngine(iris_connection, embedding_dimension=768)
    eng.initialize_schema()
    return eng


@pytest.fixture
def chain(iris_connection, engine):
    p = f"ivg411:bfs:{int(time.time() * 1000)}:"
    ids = [p + f"n{i}" for i in range(4)]
    for node_id in ids:
        engine.create_node(node_id, labels=["N"])
    for a, b in zip(ids, ids[1:]):
        engine.create_edge(a, "NEXT", b)
    yield ids
    for node_id in ids:
        engine.delete_node(node_id)


def _objectscript_only(engine):
    from iris_vector_graph.stores.iris_sql_store import _ObjectScriptBfsAdapter

    store = engine._store
    engine._arno_available = store._arno_available = False
    strategy = store._select_bfs_strategy(None, direction="out")
    assert isinstance(strategy, _ObjectScriptBfsAdapter), type(strategy).__name__
    return store


class TestObjectScriptBfs:
    def test_a_chain_is_walked_once(self, engine, chain):
        store = _objectscript_only(engine)
        try:
            rows = store.execute_bfs(chain[0], ["NEXT"], 3, "out", 0).rows
        finally:
            engine._arno_available = store._arno_available = None
        assert [(r[0], r[1]) for r in rows] == [(chain[1], 1), (chain[2], 2), (chain[3], 3)]

    def test_the_cypher_read_the_upgrade_stage_makes(self, engine, chain):
        store = _objectscript_only(engine)
        try:
            rows = engine.execute_cypher(
                "MATCH (a {node_id:$id})-[:NEXT*1..3]->(b) RETURN b.node_id AS id ORDER BY id",
                {"id": chain[0]},
            )["rows"]
        finally:
            engine._arno_available = store._arno_available = None
        assert [r[0] for r in rows] == chain[1:], rows
