"""An edge in a named graph needs its endpoints in that same graph.

Spec 227 re-keyed `Graph_KG.nodes` on `(graph_id, node_id)`, so `rdf_edges` now
carries composite foreign keys:

    CONSTRAINT fk_edges_source FOREIGN KEY (graph_id, s)    REFERENCES nodes (graph_id, node_id)
    CONSTRAINT fk_edges_dest   FOREIGN KEY (graph_id, o_id) REFERENCES nodes (graph_id, node_id)

Before the re-key the key was `node_id` alone, so a node created once — in any
graph — satisfied an edge written in every graph. That is the whole reason the
edge writers never had to think about `nodes`: `create_node("a")` followed by
`create_edge("a", "KNOWS", "b", graph="acme")` was accepted, and the edge row
pointed at a node row belonging to the default graph.

Under the composite key that same pair of calls is `SQLCODE -121`, caught and
logged by `create_edge`, which then returns False — the same False it returns for
a duplicate. So a named-graph load reports "already there" and writes nothing.

These tests pin the fix at the writers: every edge path ensures a `nodes` row for
`(graph, s)` and `(graph, o_id)` before inserting the edge. The live proof is in
`tests/integration/test_227_edge_endpoints_e2e.py`, because the guarantee is the
constraint, not the Python.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from iris_vector_graph.engine import IRISGraphEngine


def _engine():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.description = [("node_id", None)]
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    cursor.reset_mock()
    return engine, conn, cursor


def _statements(cursor):
    """Every SQL string the writer sent, in order, execute and executemany alike."""
    out = []
    for call in cursor.execute.call_args_list:
        out.append((call.args[0] if call.args else "", call.args[1] if len(call.args) > 1 else []))
    for call in cursor.executemany.call_args_list:
        sql = call.args[0] if call.args else ""
        for params in (call.args[1] if len(call.args) > 1 else []) or []:
            out.append((sql, params))
    return out


def _node_inserts(cursor):
    """The (graph_id, node_id) pairs the writer ensured in `nodes`."""
    pairs = []
    for sql, params in _statements(cursor):
        upper = sql.upper()
        if "INSERT INTO" in upper and "GRAPH_KG.NODES" in upper:
            # Both templates put node_id then graph_id first; the guard repeats them.
            pairs.append((params[1] if len(params) > 1 else "", params[0]))
    return pairs


class TestCreateEdge:
    def test_both_endpoints_are_ensured_in_the_edges_graph(self):
        engine, _conn, cursor = _engine()

        engine.create_edge("a", "KNOWS", "b", graph="acme")

        pairs = _node_inserts(cursor)
        assert ("acme", "a") in pairs, "the source node was not ensured in the edge's graph"
        assert ("acme", "b") in pairs, "the target node was not ensured in the edge's graph"

    def test_the_endpoints_are_ensured_before_the_edge_is_inserted(self):
        """Otherwise the FK refuses the edge and create_edge reports a duplicate."""
        engine, _conn, cursor = _engine()

        engine.create_edge("a", "KNOWS", "b", graph="acme")

        order = [sql.upper() for sql, _ in _statements(cursor)]
        first_node = next(
            i for i, s in enumerate(order) if "INSERT INTO" in s and "GRAPH_KG.NODES" in s
        )
        first_edge = next(
            i for i, s in enumerate(order) if "INSERT INTO" in s and "RDF_EDGES" in s
        )
        assert first_node < first_edge

    def test_the_default_graph_gets_the_empty_spelling(self):
        engine, _conn, cursor = _engine()

        engine.create_edge("a", "KNOWS", "b")

        pairs = _node_inserts(cursor)
        assert ("", "a") in pairs
        assert ("", "b") in pairs

    def test_a_self_edge_ensures_its_one_endpoint(self):
        engine, _conn, cursor = _engine()

        engine.create_edge("a", "KNOWS", "a", graph="acme")

        assert _node_inserts(cursor).count(("acme", "a")) == 1, (
            "the same endpoint was ensured twice, so a self-edge costs two inserts"
        )


class TestBulkCreateEdges:
    def test_endpoints_are_ensured_for_a_named_graph_batch(self):
        engine, _conn, cursor = _engine()

        engine.bulk_create_edges(
            [
                {"source_id": "a", "predicate": "KNOWS", "target_id": "b"},
                {"source_id": "b", "predicate": "KNOWS", "target_id": "c"},
            ],
            graph="acme",
            disable_indexes=False,
            auto_sync=False,
        )

        pairs = _node_inserts(cursor)
        for node in ("a", "b", "c"):
            assert ("acme", node) in pairs, f"{node} was not ensured in the batch's graph"
        assert pairs.count(("acme", "b")) == 1, "a shared endpoint was ensured twice"

    def test_endpoints_are_ensured_for_a_default_graph_batch(self):
        engine, _conn, cursor = _engine()

        engine.bulk_create_edges(
            [{"source_id": "a", "predicate": "KNOWS", "target_id": "b"}],
            disable_indexes=False,
            auto_sync=False,
        )

        pairs = _node_inserts(cursor)
        assert ("", "a") in pairs
        assert ("", "b") in pairs


class TestBulkIngestEdges:
    def test_endpoints_are_ensured_on_the_sql_fallback(self):
        engine, _conn, cursor = _engine()
        # Force the SQL path: the ObjectScript path is covered live.
        engine.capabilities.objectscript_deployed = False

        engine.bulk_ingest_edges([{"s": "a", "p": "KNOWS", "o": "b"}], auto_sync=False)

        pairs = _node_inserts(cursor)
        assert ("", "a") in pairs
        assert ("", "b") in pairs
