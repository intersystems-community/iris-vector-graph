"""Spec 214 Phase E — import_graph_ndjson with graph= param (T057, US1, live container)."""

import json
import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def eng(iris_connection, iris_master_cleanup, node_graph_reset):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(iris_connection, embedding_dimension=4)


class TestImportNdjsonGraph214:
    def test_import_with_graph_creates_named_graph_nodes(self, eng, iris_connection, tmp_path):
        ndjson = tmp_path / "test.ndjson"
        ndjson.write_text(
            '{"type":"node","id":"pump-1","labels":["Equipment"],"props":{"status":"ok"}}\n'
            '{"type":"node","id":"tank-1","labels":["Vessel"],"props":{}}\n'
            '{"type":"rel","s":"pump-1","p":"FEEDS","o":"tank-1","graph":null,"quals":{"weight":"1.0"},"stmt_id":"42"}\n'
        )
        result = eng.import_graph_ndjson(str(ndjson), graph="snapshot:H1")
        assert result["nodes"] == 2 and result["edges"] == 1

        cur = iris_connection.cursor()
        try:
            cur.execute("SELECT graph_id FROM Graph_KG.nodes WHERE node_id = 'pump-1'")
            graphs = [tuple(r)[0] for r in cur.fetchall()]
            cur.execute("SELECT graph_id FROM Graph_KG.rdf_edges WHERE s='pump-1' AND p='FEEDS'")
            edge_graphs = [tuple(r)[0] for r in cur.fetchall()]
        finally:
            cur.close()
        assert "snapshot:H1" in graphs, f"Expected graph_id='snapshot:H1', got {graphs}"
        assert "snapshot:H1" in edge_graphs, f"Edge missing graph_id, got {edge_graphs}"

    def test_import_no_graph_creates_default_nodes(self, eng, iris_connection, tmp_path):
        ndjson = tmp_path / "default.ndjson"
        ndjson.write_text('{"kind":"node","id":"def-node","labels":["L"],"properties":{"k":"v"}}\n')
        eng.import_graph_ndjson(str(ndjson))
        cur = iris_connection.cursor()
        try:
            cur.execute("SELECT graph_id FROM Graph_KG.nodes WHERE node_id = 'def-node'")
            graphs = [tuple(r)[0] for r in cur.fetchall()]
        finally:
            cur.close()
        assert "" in graphs, f"Expected default graph, got {graphs}"

    def test_drop_graph_removes_imported_snapshot(self, eng, iris_connection, tmp_path):
        ndjson = tmp_path / "snap.ndjson"
        ndjson.write_text('{"type":"node","id":"snap-node","labels":[],"props":{}}\n')
        eng.import_graph_ndjson(str(ndjson), graph="snap:1")
        count = eng.drop_graph("snap:1")
        assert count >= 1
        cur = iris_connection.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE graph_id='snap:1'")
            remaining = cur.fetchone()[0]
        finally:
            cur.close()
        assert remaining == 0

    def test_ledger_head_unchanged_after_import(self, eng, iris_connection, tmp_path):
        eng.ledger.enable()
        head = eng.ledger.head().revision_id
        ndjson = tmp_path / "imp.ndjson"
        ndjson.write_text('{"type":"node","id":"imp-1","labels":[],"props":{}}\n')
        eng.import_graph_ndjson(str(ndjson), graph="snap:2")
        assert (
            eng.ledger.head().revision_id == head
        ), "import_graph_ndjson must not create a revision"
