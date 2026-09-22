"""`MERGE (a {id:…})-[:R]->(b {id:…})` has to run, and run twice for one edge.

Measured on the enterprise container before the fix, this one-liner never worked in
any graph:

    [SQLCODE: <-23>:<Label is not listed among the applicable tables>]
    %msg: Label 'N0' is not listed among the applicable tables^INSERT INTO
          Graph_KG.rdf_edges (s, p, o_id) SELECT ?, ?, ? WHERE NOT EXISTS
          (SELECT 1 FROM rdf_edges WHERE s = n0.node_id AND p = ? AND o_id = n1.node_id)

The guard named aliases the statement does not select from, because inline nodes carry
no CREATE-generated UUID and the value form fell back to the alias form. The same MERGE
over MATCH-bound variables always worked, which is what kept this hidden.

Live because the failure was IRIS refusing to prepare the statement — a unit test on the
generated DML (tests/unit/test_merge_relationship_inline_nodes.py) cannot see that.
"""

import os

import pytest


SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

PREFIX = "MRGE"


def _purge(engine):
    cursor = engine.conn.cursor()
    try:
        for table in ("rdf_edges",):
            cursor.execute(
                f"DELETE FROM Graph_KG.{table} WHERE s %STARTSWITH '{PREFIX}' "
                f"OR o_id %STARTSWITH '{PREFIX}'"
            )
        for table in ("rdf_labels", "rdf_props"):
            cursor.execute(
                f"DELETE FROM Graph_KG.{table} WHERE s %STARTSWITH '{PREFIX}'"
            )
        cursor.execute(
            f"DELETE FROM Graph_KG.nodes WHERE node_id %STARTSWITH '{PREFIX}'"
        )
        engine.conn.commit()
    finally:
        cursor.close()


@pytest.fixture
def clean(engine):
    _purge(engine)
    yield engine
    _purge(engine)


def _edges(engine, src):
    cursor = engine.conn.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s = ? AND p = 'LIKES'",
            [src],
        )
        return cursor.fetchone()[0]
    finally:
        cursor.close()


class TestInlineNodeMerge:

    def test_a_directed_merge_runs_and_is_idempotent(self, clean):
        engine = clean
        cypher = (
            f"MERGE (a:Person {{id:'{PREFIX}:a'}})-[:LIKES]->(b:Person {{id:'{PREFIX}:b'}})"
        )
        engine.execute_cypher(cypher)
        assert _edges(engine, f"{PREFIX}:a") == 1
        engine.execute_cypher(cypher)
        assert _edges(engine, f"{PREFIX}:a") == 1

    def test_an_undirected_merge_is_idempotent_in_both_orientations(self, clean):
        engine = clean
        engine.execute_cypher(
            f"MERGE (a:Person {{id:'{PREFIX}:c'}})-[:LIKES]-(b:Person {{id:'{PREFIX}:d'}})"
        )
        # The reverse spelling of the same undirected pattern must not add a second edge.
        engine.execute_cypher(
            f"MERGE (b:Person {{id:'{PREFIX}:d'}})-[:LIKES]-(a:Person {{id:'{PREFIX}:c'}})"
        )
        total = _edges(engine, f"{PREFIX}:c") + _edges(engine, f"{PREFIX}:d")
        assert total == 1

    def test_the_endpoints_are_registered_nodes(self, clean):
        """4.0.0's composite FKs mean a MERGE that skipped this could not insert at all."""
        engine = clean
        engine.execute_cypher(
            f"MERGE (a:Person {{id:'{PREFIX}:e'}})-[:LIKES]->(b:Person {{id:'{PREFIX}:f'}})"
        )
        cursor = engine.conn.cursor()
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id IN (?, ?)",
                [f"{PREFIX}:e", f"{PREFIX}:f"],
            )
            assert cursor.fetchone()[0] == 2
        finally:
            cursor.close()

    def test_a_merge_over_matched_variables_still_works(self, clean):
        """The alias form is the shape that always worked; it must keep working."""
        engine = clean
        engine.execute_cypher(f"CREATE (a:Person {{id:'{PREFIX}:g'}})")
        engine.execute_cypher(f"CREATE (b:Person {{id:'{PREFIX}:h'}})")
        cypher = (
            f"MATCH (a:Person {{id:'{PREFIX}:g'}}), (b:Person {{id:'{PREFIX}:h'}}) "
            "MERGE (a)-[:LIKES]->(b)"
        )
        engine.execute_cypher(cypher)
        engine.execute_cypher(cypher)
        assert _edges(engine, f"{PREFIX}:g") == 1
