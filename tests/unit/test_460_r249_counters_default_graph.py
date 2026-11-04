"""4.6.0 R249: the remaining unscoped counters count one graph, the default graph when ``graph`` is omitted (R219's
rule, USER_GUIDE section 4: "Python methods that write or read take ``graph=``; omitted, they mean the default graph,
never every graph").

R219 scoped ``node_count`` and ``edge_count`` and left ``count_nodes``, ``get_node_count``, ``get_edge_count``,
``get_label_distribution`` and ``IVGClient.node_count`` counting every graph (fixes26). Each now takes ``graph=None``
(None and ``""`` the default graph); a name that cannot be a graph raises ``ValueError`` before any SQL. A row a 4.x
upgrade left with a NULL ``graph_id`` is the default graph's (``COALESCE``), as R219 counts it. ``IVGClient.node_count``
runs ``USE GRAPH '<graph>'`` before its count (``USE GRAPH ''`` is the default graph's). The live half, with the
bucket-groups window (R217's rule for ``get_bucket_groups``), is tests/e2e/test_460_r249_counters_default_graph_e2e.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SCOPE = "COALESCE(graph_id, '') = ?"


def _engine(fetchone=(7,), fetchall=None):
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    conn = MagicMock()
    cur = conn.cursor.return_value
    cur.fetchone.return_value = fetchone
    cur.fetchall.return_value = fetchall or []
    eng.conn = conn
    eng._schema_prefix = "Graph_KG"
    eng.execute_cypher = MagicMock(side_effect=AssertionError("a count is not an unscoped Cypher read"))
    return eng, cur


GRAPHS = [({}, ""), ({"graph": None}, ""), ({"graph": ""}, ""), ({"graph": "proj"}, "proj")]


@pytest.mark.parametrize("method", ["count_nodes", "get_node_count"])
@pytest.mark.parametrize("kwargs, gid", GRAPHS)
def test_node_counts_count_one_graph(method, kwargs, gid):
    eng, cur = _engine((7,))
    assert getattr(eng, method)(**kwargs) == 7
    sql, params = cur.execute.call_args.args
    assert "Graph_KG.nodes" in sql and SCOPE in sql
    assert params == [gid]


@pytest.mark.parametrize("method", ["count_nodes", "get_node_count"])
@pytest.mark.parametrize("kwargs, gid", GRAPHS)
def test_a_label_count_counts_one_graph(method, kwargs, gid):
    eng, cur = _engine((3,))
    assert getattr(eng, method)(label="A", **kwargs) == 3
    sql, params = cur.execute.call_args.args
    assert "Graph_KG.rdf_labels" in sql and "label = ?" in sql and SCOPE in sql
    assert params == ["A", gid]


@pytest.mark.parametrize("kwargs, gid", GRAPHS)
def test_edge_count_counts_one_graph(kwargs, gid):
    eng, cur = _engine((5,))
    assert eng.get_edge_count(**kwargs) == 5
    sql, params = cur.execute.call_args.args
    assert "Graph_KG.rdf_edges" in sql and SCOPE in sql
    assert params == [gid]
    assert eng.get_edge_count(predicate="KNOWS", **kwargs) == 5
    sql, params = cur.execute.call_args.args
    assert "p = ?" in sql and SCOPE in sql
    assert params == ["KNOWS", gid]


@pytest.mark.parametrize("kwargs, gid", GRAPHS)
def test_label_distribution_counts_one_graph(kwargs, gid):
    eng, cur = _engine(fetchall=[("A", 3), ("B", 1)])
    assert eng.get_label_distribution(**kwargs) == {"A": 3, "B": 1}
    sql, params = cur.execute.call_args.args
    assert "Graph_KG.rdf_labels" in sql and SCOPE in sql and "GROUP BY label" in sql
    assert params == [gid]


@pytest.mark.parametrize(
    "call",
    [
        lambda e: e.count_nodes(graph="0"),
        lambda e: e.get_node_count(graph="0"),
        lambda e: e.get_edge_count(graph="0"),
        lambda e: e.get_label_distribution(graph="0"),
    ],
)
def test_a_name_that_cannot_be_a_graph_raises_before_sql(call):
    eng, cur = _engine()
    with pytest.raises(ValueError):
        call(eng)
    cur.execute.assert_not_called()


def test_the_signatures_take_graph():
    import inspect

    from iris_vector_graph.sdk import IVGClient

    for m in (
        IRISGraphEngine.count_nodes,
        IRISGraphEngine.get_node_count,
        IRISGraphEngine.get_edge_count,
        IRISGraphEngine.get_label_distribution,
        IVGClient.node_count,
    ):
        assert inspect.signature(m).parameters["graph"].default is None, m


@pytest.mark.parametrize(
    "kwargs, use",
    [
        ({}, "USE GRAPH ''"),
        ({"graph": ""}, "USE GRAPH ''"),
        ({"graph": "proj"}, "USE GRAPH 'proj'"),
        ({"graph": "it's"}, "USE GRAPH 'it\\'s'"),  # the Cypher string escape
    ],
)
def test_client_node_count_counts_one_graph(kwargs, use):
    from iris_vector_graph.result import IVGResult
    from iris_vector_graph.sdk import IVGClient

    client = IVGClient.__new__(IVGClient)
    client.execute_cypher = MagicMock(return_value=IVGResult(columns=["c"], rows=[[4]]))
    assert client.node_count(**kwargs) == 4
    query = client.execute_cypher.call_args.args[0]
    assert query.startswith(use + " ") and "MATCH (n) RETURN count(n)" in query


def test_client_node_count_refuses_a_name_that_cannot_be_a_graph():
    from iris_vector_graph.sdk import IVGClient

    client = IVGClient.__new__(IVGClient)
    client.execute_cypher = MagicMock()
    with pytest.raises(ValueError):
        client.node_count(graph="0")
    client.execute_cypher.assert_not_called()
