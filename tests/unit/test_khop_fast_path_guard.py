"""The k-hop shortcuts (`_try_khop_fast_path`) only answer a query that starts at one node named by its node_id.

Before choosing, the shortcut lists the graphs holding adjacency (`_edge_graphs`, two Native API round trips). That
listing ran for every Cypher query, about 0.2 ms each, which was 40% of a small native read; a query no shortcut can
answer must not pay it.
"""

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine


def _engine():
    e = IRISGraphEngine(MagicMock(), native_cypher=False)
    e._edge_graphs = MagicMock(return_value=[""])
    return e


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (t:Tag)<-[:HAS_TAG]-(m:Post) WHERE t.name = $n RETURN count(m)",
        "MATCH (p:Person) RETURN count(p)",
        "MATCH (a {sid: $x})-[:R]->(b) RETURN count(b) AS n",
    ],
)
def test_a_query_no_shortcut_answers_lists_no_graphs(cypher):
    e = _engine()
    assert e._try_khop_fast_path(cypher, {"n": "x", "x": "y"}) is None
    e._edge_graphs.assert_not_called()


def test_a_shortcut_query_still_checks_the_graphs():
    e = _engine()
    e._edge_graphs.return_value = ["g1"]  # a named graph holds edges: the shortcut declines
    assert e._try_khop_fast_path("MATCH (a {node_id: $x})-[:R]->(b) RETURN count(b) AS n", {"x": "a"}) is None
    e._edge_graphs.assert_called_once()
