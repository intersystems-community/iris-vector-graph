"""`ledger.verify()` right after `enable()` over relationships written before it (KNOWN_ISSUES, found 2026-10-22).

The genesis revision records each relationship already in the tables; the verifier then compares the replay with the
tables by (s, p, o, graph). The genesis wrote the default graph as "\\u0000" and the tables read back as None, so every
default-graph relationship written before enable was reported twice: as a ledger statement whose row is gone and as an
unrecorded row. Nothing was written after enable, so the answer must be "equal".
"""

import os

import pytest

from tests.integration._ledger_helpers import make_engine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def engine(iris_connection, ledger_reset):
    eng = make_engine(iris_connection)
    yield eng
    eng.ledger.disable()


@pytest.mark.parametrize("graph", [None, "pre_enable_g"])
def test_create_edge_before_enable_verifies_equal(engine, graph):
    engine.create_node("pe_a", labels=["PE"], properties={"k": "v"}, graph=graph)
    engine.create_node("pe_b", labels=["PE"], graph=graph)
    engine.create_edge("pe_a", "PE_R", "pe_b", qualifiers={"w": "1"}, graph=graph)
    engine.ledger.enable()
    report = engine.ledger.verify()
    assert report.result == "equal", report.differences


def test_bulk_create_edges_before_enable_verifies_equal(engine):
    for k in ("pe_c", "pe_d", "pe_e"):
        engine.create_node(k, labels=["PE"])
    engine.bulk_create_edges(
        [{"source_id": "pe_c", "predicate": "PE_R", "target_id": "pe_d"},
         {"source_id": "pe_d", "predicate": "PE_R", "target_id": "pe_e"}]
    )
    engine.ledger.enable()
    report = engine.ledger.verify()
    assert report.result == "equal", report.differences


def test_a_relationship_written_after_enable_without_the_ledger_is_still_unrecorded(engine):
    """The normalisation must not hide a real unrecorded write in the default graph."""
    engine.create_node("pe_f", labels=["PE"])
    engine.create_node("pe_g", labels=["PE"])
    engine.ledger.enable()
    cur = engine.conn.cursor()
    cur.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id, qualifiers) VALUES ('pe_f', 'PE_RAW', 'pe_g', '{}')")
    engine.conn.commit()
    cur.close()
    report = engine.ledger.verify()
    assert report.result == "diverges"
    rels = [e for e in report.differences if e.entity_kind == "rel" and e.attr == ""]
    assert len(rels) == 1 and rels[0].before is None and '"p":"PE_RAW"' in rels[0].after, rels
