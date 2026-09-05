"""Spec 213 FR-043 — ledger storage survives snapshot save/restore (T052)."""

import os

import pytest

from iris_vector_graph.ledger import Changeset
from tests.integration._ledger_helpers import canonical_tables, make_engine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def engine(iris_connection, ledger_reset):
    eng = make_engine(iris_connection)
    eng.ledger.enable()
    return eng


class TestSnapshotRoundTrip:
    def test_save_wipe_restore_preserves_head_history_and_statements(self, engine, tmp_path):
        cs1 = Changeset(actor="a", actor_type="human", message="one")
        cs1.create_node("a", labels=["L"], properties={"k": "v"})
        cs1.create_node("b")
        r = cs1.create_relationship("a", "R", "b", qualifiers={"weight": "3"})
        cs1.set_qualifier(r, "cap", "9")
        engine.ledger.commit(cs1)
        cs2 = Changeset(actor="b", actor_type="agent", message="two", idempotency_key="k2")
        cs2.set_property("a", "k", "v2")
        res2 = engine.ledger.commit(cs2)
        head = engine.ledger.head()
        tables = canonical_tables(engine.conn)
        recs2 = [
            rec.to_wire() for rec in engine.ledger.get_revision(res2.revision.revision_id).records
        ]
        stmt = str(
            engine._iris_obj().classMethodValue(
                "Graph.KG.Ledger", "StmtForTuple", "a", "R", "b", ""
            )
        )
        assert stmt != ""

        path = str(tmp_path / "ledger.snapshot")
        engine.save_snapshot(path, layers=["sql", "globals"])

        # wipe everything: ledger and structural graph
        engine._iris_obj().classMethodValue("Graph.KG.Ledger", "PurgeAll")
        cur = engine.conn.cursor()
        for t in ["rdf_edges", "rdf_labels", "rdf_props", "nodes"]:
            cur.execute(f"DELETE FROM Graph_KG.{t}")
        engine.conn.commit()
        cur.close()
        assert engine.ledger.meta()["state"] == "never-enabled"

        engine.restore_snapshot(path, merge=False)

        assert engine.ledger.meta()["state"] == "enabled"
        restored_head = engine.ledger.head()
        assert restored_head.revision_id == head.revision_id and restored_head.seq == head.seq
        assert canonical_tables(engine.conn) == tables
        assert [
            rec.to_wire() for rec in engine.ledger.get_revision(res2.revision.revision_id).records
        ] == recs2
        assert (
            str(
                engine._iris_obj().classMethodValue(
                    "Graph.KG.Ledger", "StmtForTuple", "a", "R", "b", ""
                )
            )
            == stmt
        )
        # idempotency binding survived
        again = engine.ledger.commit(
            Changeset(actor="b", actor_type="agent", idempotency_key="k2").set_property(
                "a", "k", "v2"
            )
        )
        assert again.replayed is True and again.revision.revision_id == res2.revision.revision_id
        assert engine.ledger.verify().result == "equal"
