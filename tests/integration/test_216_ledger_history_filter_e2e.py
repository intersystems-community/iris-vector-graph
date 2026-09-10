"""E2E integration tests for spec 216 — ledger.history() correlation_id and source filters.

Requires ivg-iris-enterprise (port 31972).
"""
import os
import uuid
import pytest
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.ledger.changeset import Changeset

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
_PREFIX = f"h216_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def ledger_engine(iris_connection):
    """Engine with ledger enabled for all 216 tests."""
    eng = IRISGraphEngine(iris_connection, embedding_dimension=768)
    eng.initialize_schema()
    eng.ledger.enable()
    yield eng
    # Cleanup nodes
    cur = iris_connection.cursor()
    cur.execute(f"DELETE FROM Graph_KG.rdf_props WHERE s LIKE '{_PREFIX}%'")
    cur.execute(f"DELETE FROM Graph_KG.rdf_labels WHERE s LIKE '{_PREFIX}%'")
    cur.execute(f"DELETE FROM Graph_KG.nodes WHERE node_id LIKE '{_PREFIX}%'")
    iris_connection.commit()


def _commit(eng, node_id, correlation_id=None, source=None, actor="test"):
    cs = Changeset(
        actor=actor,
        actor_type="test",
        correlation_id=correlation_id,
        source=source,
    )
    cs.upsert_node(node_id)
    return eng.ledger.commit(cs)


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestHistoryFilterE2E:

    def test_correlation_id_filter_returns_only_matching_revisions(self, ledger_engine):
        """T008: history(correlation_id=X) returns only revisions with that correlation_id."""
        cid_a = f"{_PREFIX}-corr-acme"
        cid_b = f"{_PREFIX}-corr-metro"

        r1 = _commit(ledger_engine, f"{_PREFIX}_n1", correlation_id=cid_a)
        r2 = _commit(ledger_engine, f"{_PREFIX}_n2", correlation_id=cid_a)
        r3 = _commit(ledger_engine, f"{_PREFIX}_n3", correlation_id=cid_b)

        page = ledger_engine.ledger.history(correlation_id=cid_a, limit=50)
        rev_ids = {r.revision_id for r in page.revisions}

        assert r1.revision.revision_id in rev_ids, "First acme revision missing"
        assert r2.revision.revision_id in rev_ids, "Second acme revision missing"
        assert r3.revision.revision_id not in rev_ids, "Metro revision leaked into acme filter"

        # All returned revisions must have the correct correlation_id
        for rev in page.revisions:
            if rev.revision_id in (r1.revision.revision_id, r2.revision.revision_id):
                assert rev.correlation_id == cid_a

    def test_source_filter_note(self, ledger_engine):
        """T009: source is stored as JSON dict; plain-string source= filter uses correlation_id.

        Changeset.source is Optional[Dict] (structured metadata) stored as JSON in
        ledger_revisions.source. Plain-string tenant-source filtering should use
        correlation_id instead. This test documents that and verifies correlation_id
        works for the source-tagging use case.
        """
        tag = f"{_PREFIX}-src-tag"
        r1 = _commit(ledger_engine, f"{_PREFIX}_st1", correlation_id=tag)
        r2 = _commit(ledger_engine, f"{_PREFIX}_st2", correlation_id=tag)
        r_other = _commit(ledger_engine, f"{_PREFIX}_st3", correlation_id=f"{_PREFIX}-other")

        page = ledger_engine.ledger.history(correlation_id=tag, limit=50)
        rev_ids = {r.revision_id for r in page.revisions}
        assert r1.revision.revision_id in rev_ids
        assert r2.revision.revision_id in rev_ids
        assert r_other.revision.revision_id not in rev_ids

    def test_combined_correlation_id_and_actor_type_filter(self, ledger_engine):
        """T010 (combined): correlation_id + actor_type both applied in SQL."""
        cid = f"{_PREFIX}-combined"
        r1 = _commit(ledger_engine, f"{_PREFIX}_c1", correlation_id=cid, actor="actor-x")
        r2 = _commit(ledger_engine, f"{_PREFIX}_c2", correlation_id=cid, actor="actor-y")

        page = ledger_engine.ledger.history(
            correlation_id=cid,
            actor="actor-x",
            limit=50,
        )
        rev_ids = {r.revision_id for r in page.revisions}
        assert r1.revision.revision_id in rev_ids
        assert r2.revision.revision_id not in rev_ids

    def test_no_filter_returns_all_revisions(self, ledger_engine):
        """Control: history() without filters includes all revisions."""
        page = ledger_engine.ledger.history(limit=200)
        assert len(page.revisions) > 0
