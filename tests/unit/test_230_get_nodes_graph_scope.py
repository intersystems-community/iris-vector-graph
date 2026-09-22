"""`get_node`/`get_nodes` read one graph, not every graph that shares the node ID.

Spec 227 replaced `UNIQUE (node_id)` with `UNIQUE (graph_id, node_id)`, so the same node
ID can now legitimately exist in two graphs with different labels, properties and
embeddings. `get_nodes` selected from `rdf_labels` and `rdf_props` on `s IN (...)` with no
graph predicate at all, so it merged both graphs' rows into one dict: graph A's caller saw
graph B's labels and, where the keys collided, graph B's property values won by row order.

The same omission made the existence check (`SELECT node_id FROM nodes WHERE node_id IN
(...)`) report a node as present when it only exists in another graph.

A caller that names no graph gets the default graph (`DEFAULT_GRAPH`), exactly as spec 214
and 223 established — never a cross-graph scan.
"""

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.constants import DEFAULT_GRAPH
from iris_vector_graph.engine import IRISGraphEngine


@pytest.fixture
def mock_conn():
    conn = MagicMock()
    conn.cursor.return_value = MagicMock()
    return conn


@pytest.fixture
def engine(mock_conn):
    return IRISGraphEngine(mock_conn)


def _statements(cursor):
    """(sql, params) for every execute recorded since the last reset.

    Constructing the engine probes `Graph_KG.ivf_indexes` on the same mock, so each test
    resets the cursor before the call it is measuring.
    """
    return [
        (c.args[0], c.args[1] if len(c.args) > 1 else [])
        for c in cursor.execute.call_args_list
    ]


def test_labels_and_props_are_read_from_one_graph(engine, mock_conn):
    cursor = mock_conn.cursor.return_value
    cursor.execute.reset_mock()
    cursor.fetchall.side_effect = [[("n1", "A")], [("n1", "name", "in-a")], []]

    engine.get_nodes(["n1"], graph="ga")

    for sql, params in _statements(cursor):
        assert "graph_id = ?" in sql, sql
        assert "ga" in params, (sql, params)


def test_omitting_the_graph_means_the_default_graph(engine, mock_conn):
    cursor = mock_conn.cursor.return_value
    cursor.execute.reset_mock()
    cursor.fetchall.side_effect = [[("n1", "A")], [], []]

    engine.get_nodes(["n1"])

    for sql, params in _statements(cursor):
        assert "graph_id = ?" in sql, sql
        assert DEFAULT_GRAPH in params, (sql, params)


def test_get_node_passes_the_graph_through(engine, mock_conn):
    cursor = mock_conn.cursor.return_value
    cursor.execute.reset_mock()
    cursor.fetchall.side_effect = [[("n1", "A")], [], []]

    engine.get_node("n1", graph="gb")

    assert all("gb" in params for _, params in _statements(cursor))


def test_the_existence_check_is_scoped_too(engine, mock_conn):
    """A node with no labels and no properties still has to exist *in this graph*.

    The unscoped `SELECT node_id FROM nodes WHERE node_id IN (...)` answered "present" for
    a node that only exists in another graph, so `get_node` returned a bare, label-less
    node dict instead of None.
    """
    cursor = mock_conn.cursor.return_value
    cursor.execute.reset_mock()
    # No labels, no props, and the scoped existence check finds nothing.
    cursor.fetchall.side_effect = [[], [], []]

    assert engine.get_nodes(["n1"], graph="ga") == []

    existence = [s for s in _statements(cursor) if "nodes" in s[0] and "node_id IN" in s[0]]
    assert existence, _statements(cursor)
    for sql, params in existence:
        assert "graph_id = ?" in sql, sql
        assert "ga" in params, (sql, params)
