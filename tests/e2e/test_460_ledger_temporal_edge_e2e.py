"""4.6.0 R094: `ledger.verify()` right after `ledger.enable()` is `equal` when the graph holds a temporal edge.

A temporal write also asserts the plain relationship (ADR-0009/0015): an `rdf_edges` row whose qualifiers carry the
marker `{"temporal": true}`. That row is derived from the temporal store, like `^KG`, and is not a governed statement
(temporal writes make no revisions, spec 213 US11): verify's canonical read leaves it out. The genesis recorded it, so
the replay held `a-PING->b` and the tables, as verify reads them, did not: `diverges`, `unrecorded_writes`, one
difference. Genesis and verify must take the same rows.

Worked by hand: nodes a and b, one temporal edge a-PING->b. The governed graph is the two nodes and nothing else, so
the genesis is two create_node records, and verify finds no difference. A plain relationship written beside it is
governed: a-KNOWS->b makes three records, and still no difference.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.e2e]

TS = 1_700_000_000


@pytest.fixture
def engine(iris_connection, ledger_reset):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    e.create_node("a")
    e.create_node("b")
    assert e.create_edge_temporal("a", "PING", "b", TS)
    yield e
    e.ledger.disable()


def _ops(engine, revision):
    return sorted((r.op, r.entity_id if r.op == "create_node" else "") for r in
                  engine.ledger.get_revision(revision.revision_id).records)


def test_verify_after_enable_agrees_with_a_temporal_edge(engine):
    genesis = engine.ledger.enable()
    assert _ops(engine, genesis) == [("create_node", "a"), ("create_node", "b")]
    report = engine.ledger.verify()
    assert (report.result, report.classification, report.differences) == ("equal", None, [])


def test_a_plain_edge_beside_the_temporal_one_is_still_governed(engine):
    engine.create_edge("a", "KNOWS", "b")
    genesis = engine.ledger.enable()
    assert _ops(engine, genesis) == [("create_node", "a"), ("create_node", "b"), ("create_rel", "")]
    report = engine.ledger.verify()
    assert (report.result, report.differences) == ("equal", [])
