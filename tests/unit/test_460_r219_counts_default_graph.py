"""R219: ``node_count()`` and ``edge_count()`` count one graph, the default graph when ``graph`` is omitted.

Walkthrough 2 (W8): with 10 nodes and 18 edges in the default graph and 4 nodes and 3 edges in graph ``proj``,
``node_count()`` answered 14 and ``edge_count()`` 21, and ``node_count(graph='proj')`` was a TypeError. USER_GUIDE
section 4: "Python methods that write or read take ``graph=``; omitted, they mean the default graph, never every
graph" (CONTEXT.md, Default graph: the graph a read gets when it names none; an all-graphs read is a Cypher query
without ``USE GRAPH``, not a Python method).
"""

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine


def _engine(count=7):
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    conn = MagicMock()
    cur = conn.cursor.return_value
    cur.fetchone.return_value = (count,)
    eng.conn = conn
    eng._schema_prefix = "Graph_KG"
    eng.execute_cypher = MagicMock(side_effect=AssertionError("counts no longer go through an unscoped Cypher read"))
    return eng, cur


@pytest.mark.parametrize("method, table", [("node_count", "nodes"), ("edge_count", "rdf_edges")])
@pytest.mark.parametrize("kwargs, graph_id", [({}, ""), ({"graph": None}, ""), ({"graph": ""}, ""), ({"graph": "proj"}, "proj")])
def test_counts_one_graph(method, table, kwargs, graph_id):
    eng, cur = _engine(7)
    assert getattr(eng, method)(**kwargs) == 7
    sql, params = cur.execute.call_args.args
    assert f"Graph_KG.{table}" in sql
    assert "COALESCE(graph_id, '') = ?" in sql
    assert params == [graph_id]


@pytest.mark.parametrize("method", ["node_count", "edge_count"])
def test_a_graph_name_that_cannot_be_one_raises(method):
    eng, cur = _engine()
    with pytest.raises(ValueError):
        getattr(eng, method)(graph="0")
    cur.execute.assert_not_called()


def test_the_signatures_take_graph():
    import inspect

    for m in (IRISGraphEngine.node_count, IRISGraphEngine.edge_count):
        p = inspect.signature(m).parameters["graph"]
        assert p.default is None
