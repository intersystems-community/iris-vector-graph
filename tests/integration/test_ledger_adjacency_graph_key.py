"""A ledger commit writes adjacency under the index key, not the ledger key.

Requires ivg-iris-enterprise:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/integration/test_ledger_adjacency_graph_key.py

There are three derivations and `Graph.KG.GraphKey` owns all of them (ADR-0003):
`ForIndex()` spells the default graph as the integer `0` for `^KG`, `ForLedger()`
spells it `$Char(1)` for `^IVG.Ledger("tuple", ...)`. `Graph.KG.LedgerApply` used
the ledger's derivation for both, so a committed relationship in the default graph
landed at `^KG("out", $Char(1), ...)` — a fifth subscript position no reader ever
walks. The SQL row was correct, the Cypher answer was correct, and the adjacency
the traversals read was in a graph that does not exist.

The two later writes had the mirror-image defect: `PersistQuals` and the
`delete_rel` branch hardcoded the key `0`, so a named graph's weight refresh and
its whole adjacency removal were applied to the default graph instead — leaving
the named graph's entries behind and corrupting a graph the changeset never named.

Source-level companion: none. The defect is which subscript a `Set` lands on, so
only the live global can show it.
"""

from __future__ import annotations

import json
import os

import pytest

from iris_vector_graph.ledger import Changeset
from tests.integration._ledger_helpers import make_engine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

GRAPH = "ledger_adj_acme"

# What ForLedger() returns for the default graph. Nothing may key ^KG with it.
LEDGER_DEFAULT_KEY = "\x01"


@pytest.fixture()
def engine(iris_connection, ledger_reset):
    eng = make_engine(iris_connection)
    eng.ledger.enable()
    return eng


def _kg(engine, *subs):
    v = engine._iris_obj().classMethodValue(
        "Graph.KG.Meta", "GetKG", *[str(s) for s in subs]
    )
    return "" if v is None else str(v)


def _commit_edge(engine, graph=None, weight="2.5"):
    # upsert, not create: the two-graph tests commit the same endpoints twice, and
    # the nodes are shared — they have no graph dimension of their own.
    cs = Changeset(actor="op", actor_type="human")
    cs.upsert_node("a")
    cs.upsert_node("b")
    cs.create_relationship("a", "R", "b", qualifiers={"weight": weight}, graph=graph)
    return engine.ledger.commit(cs)


# ---------------------------------------------------------------------------
# create_rel — where the adjacency lands
# ---------------------------------------------------------------------------


def test_a_default_graph_relationship_lands_on_the_index_key(engine):
    """`^KG("out", 0, ...)` is the only place a default-graph reader looks."""
    _commit_edge(engine)

    assert _kg(engine, "out", 0, "a", "R", "b") == "2.5", (
        "the committed relationship is not in the default graph's adjacency"
    )
    assert _kg(engine, "in", 0, "b", "R", "a") == "2.5"


def test_a_default_graph_relationship_never_lands_on_the_ledger_key(engine):
    """`$Char(1)` keys `^IVG.Ledger("tuple", ...)` and nothing in `^KG`."""
    _commit_edge(engine)

    assert _kg(engine, "out", LEDGER_DEFAULT_KEY, "a", "R", "b") == "", (
        "the ledger's default-graph sentinel reached ^KG, so the adjacency is in a "
        "graph no reader walks"
    )


def test_the_counters_land_in_the_same_graph_as_the_edge(engine):
    """A counter in one graph and its edge in another is drift by construction."""
    _commit_edge(engine)

    assert _kg(engine, "deg", 0, "a") != ""
    assert _kg(engine, "degp", 0, "a", "R") != ""
    assert _kg(engine, "deg", LEDGER_DEFAULT_KEY, "a") == ""


def test_a_named_graph_relationship_lands_under_its_own_graph(engine):
    _commit_edge(engine, graph=GRAPH)

    assert _kg(engine, "out", GRAPH, "a", "R", "b") == "2.5"
    assert _kg(engine, "out", 0, "a", "R", "b") == "", (
        "a named graph's relationship reached the default graph's adjacency"
    )


# ---------------------------------------------------------------------------
# set_qual / upsert_rel — the weight projection
# ---------------------------------------------------------------------------


def test_a_qualifier_change_refreshes_the_weight_in_the_default_graph(engine):
    _commit_edge(engine)

    cs = Changeset(actor="op", actor_type="human")
    cs.set_qualifier(("a", "R", "b"), "weight", "9.5")
    engine.ledger.commit(cs)

    assert _kg(engine, "out", 0, "a", "R", "b") == "9.5"
    assert _kg(engine, "in", 0, "b", "R", "a") == "9.5"


def test_a_qualifier_change_refreshes_the_weight_in_its_own_graph(engine):
    """The weight projection hardcoded the key 0, so a named graph never moved."""
    _commit_edge(engine, graph=GRAPH)

    cs = Changeset(actor="op", actor_type="human")
    cs.set_qualifier(("a", "R", "b", GRAPH), "weight", "9.5")
    engine.ledger.commit(cs)

    assert _kg(engine, "out", GRAPH, "a", "R", "b") == "9.5", (
        "the named graph's adjacency still carries the old weight, so traversals "
        "there disagree with the SQL row"
    )


def test_a_named_graph_qualifier_change_leaves_the_default_graph_alone(engine):
    """The same triple in both graphs. Only the one named may move."""
    _commit_edge(engine)
    _commit_edge(engine, graph=GRAPH, weight="2.5")

    cs = Changeset(actor="op", actor_type="human")
    cs.set_qualifier(("a", "R", "b", GRAPH), "weight", "9.5")
    engine.ledger.commit(cs)

    assert _kg(engine, "out", 0, "a", "R", "b") == "2.5", (
        "a changeset naming one graph rewrote the default graph's weight"
    )


# ---------------------------------------------------------------------------
# delete_rel — removing the adjacency it wrote
# ---------------------------------------------------------------------------


def test_deleting_a_default_graph_relationship_removes_its_adjacency(engine):
    _commit_edge(engine)

    cs = Changeset(actor="op", actor_type="human")
    cs.delete_relationship(("a", "R", "b"))
    engine.ledger.commit(cs)

    assert _kg(engine, "out", 0, "a", "R", "b") == ""
    assert _kg(engine, "in", 0, "b", "R", "a") == ""
    assert _kg(engine, "deg", 0, "a") == ""


def test_deleting_a_named_graph_relationship_removes_its_adjacency(engine):
    """The delete branch hardcoded 0, so the named graph's entry outlived the row."""
    _commit_edge(engine, graph=GRAPH)

    cs = Changeset(actor="op", actor_type="human")
    cs.delete_relationship(("a", "R", "b", GRAPH))
    engine.ledger.commit(cs)

    assert _kg(engine, "out", GRAPH, "a", "R", "b") == "", (
        "^KG(\"out\") still answers traversals for a relationship the ledger deleted"
    )
    assert _kg(engine, "in", GRAPH, "b", "R", "a") == ""
    assert _kg(engine, "deg", GRAPH, "a") == ""


def test_deleting_a_named_graph_relationship_leaves_the_default_graph_alone(engine):
    _commit_edge(engine)
    _commit_edge(engine, graph=GRAPH)

    cs = Changeset(actor="op", actor_type="human")
    cs.delete_relationship(("a", "R", "b", GRAPH))
    engine.ledger.commit(cs)

    assert _kg(engine, "out", 0, "a", "R", "b") == "2.5", (
        "deleting a named graph's relationship erased the default graph's adjacency"
    )
    assert _kg(engine, "deg", 0, "a") != ""


# ---------------------------------------------------------------------------
# The oracle
# ---------------------------------------------------------------------------


def test_the_oracle_finds_no_drift_after_a_default_graph_commit(engine):
    _commit_edge(engine)

    report = engine.verify_graph("")

    assert report["ok"] == 1, f"the commit left drift: {json.dumps(report['drift'])}"


def test_the_oracle_finds_no_drift_after_a_named_graph_commit(engine):
    _commit_edge(engine, graph=GRAPH)

    report = engine.verify_graph(GRAPH)

    assert report["ok"] == 1, f"the commit left drift: {json.dumps(report['drift'])}"
