import os
from unittest.mock import patch, MagicMock
import pytest

SKIP = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

HUB = "sbfs_hub"
SPOKES = 120
PRED = "SBFS_R"


@pytest.fixture(scope="module")
def engine(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine
    return IRISGraphEngine(iris_connection)


@pytest.fixture(scope="module")
def hub_graph(engine):
    spoke_ids = [f"sbfs_spoke_{i}" for i in range(SPOKES)]
    hop2_ids = [f"sbfs_hop2_{i}" for i in range(SPOKES)]
    all_nodes = [HUB] + spoke_ids + hop2_ids

    for nid in all_nodes:
        engine.create_node(nid)

    for spoke in spoke_ids:
        engine.create_edge(HUB, PRED, spoke)
    for i, spoke in enumerate(spoke_ids):
        engine.create_edge(spoke, PRED, hop2_ids[i])

    engine.rebuild_kg()

    yield {"hub": HUB, "spokes": SPOKES, "expected_2hop": SPOKES * 2}

    engine.bulk_delete_nodes(all_nodes)


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
@pytest.mark.e2e
def test_unbounded_bfs_large_result_set_completes(engine, hub_graph):
    query = f"MATCH (s {{node_id:$id}})-[:{PRED}*1..2]->(n) RETURN n.node_id AS id"
    result = engine.execute_cypher(query, {"id": HUB})
    assert "error" not in result or not result.get("error"), f"Query error: {result.get('error')}"
    rows = result.get("rows", [])
    assert len(rows) == hub_graph["expected_2hop"], (
        f"Expected {hub_graph['expected_2hop']} results, got {len(rows)}"
    )


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
@pytest.mark.e2e
def test_unbounded_bfs_empty_result_completes(engine):
    nid = "sbfs_isolated"
    engine.create_node(nid)
    result = engine.execute_cypher(
        f"MATCH (s {{node_id:$id}})-[:{PRED}*1..2]->(n) RETURN n.node_id",
        {"id": nid}
    )
    assert "error" not in result or not result.get("error")
    assert len(result.get("rows", [])) == 0
    engine.delete_node(nid)
