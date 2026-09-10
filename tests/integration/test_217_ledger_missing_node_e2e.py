"""E2E integration tests for spec 217 — NodeNotFoundError and auto_stub_missing_nodes.

Requires ivg-iris-enterprise (port 31972).
"""
import os
import uuid
import pytest
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.ledger.changeset import Changeset
from iris_vector_graph.ledger.errors import NodeNotFoundError

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
_PREFIX = f"h217_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def ledger_engine(iris_connection):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=768)
    eng.initialize_schema()
    eng.ledger.enable()
    yield eng
    cur = iris_connection.cursor()
    for tbl in ("rdf_edges", "rdf_props", "rdf_labels", "nodes"):
        try:
            cur.execute(f"DELETE FROM Graph_KG.{tbl} WHERE "
                        f"{'node_id' if tbl == 'nodes' else 's'} LIKE '{_PREFIX}%'")
        except Exception:
            pass
    iris_connection.commit()


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestMissingNodeE2E:

    def test_missing_node_raises_node_not_found_error(self, ledger_engine):
        """T011: commit with missing target node raises NodeNotFoundError."""
        cs = Changeset(
            actor="test",
            actor_type="test",
            auto_stub_missing_nodes=False,
        )
        missing_id = f"{_PREFIX}_missing_{uuid.uuid4().hex[:4]}"
        existing_id = f"{_PREFIX}_existing_{uuid.uuid4().hex[:4]}"
        # Create source node first
        ledger_engine.create_node(existing_id)
        cs.create_relationship(existing_id, "CALLS", missing_id)

        with pytest.raises(NodeNotFoundError) as exc_info:
            ledger_engine.ledger.commit(cs)

        assert exc_info.value.missing_node == missing_id
        assert missing_id in str(exc_info.value)

    def test_auto_stub_creates_missing_node_and_relationship(self, ledger_engine):
        """T010: auto_stub=True creates stub target node and the relationship."""
        src = f"{_PREFIX}_auto_src_{uuid.uuid4().hex[:4]}"
        tgt = f"{_PREFIX}_auto_tgt_{uuid.uuid4().hex[:4]}"

        cs = Changeset(
            actor="test",
            actor_type="test",
            auto_stub_missing_nodes=True,
        )
        cs.create_relationship(src, "CALLS", tgt)
        result = ledger_engine.ledger.commit(cs)
        assert not result.replayed

        # Both nodes must exist after commit
        cur = ledger_engine.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ?", [src])
        assert cur.fetchone()[0] > 0, f"Source node {src} not found"
        cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ?", [tgt])
        assert cur.fetchone()[0] > 0, f"Target node {tgt} not found"

        # Relationship visible in diff
        changes = ledger_engine.ledger.diff(
            ledger_engine.ledger.history(limit=1, descending=True).revisions[0].parent_id or
            ledger_engine.ledger.history(limit=1).revisions[0].revision_id,
            result.revision.revision_id,
        )
        rel_changes = [e for e in changes.entries if e.entity_kind == "rel"]
        assert any(
            e.rel_info and e.rel_info["s"] == src and e.rel_info["o"] == tgt
            for e in rel_changes
        ), f"Relationship {src}→{tgt} not found in diff"
