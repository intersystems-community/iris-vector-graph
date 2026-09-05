"""Spec 213 US10 — entity lifecycle history (live ivg-iris-enterprise). T090."""

import os

import pytest

from iris_vector_graph.ledger import Changeset, ChangesetOperationError
from tests.integration._ledger_helpers import canonical_tables, make_engine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def engine(iris_connection, ledger_reset):
    eng = make_engine(iris_connection)
    eng.ledger.enable()
    return eng


def _c(engine, actor="ops", **kw):
    return Changeset(actor=actor, actor_type="human", **kw)


class TestUS10Lifecycle:
    def test_property_evolution_across_revisions(self, engine):
        cs = _c(engine)
        cs.create_node("pump-7", labels=["Equipment"], properties={"status": "ok"})
        h1 = engine.ledger.commit(cs).revision
        ids = [h1.revision_id]
        for val in ("warn", "fault", "ok"):
            cs = _c(engine)
            cs.set_property("pump-7", "status", val)
            ids.append(engine.ledger.commit(cs).revision.revision_id)
        expected = ["ok", "warn", "fault", "ok"]
        priors = [None, "ok", "warn", "fault"]
        for rid, exp, prior in zip(ids, expected, priors):
            rev = engine.ledger.get_revision(rid)
            rec = [r for r in rev.records if r.op == "set_prop" and r.attr == "status"][0]
            assert (rec.prior, rec.new) == (prior, exp)
            assert engine.ledger.reconstruct(rid).nodes["pump-7"].props["status"] == exp
        d = engine.ledger.diff(ids[0], ids[3]).to_list()
        assert not any(e["attr"] == "prop:status" for e in d)

    def test_qualifier_evolution_keeps_statement_identity(self, engine):
        cs = _c(engine)
        cs.create_node("pump-7")
        cs.create_node("tank-2")
        engine.ledger.commit(cs)
        cs = _c(engine)
        cs.create_relationship("pump-7", "FEEDS", "tank-2", qualifiers={"capacity": "100"})
        res2 = engine.ledger.commit(cs)
        stmt = res2.stmt_ids[0]
        ids = [res2.revision.revision_id]
        cs = _c(engine)
        cs.set_qualifier(stmt_id=stmt, key="capacity", value="120")
        ids.append(engine.ledger.commit(cs).revision.revision_id)
        cs = _c(engine)
        cs.set_qualifier(("pump-7", "FEEDS", "tank-2"), "verified", "true")
        ids.append(engine.ledger.commit(cs).revision.revision_id)
        cs = _c(engine)
        cs.remove_qualifier(stmt_id=stmt, key="verified")
        ids.append(engine.ledger.commit(cs).revision.revision_id)
        expected = [
            {"capacity": "100"},
            {"capacity": "120"},
            {"capacity": "120", "verified": "true"},
            {"capacity": "120"},
        ]
        for rid, exp in zip(ids, expected):
            st = engine.ledger.reconstruct(rid)
            assert st.statements[stmt].quals == exp
            rev = engine.ledger.get_revision(rid)
            assert all(rec.entity_id == stmt for rec in rev.records if rec.entity_kind == "rel")
        assert canonical_tables(engine.conn)["rels"][("pump-7", "FEEDS", "tank-2", None)][
            "quals"
        ] == {"capacity": "120"}
        assert (
            str(
                engine._iris_obj().classMethodValue(
                    "Graph.KG.Ledger", "StmtForTuple", "pump-7", "FEEDS", "tank-2", ""
                )
            )
            == stmt
        )

    def test_detach_delete_records_cascade_in_statement_order(self, engine):
        cs = _c(engine)
        cs.create_node("pump-7")
        cs.create_node("tank-2")
        cs.create_node("gen-1")
        engine.ledger.commit(cs)
        cs = _c(engine)
        cs.create_relationship("pump-7", "FEEDS", "tank-2")
        cs.create_relationship("gen-1", "POWERS", "pump-7")
        res = engine.ledger.commit(cs)
        h5 = res.revision.revision_id
        s1, s2 = res.stmt_ids[0], res.stmt_ids[1]
        cs = _c(engine)
        cs.delete_node("pump-7", mode="detach")
        res6 = engine.ledger.commit(cs)
        h6 = res6.revision.revision_id
        rev = engine.ledger.get_revision(h6)
        ops = [(r.op, r.entity_id, tuple(r.flags)) for r in rev.records]
        assert ops == [
            ("delete_rel", s1, ("cascade",)),
            ("delete_rel", s2, ("cascade",)),
            ("delete_node", "pump-7", ()),
        ]
        assert int(s1) < int(s2)
        before, after = engine.ledger.reconstruct(h5), engine.ledger.reconstruct(h6)
        assert "pump-7" in before.nodes and len(before.statements) == 2
        assert "pump-7" not in after.nodes and after.statements == {}
        tables = canonical_tables(engine.conn)
        assert "pump-7" not in tables["nodes"] and tables["rels"] == {}

    def test_strict_delete_rejected_with_incident_relationships(self, engine):
        cs = _c(engine)
        cs.create_node("pump-7")
        cs.create_node("tank-2")
        cs.create_relationship("pump-7", "FEEDS", "tank-2")
        engine.ledger.commit(cs)
        head = engine.ledger.head().revision_id
        cs = _c(engine)
        cs.delete_node("pump-7")  # strict default
        with pytest.raises(ChangesetOperationError) as ei:
            engine.ledger.commit(cs)
        assert "node_has_relationships" in ei.value.reason
        assert engine.ledger.head().revision_id == head
        assert "pump-7" in canonical_tables(engine.conn)["nodes"]

    def test_recreated_identifier_is_two_lifecycles(self, engine):
        cs = _c(engine)
        cs.create_node("pump-7", labels=["A"])
        h1 = engine.ledger.commit(cs).revision.revision_id
        cs = _c(engine)
        cs.delete_node("pump-7")
        h6 = engine.ledger.commit(cs).revision.revision_id
        cs = _c(engine)
        cs.create_node("pump-7", labels=["B"])
        h7 = engine.ledger.commit(cs).revision.revision_id
        creates = [
            r
            for rid in (h1, h7)
            for r in engine.ledger.get_revision(rid).records
            if r.op == "create_node"
        ]
        assert len(creates) == 2
        assert "pump-7" not in engine.ledger.reconstruct(h6).nodes
        assert engine.ledger.reconstruct(h7).nodes["pump-7"].labels == {"B"}
        d = engine.ledger.diff(h1, h7).to_list()
        existence = [e for e in d if e["entity_id"] == "pump-7" and e["attr"] == ""]
        assert len(existence) == 2
