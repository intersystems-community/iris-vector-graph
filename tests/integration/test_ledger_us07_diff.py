"""Spec 213 US7 — deterministic diff between two revisions (live ivg-iris-enterprise). T070."""

import os

import pytest

from iris_vector_graph.ledger import Changeset
from tests.integration._ledger_helpers import make_engine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def engine(iris_connection, ledger_reset):
    eng = make_engine(iris_connection)
    eng.ledger.enable()
    return eng


def _history(engine):
    """Build a known history; returns dict name → revision_id and the stmt ids used."""
    ids = {"G": engine.ledger.head().revision_id}
    st = {}

    def commit(name, cs):
        res = engine.ledger.commit(cs)
        ids[name] = res.revision.revision_id
        return res

    cs = Changeset(actor="a", actor_type="human")
    cs.create_node("x", labels=["L"], properties={"k": "1"})
    cs.create_node("y")
    commit("H1", cs)
    cs = Changeset(actor="a", actor_type="human")
    cs.create_relationship("x", "R", "y", qualifiers={"w": "1"})
    st["r1"] = commit("H2", cs).stmt_ids[0]
    cs = Changeset(actor="a", actor_type="human")
    cs.set_property("x", "k", "2")
    cs.add_label("y", "M")
    commit("H3", cs)
    cs = Changeset(actor="a", actor_type="human")
    cs.delete_relationship(stmt_id=st["r1"])
    commit("H4", cs)
    cs = Changeset(actor="a", actor_type="human")
    cs.set_property("x", "k", "3")
    commit("H5", cs)
    cs = Changeset(actor="a", actor_type="human")
    cs.create_relationship("x", "R", "y", qualifiers={"w": "9"})
    st["r2"] = commit("H6", cs).stmt_ids[0]
    cs = Changeset(actor="a", actor_type="human")
    cs.set_property("x", "k", "1")
    cs.remove_label("y", "M")
    commit("H7", cs)
    return ids, st


class TestUS7Diff:
    def test_diff_is_deterministic_net_and_ordered(self, engine):
        ids, st = _history(engine)
        d1 = engine.ledger.diff(ids["H2"], ids["H7"])
        d2 = engine.ledger.diff(ids["H2"], ids["H7"])
        assert d1 == d2
        entries = d1.to_list()
        # x.k went 1→2→3→1 across the range: net zero
        assert not any(e["entity_id"] == "x" and e["attr"] == "prop:k" for e in entries)
        # y label M added at H3 and removed at H7: net zero
        assert not any(e["entity_id"] == "y" and e["attr"] == "label:M" for e in entries)
        keys = [(e["entity_kind"], e["entity_id"], e["attr"]) for e in entries]
        assert keys == sorted(keys, key=lambda k: (0 if k[0] == "node" else 1, k[1], k[2]))

    def test_reverse_diff_is_exact_inverse(self, engine):
        ids, _ = _history(engine)
        fwd = engine.ledger.diff(ids["H1"], ids["H6"])
        rev = engine.ledger.diff(ids["H6"], ids["H1"])
        assert rev == fwd.inverse()
        assert rev.inverse() == fwd

    def test_relationship_delete_and_recreate_shows_two_statements(self, engine):
        ids, st = _history(engine)
        d = engine.ledger.diff(ids["H3"], ids["H7"]).to_list()
        deletion = [
            e
            for e in d
            if e["entity_kind"] == "rel" and e["entity_id"] == st["r1"] and e["attr"] == ""
        ]
        creation = [
            e
            for e in d
            if e["entity_kind"] == "rel" and e["entity_id"] == st["r2"] and e["attr"] == ""
        ]
        assert len(deletion) == 1 and deletion[0]["after"] is None
        assert len(creation) == 1 and creation[0]["before"] is None
        assert st["r1"] != st["r2"]

    def test_adjacent_diff_equals_net_of_records(self, engine):
        ids, _ = _history(engine)
        d = engine.ledger.diff(ids["H2"], ids["H3"]).to_list()
        assert {(e["entity_id"], e["attr"], e["before"], e["after"]) for e in d} == {
            ("x", "prop:k", "1", "2"),
            ("y", "label:M", None, ""),
        }

    def test_same_revision_is_empty(self, engine):
        ids, _ = _history(engine)
        for k in ("G", "H1", "H7"):
            assert engine.ledger.diff(ids[k], ids[k]).to_list() == []

    def test_node_delete_and_recreate_is_lifecycle_aware(self, engine):
        ids, _ = _history(engine)
        cs = Changeset(actor="a", actor_type="human")
        cs.delete_node("y", mode="detach")
        engine.ledger.commit(cs)
        cs = Changeset(actor="a", actor_type="human")
        cs.create_node("y")
        h9 = engine.ledger.commit(cs).revision.revision_id
        d = engine.ledger.diff(ids["H7"], h9).to_list()
        existence = [
            e for e in d if e["entity_kind"] == "node" and e["entity_id"] == "y" and e["attr"] == ""
        ]
        assert len(existence) == 2
        assert {e["after"] is None for e in existence} == {True, False}
