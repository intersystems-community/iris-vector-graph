"""4.6.0 R243: setup's ^KG/^NKG bootstrap reads the engine's IVG Schema, not ``Graph_KG``.

``GraphSchema.bootstrap_kg_global`` asked ``SELECT COUNT(*) FROM Graph_KG.rdf_edges`` whatever the engine's schema. In a
namespace whose IVG Schema is RAG there is no ``Graph_KG.rdf_edges``; the count failed, the bootstrap answered "nothing
to backfill", and ^KG stayed without the namespace's edges.

The fixture gives setup a RAG namespace (`IVG_NAMESPACE`, ns460alt.py) whose edges never reached ^KG: three nodes and
two edges r243:a -R243-> r243:b -R243-> r243:c are written, then their ^KG("out"/"in", 0, ...) entries and the
``kg_built``/``nkg_built`` markers are killed (^NKG is left alone). A new engine's ``initialize_schema()`` then has to
bootstrap. By hand: ^KG("out", 0, "r243:a", "R243", "r243:b") and ("out", 0, "r243:b", "R243", "r243:c") are defined
again, the status says ``kg_built``, and ``MATCH (a)-[:R243*1..2]->(b) WHERE id(a) = 'r243:a'`` answers r243:b and
r243:c.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

NODES = ("r243:a", "r243:b", "r243:c")
EDGES = (("r243:a", "r243:b"), ("r243:b", "r243:c"))


def _drop_test_nodes(engine) -> None:
    for nid in NODES:
        engine.delete_node(nid, graph=None)


@pytest.fixture(scope="module")
def bootstrapped():
    import iris

    alt.make_namespace()
    conn = alt.connect()
    first = alt.engine(conn, native_cypher=False)
    first.initialize_schema()
    _drop_test_nodes(first)
    for nid in NODES:
        assert first.create_node(nid) is True
    for s, o in EDGES:
        assert first.create_edge(s, "R243", o) is True
    conn.commit()
    nat = iris.createIRIS(conn)
    for s, o in EDGES:
        nat.kill("^KG", "out", 0, s)
        nat.kill("^KG", "in", 0, o)
    nat.kill("^Graph.KG.Meta", "kg_built")
    nat.kill("^Graph.KG.Meta", "nkg_built")
    before = {
        "out": [int(nat.isDefined("^KG", "out", 0, s, "R243", o) or 0) for s, o in EDGES],
        "rows": alt.rows(conn, "SELECT COUNT(*) FROM RAG.rdf_edges WHERE p = 'R243'")[0][0],
    }
    engine = alt.engine(conn, native_cypher=False)
    status = engine.initialize_schema()
    yield {"engine": engine, "conn": conn, "nat": nat, "before": before, "status": status}
    _drop_test_nodes(engine)
    conn.commit()
    conn.close()


def test_the_fixture_left_rag_edges_outside_kg(bootstrapped):
    assert bootstrapped["before"] == {"out": [0, 0], "rows": 2}


def test_setup_bootstraps_kg_from_the_rag_edges(bootstrapped):
    nat = bootstrapped["nat"]
    assert bootstrapped["status"]["kg_built"] is True, bootstrapped["status"]["warnings"]
    for s, o in EDGES:
        assert int(nat.isDefined("^KG", "out", 0, s, "R243", o)) in (1, 11), (s, o)
        assert int(nat.isDefined("^KG", "in", 0, o, "R243", s)) in (1, 11), (s, o)


def test_a_variable_length_read_finds_the_bootstrapped_edges(bootstrapped):
    rows = bootstrapped["engine"].execute_cypher(
        "MATCH (a)-[:R243*1..2]->(b) WHERE id(a) = 'r243:a' RETURN id(b) ORDER BY id(b)", raise_on_error=True).rows
    assert rows == [["r243:b"], ["r243:c"]]


# The bulk loaders' index helpers. Before R243, 600 nodes through bulk_create_nodes(disable_indexes=True) dropped
# RAG's seven bulk indexes by bare name and recreated them on Graph_KG.*, which this namespace does not have: the
# query below listed seven before the load and none after (2026.3).
BULK_INDEXES = (
    "SELECT DISTINCT %EXACT(TABLE_NAME), %EXACT(INDEX_NAME) FROM INFORMATION_SCHEMA.INDEXES WHERE TABLE_SCHEMA = 'RAG' "
    "AND INDEX_NAME IN ('idx_labels_s', 'idx_labels_label', 'idx_labels_s_label', 'idx_props_s', 'idx_props_key', "
    "'idx_props_s_key', 'idx_props_key_valkey')")
EXPECTED_INDEXES = sorted([
    ("rdf_labels", "idx_labels_label"), ("rdf_labels", "idx_labels_s"), ("rdf_labels", "idx_labels_s_label"),
    ("rdf_props", "idx_props_key"), ("rdf_props", "idx_props_key_valkey"), ("rdf_props", "idx_props_s"),
    ("rdf_props", "idx_props_s_key")])
BULK = [f"r243:bulk{i}" for i in range(600)]


def test_a_bulk_load_keeps_the_schemas_indexes(bootstrapped):
    # A new engine, as a loading process has: it has not run initialize_schema(), so its capabilities are unknown
    # and bulk_create_nodes takes the SQL path, the one that disables and rebuilds the indexes.
    conn = bootstrapped["conn"]
    engine = alt.engine(conn, native_cypher=False)
    assert sorted(alt.rows(conn, BULK_INDEXES)) == EXPECTED_INDEXES
    try:
        created = engine.bulk_create_nodes(
            [{"id": nid, "labels": ["R243Bulk"], "properties": {"n": nid[9:]}} for nid in BULK], disable_indexes=True)
        assert len(created) == 600
        assert sorted(alt.rows(conn, BULK_INDEXES)) == EXPECTED_INDEXES
        # the rows the rebuilt indexes serve: 600 labels, and one node's property read back by its key
        assert alt.rows(conn, "SELECT COUNT(*) FROM RAG.rdf_labels WHERE label = 'R243Bulk'") == [(600,)]
        assert alt.rows(conn, "SELECT val FROM RAG.rdf_props WHERE s = 'r243:bulk417' AND \"key\" = 'n'") == [("417",)]
    finally:
        for nid in BULK:
            engine.delete_node(nid, graph=None)
        conn.commit()


def test_a_bulk_load_session_keeps_the_schemas_indexes(bootstrapped):
    engine, conn = bootstrapped["engine"], bootstrapped["conn"]
    assert sorted(alt.rows(conn, BULK_INDEXES)) == EXPECTED_INDEXES
    try:
        with engine.bulk_load_session(rebuild_indexes=True, incremental=False) as session:
            session.add_nodes([{"id": nid, "labels": ["R243Bulk"]} for nid in BULK[:20]])
        assert sorted(alt.rows(conn, BULK_INDEXES)) == EXPECTED_INDEXES
        assert alt.rows(conn, "SELECT COUNT(*) FROM RAG.rdf_labels WHERE label = 'R243Bulk'") == [(20,)]
    finally:
        for nid in BULK[:20]:
            engine.delete_node(nid, graph=None)
        conn.commit()
