"""4.6.0 R313, live: PageRank's cached answer follows the node set, now that the loners are read on a miss only.

A node written with no edge moves no adjacency token, so the cache entry is keyed by the node table's row count and
largest row id: adding one, or removing one, must give a new answer that scores it (or not), never the stored one.
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
