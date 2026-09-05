"""Spec 213 US2 — rollback after a failed operation (live ivg-iris-enterprise).

T036: scenarios 1–3 plus the C2 edge cases (too large, duplicate ops, create-then-delete,
reference after in-changeset delete, reserved property before apply).
"""

import os

import pytest

from iris_vector_graph.ledger import (
    Changeset,
    ChangesetOperationError,
    ChangesetTooLargeError,
)
from tests.integration._ledger_helpers import canonical_tables, make_engine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def engine(iris_connection, ledger_reset):
    eng = make_engine(iris_connection)
    eng.ledger.enable()
    return eng


def _kg_out(engine, s, p, o):
    return str(engine._iris_obj().classMethodValue("Graph.KG.Meta", "GetKG", "out", "0", s, p, o))


def _history_len(engine):
    cur = engine.conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM Graph_KG.ledger_revisions")
        return cur.fetchone()[0]
    finally:
        cur.close()


def _snapshot(engine, rel):
    return {
        "head": engine.ledger.head().revision_id,
        "hist": _history_len(engine),
        "tables": canonical_tables(engine.conn),
        "adj": _kg_out(engine, *rel),
        "dirty": engine._nkg_dirty,
        "stmt_next": str(engine._iris_obj().get("^IVG.Ledger", "stmtNext")),
    }


def _seed(engine):
    cs = Changeset(actor="seed", actor_type="system")
    cs.create_node("a", labels=["L"], properties={"k": "v"})
    cs.create_node("b")
    cs.create_relationship("a", "R", "b", qualifiers={"weight": "1.0"})
    engine.ledger.commit(cs)


class TestUS2Rollback:
    def test_failing_op_mid_changeset_leaves_everything_unchanged(self, engine):
        _seed(engine)
        before = _snapshot(engine, ("a", "R", "b"))
        cs = Changeset(actor="op", actor_type="human")
        cs.create_node("c")  # 0
        cs.add_label("a", "New")  # 1
        cs.set_property("a", "k", "changed")  # 2
        cs.create_relationship("c", "R", "a")  # 3
        cs.create_relationship("a", "R", "does-not-exist")  # 4 ← fails (endpoint missing)
        cs.create_node("d")  # 5
        cs.delete_relationship(("a", "R", "b"))  # 6
        cs.remove_property("a", "k")  # 7
        with pytest.raises(ChangesetOperationError) as ei:
            engine.ledger.commit(cs)
        assert ei.value.op_index == 4
        assert "does-not-exist" in ei.value.reason
        assert _snapshot(engine, ("a", "R", "b")) == before

    def test_uniqueness_violation_rejects_whole_changeset(self, engine):
        _seed(engine)
        before = _snapshot(engine, ("a", "R", "b"))
        cs = Changeset(actor="op", actor_type="human")
        cs.create_node("x")  # 0
        cs.create_relationship("a", "R", "b")  # 1 ← already live
        with pytest.raises(ChangesetOperationError) as ei:
            engine.ledger.commit(cs)
        assert ei.value.op_index == 1 and "relationship_exists" in ei.value.reason
        assert _snapshot(engine, ("a", "R", "b")) == before

    def test_reserved_property_rejected_before_any_apply(self, engine):
        _seed(engine)
        before = _snapshot(engine, ("a", "R", "b"))
        cs = Changeset(actor="op", actor_type="human")
        cs.create_node("y")
        # bypass the client-side check to prove the server rejects too
        cs.ops.append({"op": "set_prop", "id": "a", "key": "__graph", "value": "g"})
        with pytest.raises(ChangesetOperationError) as ei:
            engine.ledger.commit(cs)
        assert ei.value.op_index == 1 and "reserved_property" in ei.value.reason
        assert _snapshot(engine, ("a", "R", "b")) == before

    def test_staleness_signal_unchanged_after_failure(self, engine):
        _seed(engine)
        engine._nkg_dirty = False
        cs = Changeset(actor="op", actor_type="human")
        cs.create_relationship("a", "R", "nope")
        with pytest.raises(ChangesetOperationError):
            engine.ledger.commit(cs)
        assert engine._nkg_dirty is False


class TestUS2EdgeCases:
    def test_too_large_rejected_before_apply(self, iris_connection, ledger_reset):
        eng = make_engine(iris_connection)
        eng.ledger.enable(max_ops=5)
        before = _history_len(eng)
        cs = Changeset(actor="op", actor_type="human")
        for i in range(6):
            cs.create_node(f"big{i}")
        with pytest.raises(ChangesetTooLargeError) as ei:
            eng.ledger.commit(cs)
        assert ei.value.limit == 5
        assert _history_len(eng) == before
        assert "big0" not in canonical_tables(eng.conn)["nodes"]

    def test_duplicate_set_property_records_both_and_last_wins(self, engine):
        cs = Changeset(actor="op", actor_type="human")
        cs.create_node("n")
        cs.set_property("n", "k", "1")
        cs.set_property("n", "k", "2")
        res = engine.ledger.commit(cs)
        rev = engine.ledger.get_revision(res.revision.revision_id)
        sets = [r for r in rev.records if r.op == "set_prop" and r.attr == "k"]
        assert [(r.prior, r.new) for r in sets] == [(None, "1"), ("1", "2")]
        assert canonical_tables(engine.conn)["nodes"]["n"]["props"] == {"k": "2"}

    def test_create_then_delete_in_one_changeset(self, engine):
        cs = Changeset(actor="op", actor_type="human")
        cs.create_node("p")
        cs.create_node("q")
        r = cs.create_relationship("p", "R", "q")
        cs.delete_relationship(r)
        cs.delete_node("p")
        res = engine.ledger.commit(cs)
        tables = canonical_tables(engine.conn)
        assert "p" not in tables["nodes"] and ("p", "R", "q", None) not in tables["rels"]
        assert "q" in tables["nodes"]
        # statement id consumed and never reused
        consumed = res.stmt_ids[2]
        cs2 = Changeset(actor="op", actor_type="human")
        cs2.create_node("p")
        cs2.create_relationship("p", "R", "q")
        res2 = engine.ledger.commit(cs2)
        assert res2.stmt_ids[1] != consumed
        assert int(res2.stmt_ids[1]) > int(consumed)

    def test_reference_after_in_changeset_delete_fails_at_that_index(self, engine):
        _seed(engine)
        cs = Changeset(actor="op", actor_type="human")
        cs.delete_relationship(("a", "R", "b"))  # 0
        cs.delete_node("a")  # 1 (a still has no other rels → ok)
        cs.set_property("a", "k", "late")  # 2 ← a deleted earlier in this changeset
        with pytest.raises(ChangesetOperationError) as ei:
            engine.ledger.commit(cs)
        assert ei.value.op_index == 2
        # nothing applied
        assert ("a", "R", "b", None) in canonical_tables(engine.conn)["rels"]
