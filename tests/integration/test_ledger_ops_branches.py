"""Branch coverage for Graph.KG.LedgerApply: property removal, qualifier operations, upserts.

Each case commits a changeset against the live ledger and then reads the governed
graph back from the canonical tables, so a branch that is skipped shows up as the wrong
stored state, and a rejected operation is asserted by its reason, not just by the exception.
"""

from __future__ import annotations

import os

import pytest

from iris_vector_graph.ledger import Changeset
from iris_vector_graph.ledger.errors import ChangesetOperationError
from tests.integration._ledger_helpers import canonical_tables, make_engine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def engine(iris_connection, ledger_reset):
    e = make_engine(iris_connection)
    e.ledger.enable()
    cs = Changeset(actor="t", actor_type="human")
    cs.create_node("p", labels=["Pump"], properties={"status": "ok", "site": "north"})
    cs.create_node("q")
    cs.create_relationship("p", "FEEDS", "q", qualifiers={"weight": "2", "note": "first"})
    e.ledger.commit(cs)
    return e


def commit(engine, build):
    cs = Changeset(actor="t", actor_type="human")
    build(cs)
    return engine.ledger.commit(cs)


def rejected(engine, build):
    with pytest.raises(ChangesetOperationError) as caught:
        commit(engine, build)
    return caught.value.reason


def quals(engine, conn):
    rels = canonical_tables(conn)["rels"]
    return rels[("p", "FEEDS", "q", None)]["quals"]


def props(engine, conn, node):
    return canonical_tables(conn)["nodes"][node]["props"]


# ------------------------------------------------------------- remove_property


def test_remove_property_deletes_it_and_leaves_the_others(engine, iris_connection):
    commit(engine, lambda cs: cs.remove_property("p", "site"))
    assert props(engine, iris_connection, "p") == {"status": "ok"}


def test_remove_property_rejections(engine):
    assert "node_not_found" in rejected(engine, lambda cs: cs.remove_property("ghost", "status"))
    assert "property_not_present" in rejected(engine, lambda cs: cs.remove_property("p", "nope"))

    def after_delete(cs):
        cs.delete_node("p", mode="detach")
        cs.remove_property("p", "status")

    assert "deleted earlier in this changeset" in rejected(engine, after_delete)


# ------------------------------------------------------------ qualifier operations


def test_replace_qualifiers_drops_what_the_new_set_leaves_out(engine, iris_connection):
    commit(engine, lambda cs: cs.replace_qualifiers(("p", "FEEDS", "q"), qualifiers={"weight": "9"}))
    assert quals(engine, iris_connection) == {"weight": "9"}


def test_replace_with_an_empty_set_clears_every_qualifier(engine, iris_connection):
    commit(engine, lambda cs: cs.replace_qualifiers(("p", "FEEDS", "q"), qualifiers={}))
    assert quals(engine, iris_connection) == {}


def test_set_and_remove_a_single_qualifier(engine, iris_connection):
    commit(engine, lambda cs: cs.set_qualifier(("p", "FEEDS", "q"), "unit", "kg"))
    assert quals(engine, iris_connection)["unit"] == "kg"
    commit(engine, lambda cs: cs.remove_qualifier(("p", "FEEDS", "q"), key="unit"))
    assert "unit" not in quals(engine, iris_connection)


def test_a_non_numeric_weight_is_rejected(engine, iris_connection):
    reason = rejected(
        engine, lambda cs: cs.replace_qualifiers(("p", "FEEDS", "q"), qualifiers={"weight": "heavy"})
    )
    assert "weight" in reason and "numeric" in reason
    assert quals(engine, iris_connection)["weight"] == "2"


def test_qualifier_operations_on_a_missing_relationship_are_rejected(engine):
    reason = rejected(engine, lambda cs: cs.set_qualifier(("p", "FEEDS", "nobody"), "k", "v"))
    assert reason


# --------------------------------------------------------------------- upserts


def test_upsert_relationship_creates_then_merges_qualifiers(engine, iris_connection):
    commit(engine, lambda cs: cs.upsert_relationship("p", "FEEDS", "q", qualifiers={"note": "second", "extra": "1"}))
    q = quals(engine, iris_connection)
    assert q["note"] == "second" and q["extra"] == "1" and q["weight"] == "2"


def test_upsert_of_a_new_relationship_assigns_a_statement(engine, iris_connection):
    res = commit(engine, lambda cs: cs.upsert_relationship("q", "DRAINS", "p"))
    assert len(res.stmt_ids) == 1
    assert ("q", "DRAINS", "p", None) in canonical_tables(iris_connection)["rels"]


def test_recreating_a_deleted_relationship_in_one_changeset_gets_a_new_statement(engine, iris_connection):
    def build(cs):
        cs.delete_relationship(("p", "FEEDS", "q"))
        cs.create_relationship("p", "FEEDS", "q", qualifiers={"weight": "7"})

    res = commit(engine, build)
    assert len(res.stmt_ids) == 1
    assert quals(engine, iris_connection) == {"weight": "7"}


def test_a_relationship_with_a_weight_updates_the_adjacency(engine):
    commit(engine, lambda cs: cs.create_relationship("q", "BACK", "p", qualifiers={"weight": "4"}))
    out = str(engine._iris_obj().classMethodValue("Graph.KG.Meta", "GetKG", "out", "0", "q", "BACK", "p"))
    assert out in ("4", "4.0")


# ---------------------------------------------------------------- reification cascade


def test_deleting_a_relationship_deletes_the_node_that_reifies_it(engine, iris_connection):
    cs = Changeset(actor="t", actor_type="human")
    cs.create_node("rf-statement")
    engine.ledger.commit(cs)
    cur = iris_connection.cursor()
    cur.execute("SELECT edge_id FROM Graph_KG.rdf_edges WHERE s = 'p' AND p = 'FEEDS' AND o_id = 'q'")
    edge_id = cur.fetchone()[0]
    cur.execute("INSERT INTO Graph_KG.rdf_reifications (reifier_id, edge_id) VALUES ('rf-statement', ?)", [edge_id])
    iris_connection.commit()

    commit(engine, lambda cs: cs.delete_relationship(("p", "FEEDS", "q")))

    state = canonical_tables(iris_connection)
    assert ("p", "FEEDS", "q", None) not in state["rels"]
    assert "rf-statement" not in state["nodes"], "the reifying node survived its relationship"
    cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_reifications WHERE reifier_id = 'rf-statement'")
    assert cur.fetchone()[0] == 0


def test_deleting_a_relationship_whose_reifier_row_has_no_node_just_drops_the_row(engine, iris_connection):
    cur = iris_connection.cursor()
    cur.execute("SELECT edge_id FROM Graph_KG.rdf_edges WHERE s = 'p' AND p = 'FEEDS' AND o_id = 'q'")
    edge_id = cur.fetchone()[0]
    cur.execute("INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES ('rf-other', '')")
    cur.execute("INSERT INTO Graph_KG.rdf_reifications (reifier_id, edge_id) VALUES ('rf-other', ?)", [edge_id])
    iris_connection.commit()
    commit(engine, lambda cs: cs.delete_relationship(("p", "FEEDS", "q")))
    cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_reifications WHERE reifier_id = 'rf-other'")
    assert cur.fetchone()[0] == 0


def test_expected_head_naming_no_revision_is_refused_and_writes_nothing(engine, iris_connection):
    from iris_vector_graph.ledger.errors import UnknownRevisionError

    before = canonical_tables(iris_connection)
    head = engine.ledger.head().revision_id
    cs = Changeset(actor="t", actor_type="human", expected_head="rev-that-never-existed")
    cs.create_node("zz")
    with pytest.raises(UnknownRevisionError):
        engine.ledger.commit(cs)
    assert engine.ledger.head().revision_id == head
    assert canonical_tables(iris_connection) == before
