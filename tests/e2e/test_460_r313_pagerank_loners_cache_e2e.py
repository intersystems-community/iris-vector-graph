"""4.6.0 R313, live: PageRank's cached answer follows the node set, now that the loners are read on a miss only.

A node written with no edge moves no adjacency token, so the cache entry is keyed by the node table's row count and
largest row id: every way of adding a node, and every way of removing one (a raw SQL DELETE too), must give a new
answer that scores it (or not), never the stored one.
The R306 algorithm oracle (`test_460_r306_algorithm_oracle_e2e.py -k pagerank`) checks the scores against networkx.

The namespace is `IVG_NAMESPACE` (an IVG460* scratch namespace with IVG set up); its default graph is erased first.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]


@pytest.fixture
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    eng = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema)
    eng.erase_graph(None)
    eng.create_edge("pr:a", "PR", "pr:b")
    eng.create_edge("pr:b", "PR", "pr:a")
    yield eng
    try:
        eng.erase_graph(None)
    finally:
        conn.close()


def ids(eng):
    return sorted(i for i, _ in eng.kg_PAGERANK())


def test_a_node_added_with_no_edge_is_scored_and_a_removed_one_is_not(engine):
    assert ids(engine) == ["pr:a", "pr:b"]
    assert ids(engine) == ["pr:a", "pr:b"]  # the second call is the cache's
    engine.create_node("pr:lone")
    got = dict(engine.kg_PAGERANK())
    assert sorted(got) == ["pr:a", "pr:b", "pr:lone"]
    assert abs(sum(got.values()) - 1.0) < 1e-6
    engine.delete_node("pr:lone")
    assert ids(engine) == ["pr:a", "pr:b"]


def test_a_node_losing_its_last_edge_is_scored_as_a_loner(engine):
    engine.create_edge("pr:c", "PR", "pr:a")
    assert ids(engine) == ["pr:a", "pr:b", "pr:c"]
    engine.execute_cypher("MATCH (c {id: 'pr:c'})-[r:PR]->() DELETE r", raise_on_error=True)
    assert ids(engine) == ["pr:a", "pr:b", "pr:c"]


def _ledger_insert(eng, node_id):
    from iris_vector_graph.ledger import Changeset

    eng.ledger.enable()
    try:
        cs = Changeset(actor="r313", actor_type="system")
        cs.create_node(node_id)
        eng.ledger.commit(cs)
    finally:
        eng.ledger.disable()


def _sql_insert(eng, node_id):
    cur = eng.conn.cursor()
    cur.execute(f"INSERT INTO {eng._t('nodes')} (node_id, graph_id) VALUES (?, '')", [node_id])
    eng.conn.commit()


INSERTS = {
    "create_node": lambda eng, i: eng.create_node(i),
    "bulk_create_nodes": lambda eng, i: eng.bulk_create_nodes([{"id": i}]),
    "cypher CREATE": lambda eng, i: eng.execute_cypher("CREATE (:R313 {id: $i})", {"i": i}, raise_on_error=True),
    "ledger commit": _ledger_insert,
    "SQL INSERT": _sql_insert,
}


@pytest.mark.parametrize("how", sorted(INSERTS))
def test_every_way_of_adding_a_node_is_seen_by_a_cached_pagerank(engine, how):
    assert ids(engine) == ["pr:a", "pr:b"]
    assert ids(engine) == ["pr:a", "pr:b"]  # cached
    INSERTS[how](engine, "pr:new")
    assert ids(engine) == ["pr:a", "pr:b", "pr:new"]


DELETES = {
    "delete_node": lambda eng, i: eng.delete_node(i),
    "delete_nodes_by_prefix": lambda eng, i: eng.delete_nodes_by_prefix(i),
    "cypher DETACH DELETE": lambda eng, i: eng.execute_cypher("MATCH (n {id: $i}) DETACH DELETE n", {"i": i},
                                                              raise_on_error=True),
}


@pytest.mark.parametrize("how", sorted(DELETES))
def test_every_ivg_way_of_removing_a_node_is_seen_by_a_cached_pagerank(engine, how):
    engine.create_node("pr:gone")
    assert ids(engine) == ["pr:a", "pr:b", "pr:gone"]
    assert ids(engine) == ["pr:a", "pr:b", "pr:gone"]  # cached
    DELETES[how](engine, "pr:gone")
    assert ids(engine) == ["pr:a", "pr:b"]


def test_a_raw_sql_delete_of_a_bare_node_is_seen_by_a_cached_pagerank(engine):
    engine.create_node("pr:gone")
    assert ids(engine) == ["pr:a", "pr:b", "pr:gone"]
    cur = engine.conn.cursor()
    cur.execute(f"DELETE FROM {engine._t('nodes')} WHERE node_id = 'pr:gone'")
    engine.conn.commit()
    assert ids(engine) == ["pr:a", "pr:b"]
