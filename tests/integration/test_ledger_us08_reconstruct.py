"""Spec 213 US8 — reconstruct the structural graph at a revision (live ivg-iris-enterprise). T076."""

import os

import pytest

from iris_vector_graph.ledger import Changeset, ReconstructionTooLargeError
from tests.integration._ledger_helpers import canonical_tables, make_engine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def engine(iris_connection, ledger_reset):
    eng = make_engine(iris_connection)
    eng.ledger.enable()
    return eng


def _history(engine):
    ids = {"G": engine.ledger.head().revision_id}

    def commit(name, cs):
        res = engine.ledger.commit(cs)
        ids[name] = res.revision.revision_id
        return res

    cs = Changeset(actor="a", actor_type="human")
    cs.create_node("x", labels=["L"], properties={"k": "1"})
    cs.create_node("y")
    cs.create_node("z")
    commit("H1", cs)
    cs = Changeset(actor="a", actor_type="human")
    cs.create_relationship("x", "R", "y", qualifiers={"w": "1"})
    cs.create_relationship("y", "R", "z")
    commit("H2", cs)
    cs = Changeset(actor="a", actor_type="human")
    cs.set_property("x", "k", "2")
    commit("H3", cs)
    cs = Changeset(actor="a", actor_type="human")
    cs.set_property("x", "k", "3")
    cs.add_label("z", "Z")
    commit("H4", cs)
    cs = Changeset(actor="a", actor_type="human")
    cs.delete_node("y", mode="detach")
    commit("H5", cs)
    cs = Changeset(actor="a", actor_type="human")
    cs.set_property("z", "q", "1")
    commit("H6", cs)
    return ids


def _as_tables(state):
    return {
        "nodes": {
            nid: {"labels": set(n.labels), "props": dict(n.props)} for nid, n in state.nodes.items()
        },
        "rels": {st.tuple: {"quals": dict(st.quals)} for st in state.statements.values()},
    }


class TestUS8Reconstruct:
    def test_intermediate_revision(self, engine):
        ids = _history(engine)
        head_before = engine.ledger.head().revision_id
        st = engine.ledger.reconstruct(ids["H4"])
        t = _as_tables(st)
        assert t["nodes"]["x"] == {"labels": {"L"}, "props": {"k": "3"}}
        assert t["nodes"]["z"] == {"labels": {"Z"}, "props": {}}
        assert set(t["rels"]) == {("x", "R", "y", None), ("y", "R", "z", None)}
        assert t["rels"][("x", "R", "y", None)]["quals"] == {"w": "1"}
        assert engine.ledger.head().revision_id == head_before

    def test_genesis_and_head(self, engine):
        ids = _history(engine)
        g = engine.ledger.reconstruct(ids["G"])
        assert g.entity_count == 0  # empty graph at adoption
        head = engine.ledger.reconstruct(engine.ledger.head().revision_id)
        assert _as_tables(head) == canonical_tables(engine.conn)

    def test_deleted_node_present_before_absent_after(self, engine):
        ids = _history(engine)
        before = engine.ledger.reconstruct(ids["H4"])
        after = engine.ledger.reconstruct(ids["H5"])
        assert "y" in before.nodes and len(before.statements) == 2
        assert "y" not in after.nodes and after.statements == {}

    def test_bound_refuse_and_stream(self, iris_connection, ledger_reset):
        eng = make_engine(iris_connection)
        eng.ledger.enable(recon_bound=4)
        ids = _history(eng)
        with pytest.raises(ReconstructionTooLargeError) as ei:
            eng.ledger.reconstruct(ids["H2"])  # 3 nodes + 2 rels = 5 > 4
        assert ei.value.bound == 4
        items = list(eng.ledger.reconstruct(ids["H2"], stream=True))
        assert len(items) == 5

    def test_export_round_trip(self, engine, tmp_path):
        ids = _history(engine)
        expected = _as_tables(engine.ledger.reconstruct(ids["H4"]))
        path = str(tmp_path / "at-h4.ndjson")
        summary = engine.ledger.export_reconstruction(ids["H4"], path)
        assert (summary.nodes, summary.rels) == (3, 2)
        head_before = engine.ledger.head().revision_id
        # wipe the structural graph (leave the ledger) and import into the empty store
        cur = engine.conn.cursor()
        for t in ["rdf_edges", "rdf_labels", "rdf_props", "nodes"]:
            cur.execute(f"DELETE FROM Graph_KG.{t}")
        engine.conn.commit()
        cur.close()
        result = engine.import_graph_ndjson(path)
        assert result["nodes"] == 3 and result["edges"] == 2
        assert canonical_tables(engine.conn) == expected
        assert (
            engine.ledger.head().revision_id == head_before
        )  # import is an unrecorded write, not a revision
