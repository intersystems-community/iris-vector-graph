"""4.6.0 R050 W3: `bulk_create_nodes(nodes, graph=...)` writes to that graph, as `bulk_create_edges(graph=...)` does.

The User Guide says Python write methods take `graph=`; `bulk_create_nodes` raised TypeError on it. A node's own
`graph` key wins over the keyword (as an edge's does in `bulk_create_edges`), and a batch with a graph goes the SQL
way, which writes each node's graph (the server loader writes the default graph only).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine


def _engine(deployed: bool):
    conn = MagicMock()
    conn.cursor.return_value.fetchall.return_value = []
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    engine.capabilities.objectscript_deployed = deployed
    engine._iris_obj = MagicMock(return_value=MagicMock())
    return engine, conn.cursor.return_value


def _node_graphs(cursor) -> dict:
    """{node id: graph} from the nodes batch: each row is [node_id, graph_id, node_id, graph_id]."""
    (rows,) = [c.args[1] for c in cursor.executemany.call_args_list if "nodes" in c.args[0] and "rdf_" not in c.args[0]]
    return {r[0]: r[1] for r in rows}


@pytest.mark.parametrize("deployed", [False, True], ids=["sql", "classes-deployed"])
def test_the_keyword_names_the_graph(deployed):
    engine, cursor = _engine(deployed)
    with patch("iris_vector_graph.schema._call_classmethod_large", return_value=2) as server:
        assert engine.bulk_create_nodes([{"id": "a"}, {"id": "b", "labels": ["L"]}], graph="num") == ["a", "b"]
    server.assert_not_called()
    assert _node_graphs(cursor) == {"a": "num", "b": "num"}
    (labels,) = [c.args[1] for c in cursor.executemany.call_args_list if "rdf_labels" in c.args[0]]
    assert labels == [("num", "b", "L", "num", "b", "L")]


def test_a_nodes_own_graph_wins():
    engine, cursor = _engine(False)
    engine.bulk_create_nodes([{"id": "a"}, {"id": "b", "graph": "other"}, {"id": "c", "graph": None}], graph="num")
    # None names the default graph, as `graph=None` does
    assert _node_graphs(cursor) == {"a": "num", "b": "other", "c": ""}


def test_no_keyword_is_the_default_graph():
    engine, cursor = _engine(False)
    engine.bulk_create_nodes([{"id": "a"}])
    assert _node_graphs(cursor) == {"a": ""}


def test_the_callers_dicts_are_not_changed():
    engine, _ = _engine(False)
    nodes = [{"id": "a"}]
    engine.bulk_create_nodes(nodes, graph="num")
    assert "graph" not in nodes[0]
