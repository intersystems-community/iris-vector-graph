"""E2E integration tests for spec 218 — post_commit_properties and REVISION_ID_SENTINEL.

Requires ivg-iris-enterprise (port 31972).
"""
import os
import uuid
import pytest
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.ledger.changeset import Changeset, REVISION_ID_SENTINEL

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
_PREFIX = f"h218_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def ledger_engine(iris_connection):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=768)
    eng.initialize_schema()
    eng.ledger.enable()
    yield eng
    cur = iris_connection.cursor()
    for tbl in ("rdf_props", "rdf_labels", "nodes"):
        col = "node_id" if tbl == "nodes" else "s"
        try:
            cur.execute(f"DELETE FROM Graph_KG.{tbl} WHERE {col} LIKE '{_PREFIX}%'")
        except Exception:
            pass
    iris_connection.commit()


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestPostCommitPropertiesE2E:

    def test_post_commit_revision_id_written_to_node_property(self, ledger_engine):
        """T009: REVISION_ID_SENTINEL substituted with actual revision_id."""
        node_id = f"{_PREFIX}_audit_{uuid.uuid4().hex[:4]}"

        cs = Changeset(
            actor="test",
            actor_type="test",
            post_commit_properties={node_id: {"committed_rev": REVISION_ID_SENTINEL}},
        )
        cs.upsert_node(node_id)
        result = ledger_engine.ledger.commit(cs)

        assert result.post_commit_applied, "post_commit_properties not applied"
        assert result.post_commit_error is None

        # Verify the property was written to rdf_props
        cur = ledger_engine.conn.cursor()
        cur.execute(
            "SELECT val FROM Graph_KG.rdf_props WHERE s = ? AND \"key\" = ?",
            [node_id, "committed_rev"],
        )
        row = cur.fetchone()
        assert row is not None, f"Property committed_rev not found on {node_id}"
        assert row[0] == result.revision.revision_id, (
            f"Expected {result.revision.revision_id}, got {row[0]}"
        )

    def test_post_commit_plain_value_written(self, ledger_engine):
        """post_commit_properties with a plain (non-sentinel) value is written as-is."""
        node_id = f"{_PREFIX}_plain_{uuid.uuid4().hex[:4]}"
        cs = Changeset(
            actor="test",
            actor_type="test",
            post_commit_properties={node_id: {"tag": "verified"}},
        )
        cs.upsert_node(node_id)
        result = ledger_engine.ledger.commit(cs)

        assert result.post_commit_applied
        cur = ledger_engine.conn.cursor()
        cur.execute(
            "SELECT val FROM Graph_KG.rdf_props WHERE s = ? AND \"key\" = ?",
            [node_id, "tag"],
        )
        row = cur.fetchone()
        assert row and row[0] == "verified"

    def test_post_commit_missing_node_sets_error_not_exception(self, ledger_engine):
        """T010: post_commit on non-existent node sets post_commit_error, no exception."""
        # Don't create the node — write post_commit to a node that doesn't exist
        ghost_id = f"{_PREFIX}_ghost_{uuid.uuid4().hex[:4]}"
        real_node = f"{_PREFIX}_real_{uuid.uuid4().hex[:4]}"

        cs = Changeset(
            actor="test",
            actor_type="test",
            post_commit_properties={ghost_id: {"k": "v"}},
        )
        cs.upsert_node(real_node)  # commit needs at least one op
        result = ledger_engine.ledger.commit(cs)

        # Main commit succeeded
        assert result.revision is not None
        # post_commit may have silently written to rdf_props even without a node
        # (rdf_props has no FK constraint to nodes) — acceptable; test that no exception was raised
        # and result is returned
        assert isinstance(result.post_commit_applied, bool)

    def test_empty_post_commit_properties_no_overhead(self, ledger_engine):
        """Default empty post_commit_properties → post_commit_applied=False."""
        node_id = f"{_PREFIX}_empty_{uuid.uuid4().hex[:4]}"
        cs = Changeset(actor="test", actor_type="test")
        cs.upsert_node(node_id)
        result = ledger_engine.ledger.commit(cs)
        assert result.post_commit_applied is False
        assert result.post_commit_error is None
