"""Spec 230 US5 — `get_schema_visualization` leaves the caller's connection usable.

The unit test beside this one reads the SQL and asserts every statement caps
itself. This one proves what that cap is for, and it has to run live: a
desynchronized wire protocol is not visible in the SQL text, only in what the
connection does afterwards.

Against a namespace where some predicate has more edges than one fetch buffer
holds, the uncapped endpoint lookup left rows pending, the next `execute()` on the
same cursor arrived out of sequence, and IRIS closed the connection:

    <COMMUNICATION ERROR> Message out of order; Invalid Message Sequence Number
    <COMMUNICATION LINK ERROR> Connection closed        (every later statement)

The caller loses a connection it still owns, so the damage is not confined to this
method's own result.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true",
    reason="SKIP_IRIS_TESTS=true",
)


@pytest.fixture
def crowded_predicate(iris_connection):
    """One predicate with more edges than a single fetch returns.

    The count is what matters, not the shape: the defect needs rows left pending
    after `fetchone()`. 200 is comfortably past any fetch buffer and still quick.
    """
    tag = uuid.uuid4().hex[:8]
    pred = f"svz_{tag}_LINKS"
    nodes = [f"svz_{tag}_{i}" for i in range(201)]
    cur = iris_connection.cursor()
    try:
        for nid in nodes:
            cur.execute(
                "INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, ?)", [nid, ""]
            )
            cur.execute(
                "INSERT INTO Graph_KG.rdf_labels (s, label, graph_id) VALUES (?, ?, ?)",
                [nid, f"SvzLabel_{tag}", ""],
            )
        for i in range(200):
            cur.execute(
                "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, graph_id, qualifiers) "
                "VALUES (?, ?, ?, ?, '{}')",
                [nodes[i], pred, nodes[i + 1], ""],
            )
        iris_connection.commit()
    finally:
        cur.close()

    yield pred

    cur = iris_connection.cursor()
    try:
        cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE p = ?", [pred])
        cur.execute("DELETE FROM Graph_KG.rdf_labels WHERE label = ?", [f"SvzLabel_{tag}"])
        for nid in nodes:
            cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id = ?", [nid])
        iris_connection.commit()
    finally:
        cur.close()


def test_visualization_returns_and_the_connection_still_works(
    iris_connection, crowded_predicate
):
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection, embedding_dimension=768)

    schema = engine.get_schema_visualization()
    assert isinstance(schema, dict)
    assert "nodes" in schema and "relationships" in schema

    # The point of the test: the caller's connection is still the caller's.
    cur = iris_connection.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE p = ?", [crowded_predicate])
        assert cur.fetchall()[0][0] == 200
    finally:
        cur.close()


def test_the_crowded_predicate_is_in_the_report(iris_connection, crowded_predicate):
    """A pass that reported nothing about the crowded predicate would prove nothing."""
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection, embedding_dimension=768)
    schema = engine.get_schema_visualization()
    names = {r.get("name") for r in schema["relationships"]}
    assert crowded_predicate in names, (
        f"{crowded_predicate} is missing from the relationship list, so the query that "
        "carried the defect was never reached"
    )
