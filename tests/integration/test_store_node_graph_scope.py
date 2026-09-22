"""`store_node` / `store_edge` named no graph, and the index rebuild exposed it.

Both writers inserted `INTO Graph_KG.nodes (node_id) VALUES (?)` and let the
`graph_id` column default fill itself in. That was invisible until 4.0.0's
`%NOINDEX` bulk-load fix made phase 5 actually run `%BuildIndices`, because a
connection holding a cached one-column INSERT for that class stops being able to
decode the class's own unique-constraint error once the indices are rebuilt.
Measured on ivg-iris-enterprise over two connections, A having inserted first and
B running the rebuild:

```text
A first                        OK
build on B                     1
A dup, same SQL text           DataError: <LIST ERROR> Incorrect list format,
                               unsupported type for IRISList; type detected : 0
A dup, graph_id spelled out    IntegrityError: SQLCODE <-119> UNIQUE ... failed
B dup (B did the rebuild)      IntegrityError: SQLCODE <-119> UNIQUE ... failed
```

`store_node` swallows a duplicate by matching `-119` / "duplicate" / "unique" in
the message, and `<LIST ERROR>` matches none of them, so the second call raised
and `test_coverage_final_push.py::test_store_node_duplicate_is_idempotent` failed
whenever `test_bulk_loader_live.py` had run first. Nothing clears the state:
`rollback()`, a `SELECT 1` and a fresh cursor all still get `<LIST ERROR>`, and
only a statement that names `graph_id` decodes correctly.

Naming the column is also what spec 227 asks of a writer, so these tests fix the
scope gap and the decode artifact in one move: `store_node` and `store_edge` take
a `graph`, and their nodes, labels, properties and edges all land in it.
"""

from __future__ import annotations

import os
import socket
import time

import pytest

from iris_vector_graph.schema import _call_classmethod

_CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")


def _second_connection():
    """A connection of our own, so the rebuild happens off the fixture's."""
    import iris as _iris

    try:
        orb_ip = socket.gethostbyname(f"{_CONTAINER}.orb.local")
        return _iris.connect(
            hostname=orb_ip, port=1972, namespace="USER", username="_SYSTEM", password="SYS"
        )
    except Exception:
        return _iris.connect(
            hostname="localhost",
            port=int(os.environ.get("IVG_PORT", "31972")),
            namespace="USER",
            username="_SYSTEM",
            password="SYS",
        )


@pytest.fixture
def scoped_ids(iris_connection):
    """Unique ids per run, cleaned out of every table afterwards."""
    stamp = int(time.time() * 1000)
    ids = {
        "a": f"sns_a_{stamp}",
        "b": f"sns_b_{stamp}",
        "graph": f"sns_graph_{stamp}",
    }
    yield ids
    cur = iris_connection.cursor()
    try:
        for table, col in (
            ("rdf_edges", "s"),
            ("rdf_edges", "o_id"),
            ("rdf_props", "s"),
            ("rdf_labels", "s"),
            ("nodes", "node_id"),
        ):
            cur.execute(
                f"DELETE FROM Graph_KG.{table} WHERE {col} IN (?, ?)", [ids["a"], ids["b"]]
            )
        iris_connection.commit()
    finally:
        cur.close()


def _graphs_of(conn, node_id: str) -> list:
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT graph_id FROM Graph_KG.nodes WHERE node_id = ? ORDER BY graph_id",
            [node_id],
        )
        return [str(r[0] or "") for r in cur.fetchall()]
    finally:
        cur.close()


class TestStoreNodeSurvivesAnIndexRebuild:
    def test_duplicate_is_idempotent_after_build_indices(self, engine, iris_connection, scoped_ids):
        """The failing case, with the cause in the test rather than in the ordering.

        A second connection rebuilds the indices between the two `store_node`
        calls, which is exactly what a `%NOINDEX` bulk load does to any other
        connection that has already written a node.
        """
        nid = scoped_ids["a"]
        assert engine.store_node(nid) is True

        rebuilder = _second_connection()
        try:
            assert _call_classmethod(rebuilder, "Graph.KG.nodes", "%BuildIndices")
        finally:
            rebuilder.close()

        # Used to raise iris.dbapi.DataError: <LIST ERROR> ... type detected : 0
        assert engine.store_node(nid) is True


class TestStoreNodeTakesAGraph:
    def test_no_graph_lands_in_the_default_graph(self, engine, scoped_ids):
        nid = scoped_ids["a"]
        assert engine.store_node(nid) is True
        assert _graphs_of(engine.conn, nid) == [""]

    def test_a_named_graph_is_written_not_defaulted(self, engine, scoped_ids):
        nid, g = scoped_ids["a"], scoped_ids["graph"]
        assert engine.store_node(nid, graph=g) is True
        assert _graphs_of(engine.conn, nid) == [g]

    def test_the_same_node_id_can_sit_in_two_graphs(self, engine, scoped_ids):
        """4.0.0 keys `nodes` on `(graph_id, node_id)`, so this is representable."""
        nid, g = scoped_ids["a"], scoped_ids["graph"]
        assert engine.store_node(nid) is True
        assert engine.store_node(nid, graph=g) is True
        assert _graphs_of(engine.conn, nid) == ["", g]

    def test_labels_and_properties_follow_the_node_into_its_graph(self, engine, scoped_ids):
        nid, g = scoped_ids["a"], scoped_ids["graph"]
        assert engine.store_node(nid, properties={"name": "Alice"}, labels=["Scoped"], graph=g) is True

        cur = engine.conn.cursor()
        try:
            cur.execute(
                "SELECT graph_id FROM Graph_KG.rdf_labels WHERE s = ? AND label = ?", [nid, "Scoped"]
            )
            rows = cur.fetchall()
            assert rows, "the label was not written at all"
            assert [str(r[0] or "") for r in rows] == [g]

            cur.execute(
                "SELECT graph_id FROM Graph_KG.rdf_props WHERE s = ? AND \"key\" = ?", [nid, "name"]
            )
            rows = cur.fetchall()
            assert rows, "the property was not written at all"
            assert [str(r[0] or "") for r in rows] == [g]
        finally:
            cur.close()


class TestStoreEdgeTakesTheSameGraph:
    def test_edge_and_both_endpoints_land_in_the_named_graph(self, engine, scoped_ids):
        """The composite FKs require the endpoints to be registered in the edge's graph."""
        a, b, g = scoped_ids["a"], scoped_ids["b"], scoped_ids["graph"]
        assert engine.store_edge(a, "SCOPED_REL", b, graph=g) is True

        assert _graphs_of(engine.conn, a) == [g]
        assert _graphs_of(engine.conn, b) == [g]

        cur = engine.conn.cursor()
        try:
            cur.execute(
                "SELECT graph_id FROM Graph_KG.rdf_edges WHERE s = ? AND p = ? AND o_id = ?",
                [a, "SCOPED_REL", b],
            )
            rows = cur.fetchall()
            assert rows, "the edge was not written at all"
            assert [str(r[0] or "") for r in rows] == [g]
        finally:
            cur.close()

    def test_no_graph_still_writes_the_default_graph(self, engine, scoped_ids):
        a, b = scoped_ids["a"], scoped_ids["b"]
        assert engine.store_edge(a, "SCOPED_REL", b) is True
        assert _graphs_of(engine.conn, a) == [""]
        assert _graphs_of(engine.conn, b) == [""]
