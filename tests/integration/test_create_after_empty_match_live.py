"""A CREATE after a MATCH that binds nothing must write nothing.

Measured on the enterprise container before the fix:

    MATCH (a:Person {id:'TXCR:missing'}) CREATE (a)-[:R]->(b:Person {id:'TXCR:new'})

left `TXCR:new` in `nodes`, its `Person` row in `rdf_labels`, and its `id` row in
`rdf_props` — with no edge and no error. openCypher runs a CREATE once per incoming
row, so zero rows means zero writes.

Live because the half-write was in the rows, not in the generated SQL: the unit test
(tests/unit/test_create_after_empty_match.py) pins the statement shape, this pins what
the statements actually do.

Row multiplicity stays out of scope: a MATCH returning N rows still creates one node
here, not N.
"""

import os

import pytest


SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

PREFIX = "TXCR"


def _purge(engine):
    cursor = engine.conn.cursor()
    try:
        cursor.execute(
            f"DELETE FROM Graph_KG.rdf_edges WHERE s %STARTSWITH '{PREFIX}' "
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


def _count(engine, sql, params=None):
    cursor = engine.conn.cursor()
    try:
        cursor.execute(sql, params or [])
        return cursor.fetchone()[0]
    finally:
        cursor.close()


def _traces(engine, node_id):
    """Every row any table holds for this node ID."""
    return (
        _count(
            engine,
            "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ?",
            [node_id],
        ),
        _count(
            engine,
            "SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE s = ?",
            [node_id],
        ),
        _count(
            engine,
            "SELECT COUNT(*) FROM Graph_KG.rdf_props WHERE s = ?",
            [node_id],
        ),
        _count(
            engine,
            "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s = ? OR o_id = ?",
            [node_id, node_id],
        ),
    )


class TestAZeroRowMatchCreatesNothing:

    def test_nothing_is_written_when_the_match_binds_nothing(self, clean):
        engine = clean
        engine.execute_cypher(
            f"MATCH (a:Person {{id:'{PREFIX}:missing'}}) "
            f"CREATE (a)-[:R]->(b:Person {{id:'{PREFIX}:new'}})"
        )
        assert _traces(engine, f"{PREFIX}:new") == (0, 0, 0, 0)

    def test_a_matching_row_still_creates_the_node_and_the_edge(self, clean):
        """The control: gating must not turn a working CREATE into a no-op."""
        engine = clean
        engine.execute_cypher(f"CREATE (a:Person {{id:'{PREFIX}:here'}})")
        engine.execute_cypher(
            f"MATCH (a:Person {{id:'{PREFIX}:here'}}) "
            f"CREATE (a)-[:R]->(b:Person {{id:'{PREFIX}:made'}})"
        )
        nodes, labels, props, edges = _traces(engine, f"{PREFIX}:made")
        assert nodes == 1
        assert labels == 1
        assert props == 1
        assert edges == 1

    def test_rerunning_the_gated_create_adds_no_duplicate_node(self, clean):
        """The `NOT EXISTS` guard still holds once the statement joins the match.

        The second CREATE names a different relationship type on purpose. Re-creating
        an *identical* edge is refused by the storage layer — `Graph_KG.rdf_edges` has
        `u_spo_graph UNIQUE (s, p, o_id, graph_id)`, so a repeated `CREATE` of the same
        triple raises `SQLCODE -119` where openCypher would add a parallel edge. That
        is a pre-existing deviation on a statement this gate does not touch, and it
        would mask the node-level check being made here.
        """
        engine = clean
        engine.execute_cypher(f"CREATE (a:Person {{id:'{PREFIX}:twice'}})")
        for rel in ("R", "R2"):
            engine.execute_cypher(
                f"MATCH (a:Person {{id:'{PREFIX}:twice'}}) "
                f"CREATE (a)-[:{rel}]->(b:Person {{id:'{PREFIX}:dup'}})"
            )
        nodes, labels, props, edges = _traces(engine, f"{PREFIX}:dup")
        assert (nodes, labels, props) == (1, 1, 1)
        assert edges == 2

    def test_a_bare_create_is_unaffected(self, clean):
        """No MATCH, no gate — this path must keep writing as it always did."""
        engine = clean
        engine.execute_cypher(f"CREATE (b:Person {{id:'{PREFIX}:bare'}})")
        nodes, labels, props, _ = _traces(engine, f"{PREFIX}:bare")
        assert (nodes, labels, props) == (1, 1, 1)
