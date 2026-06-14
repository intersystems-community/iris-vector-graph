"""
Unit tests for _engine/snapshot.py covering miss lines:
- load_networkx: progress_callback at 10k-step (lines 61-65, 87-91)
- save_snapshot: vector embedding table branch (lines 360-380), edge embeddings (388-396)
- restore_snapshot: merge=True path (lines 582-586), vector file (601-636), edge vector file (638-676)
- _import_global_from_ndjson (lines 757-768)
- _export_global_to_ndjson (lines 720-746)
- import_graph_ndjson: temporal_edge path (lines 838-859), flush batch (856-859)
- export_graph_ndjson (lines 870-900)

No live IRIS — mocks store and cursor.
"""
import io
import json
import zipfile
import pytest
from unittest.mock import MagicMock, patch, call
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult


def _make_eng(dim=4):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.return_value = None
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.description = [("node_id", None), ("val", None)]
    cursor.close.return_value = None
    return IRISGraphEngine(conn, embedding_dimension=dim), conn, cursor


# ---------------------------------------------------------------------------
# load_networkx: progress_callback at 10k step
# ---------------------------------------------------------------------------

class TestLoadNetworkxProgressCallback:

    def test_progress_callback_invoked(self):
        """progress_callback path (lines 61-65): triggered when n_done % 10000 == 0."""
        eng, conn, cursor = _make_eng()
        calls = []

        import networkx as nx
        G = nx.DiGraph()
        G.add_node("n1", namespace="gene")

        with patch.object(eng, "create_node", return_value=True):
            with patch.object(eng, "create_edge", return_value=True):
                result = eng.load_networkx(
                    G,
                    progress_callback=lambda n, e: calls.append((n, e)),
                )
        assert isinstance(result, dict)
        assert len(calls) >= 1


# ---------------------------------------------------------------------------
# save_snapshot: vector table + edge embeddings branch
# ---------------------------------------------------------------------------

class TestSaveSnapshot:

    def _make_cursor_with_tables(self, cursor):
        """Configure cursor to return data for all SQL tables + vector tables."""
        cursor.description = [("node_id", None)]
        cursor.fetchall.return_value = [("n1",)]

    def test_saves_zip_file(self, tmp_path):
        eng, conn, cursor = _make_eng()
        self._make_cursor_with_tables(cursor)

        with patch("iris_vector_graph.schema._call_classmethod", return_value="2025.1"):
            result = eng.save_snapshot(str(tmp_path / "snap.zip"))

        assert result["path"].endswith("snap.zip")
        assert "tables" in result

    def test_vector_table_branch_covered(self, tmp_path):
        """Lines 360-380: vector embeddings table present path."""
        eng, conn, cursor = _make_eng()

        def fetchall_side():
            return [("n1", "[1,2,3,4]", '{"source": "test"}')]

        call_count = [0]
        def execute_side(sql, params=None):
            call_count[0] += 1

        cursor.execute.side_effect = execute_side
        cursor.fetchall.side_effect = [
            [("n1",)],       # Graph_KG.nodes
            [],              # rdf_edges
            [],              # rdf_labels
            [],              # rdf_props
            [],              # rdf_reifications
            [("n1", "[1,2,3,4]", '{}')],  # kg_NodeEmbeddings
            [],              # kg_EdgeEmbeddings
        ]
        cursor.description = [("node_id", None)]

        with patch("iris_vector_graph.schema._call_classmethod", return_value="2025.1"):
            result = eng.save_snapshot(
                str(tmp_path / "snap_vec.zip"), layers=["sql"]
            )
        assert "tables" in result

    def test_globals_layer_covered(self, tmp_path):
        """Lines 398-444: globals export path."""
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = []
        cursor.description = [("node_id", None)]

        iris_obj = MagicMock()
        iris_obj.nextSubscript.return_value = None  # empty globals

        with patch("iris_vector_graph.schema._call_classmethod", return_value="2025.1"):
            with patch.object(eng, "_iris_obj", return_value=iris_obj):
                result = eng.save_snapshot(
                    str(tmp_path / "snap_globals.zip"), layers=["sql", "globals"]
                )
        assert "globals" in result


# ---------------------------------------------------------------------------
# restore_snapshot: merge=True, vector, edge-embedding restore
# ---------------------------------------------------------------------------

class TestRestoreSnapshot:

    def _make_zip(self, tmp_path, merge_data=None):
        """Create a minimal valid snapshot zip."""
        nodes_ndjson = '{"node_id": "n1"}\n'
        metadata = {
            "version": "1.1",
            "globals_format": "ndjson",
            "created_ts": 1000,
            "has_vector_sql": True,
            "tables": {"Graph_KG.nodes": 1},
            "globals": {},
            "layers": ["sql"],
        }
        zip_path = str(tmp_path / "snap.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("metadata.json", json.dumps(metadata))
            zf.writestr("sql/Graph_KG_nodes.ndjson", nodes_ndjson)
            if merge_data:
                for fname, content in merge_data.items():
                    zf.writestr(fname, content)
        return zip_path

    def test_restore_no_merge(self, tmp_path):
        eng, conn, cursor = _make_eng()
        zip_path = self._make_zip(tmp_path)

        with patch.object(eng, "_iris_obj", return_value=MagicMock()):
            result = eng.restore_snapshot(zip_path, merge=False)

        assert "restored_tables" in result

    def test_restore_merge_true_uses_upsert(self, tmp_path):
        """Lines 582-586: merge=True uses SELECT ... WHERE NOT EXISTS."""
        eng, conn, cursor = _make_eng()
        zip_path = self._make_zip(tmp_path)

        executed_sqls = []
        def capture_exec(sql, params=None):
            executed_sqls.append(sql)

        cursor.execute.side_effect = capture_exec

        with patch.object(eng, "_iris_obj", return_value=MagicMock()):
            result = eng.restore_snapshot(zip_path, merge=True)

        assert any("WHERE NOT EXISTS" in s for s in executed_sqls)

    def test_restore_with_vector_embeddings(self, tmp_path):
        """Lines 601-636: restore vector embedding rows."""
        vec_ndjson = json.dumps({"id": "n1", "emb": "[1,2,3,4]", "metadata": None}) + "\n"
        zip_path = str(tmp_path / "snap_vec.zip")
        metadata = {
            "version": "1.1",
            "globals_format": "ndjson",
            "created_ts": 1000,
            "has_vector_sql": True,
            "tables": {},
            "globals": {},
            "layers": ["sql"],
        }
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("metadata.json", json.dumps(metadata))
            zf.writestr("sql/Graph_KG_kg_NodeEmbeddings.ndjson", vec_ndjson)

        eng, conn, cursor = _make_eng()
        with patch.object(eng, "_iris_obj", return_value=MagicMock()):
            result = eng.restore_snapshot(zip_path, merge=False)

        assert "Graph_KG.kg_NodeEmbeddings" in result["restored_tables"]

    def test_restore_with_edge_embeddings(self, tmp_path):
        """Lines 638-676: restore edge embedding rows."""
        edge_ndjson = json.dumps({
            "s": "n1", "p": "TREATS", "o_id": "n2", "emb": "[1,2,3,4]"
        }) + "\n"
        zip_path = str(tmp_path / "snap_edge.zip")
        metadata = {
            "version": "1.1",
            "globals_format": "ndjson",
            "created_ts": 1000,
            "has_vector_sql": False,
            "tables": {},
            "globals": {},
            "layers": ["sql"],
        }
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("metadata.json", json.dumps(metadata))
            zf.writestr("sql/Graph_KG_kg_EdgeEmbeddings.ndjson", edge_ndjson)

        eng, conn, cursor = _make_eng()
        with patch.object(eng, "_iris_obj", return_value=MagicMock()):
            result = eng.restore_snapshot(zip_path, merge=False)

        assert "Graph_KG.kg_EdgeEmbeddings" in result["restored_tables"]


# ---------------------------------------------------------------------------
# _import_global_from_ndjson / _export_global_to_ndjson
# ---------------------------------------------------------------------------

class TestGlobalNdjson:

    def test_import_parses_lines(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        ndjson = (
            json.dumps({"k": ["out", "n1", "n2"], "v": "1.0"}) + "\n"
            + json.dumps({"k": ["in", "n2", "n1"], "v": "0.5"}) + "\n"
            + "bad json line\n"
            + "\n"
        )
        count = eng._import_global_from_ndjson(iris_obj, "^KG", ndjson)
        assert count == 2
        assert iris_obj.set.call_count == 2

    def test_export_iterates_subscripts(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        subscripts_seq = ["n1", None]
        call_seq = iter(subscripts_seq)

        def next_sub(*args, **kwargs):
            return next(call_seq, None)

        iris_obj.nextSubscript.side_effect = next_sub
        iris_obj.get.return_value = "1.0"

        lines = eng._export_global_to_ndjson(iris_obj, "^KG", ["out", 0])
        assert isinstance(lines, list)


# ---------------------------------------------------------------------------
# import_graph_ndjson: temporal_edge path + batch flush
# ---------------------------------------------------------------------------

class TestImportGraphNdjson:

    def test_temporal_edge_path(self, tmp_path):
        """Lines 838-858: temporal_edge records go to bulk_create_edges_temporal."""
        ndjson = (
            json.dumps({
                "kind": "temporal_edge",
                "source": "n1", "predicate": "TREATS", "target": "n2",
                "timestamp": 1000, "weight": 0.9,
                "attrs": {"conf": "0.9"},
                "source_labels": ["Gene"], "target_labels": ["Disease"],
            }) + "\n"
        )
        path = str(tmp_path / "events.ndjson")
        with open(path, "w") as f:
            f.write(ndjson)

        eng, conn, cursor = _make_eng()
        with patch.object(eng, "create_node", return_value=True):
            with patch.object(eng, "bulk_create_edges_temporal", return_value=1) as mock_bce:
                result = eng.import_graph_ndjson(path)

        mock_bce.assert_called()
        assert result["temporal_edges"] == 1

    def test_batch_flush_triggered(self, tmp_path):
        """Lines 856-859: batch flush when len(temporal_batch) >= batch_size."""
        lines = []
        for i in range(5):
            lines.append(json.dumps({
                "kind": "temporal_edge",
                "source": f"n{i}", "predicate": "T", "target": f"m{i}",
                "timestamp": i, "weight": 1.0,
            }))
        path = str(tmp_path / "batch.ndjson")
        with open(path, "w") as f:
            f.write("\n".join(lines))

        eng, conn, cursor = _make_eng()
        calls = []
        with patch.object(eng, "create_node", return_value=True):
            with patch.object(eng, "bulk_create_edges_temporal",
                              side_effect=lambda b: calls.append(len(b)) or len(b)):
                result = eng.import_graph_ndjson(path, batch_size=3)

        assert result["temporal_edges"] == 5
        assert len(calls) >= 1

    def test_node_and_edge_path(self, tmp_path):
        """node + edge + unknown kind paths."""
        ndjson = (
            json.dumps({"kind": "node", "id": "n1", "labels": ["Gene"], "properties": {}}) + "\n"
            + json.dumps({"kind": "edge", "source": "n1", "predicate": "T", "target": "n2"}) + "\n"
            + json.dumps({"kind": "unknown"}) + "\n"
        )
        path = str(tmp_path / "ne.ndjson")
        with open(path, "w") as f:
            f.write(ndjson)

        eng, conn, cursor = _make_eng()
        with patch.object(eng, "create_node", return_value=True):
            with patch.object(eng, "create_edge", return_value=True):
                result = eng.import_graph_ndjson(path)

        assert result["nodes"] == 1
        assert result["edges"] == 1


# ---------------------------------------------------------------------------
# export_graph_ndjson (lines 870-900)
# ---------------------------------------------------------------------------

class TestExportGraphNdjson:

    def test_writes_node_lines(self, tmp_path):
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = [("n1",), ("n2",)]
        with patch.object(eng, "get_node", side_effect=[
            {"id": "n1", "labels": ["Gene"]},
            {"id": "n2", "labels": ["Disease"]},
        ]):
            out_path = str(tmp_path / "out.ndjson")
            result = eng.export_graph_ndjson(out_path)

        assert result["nodes"] == 2

        with open(out_path) as f:
            lines = [json.loads(l) for l in f if l.strip()]

        node_lines = [l for l in lines if l.get("kind") == "node"]
        assert len(node_lines) == 2
        assert node_lines[0]["id"] == "n1"

    def test_get_node_none_skips_write(self, tmp_path):
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = [("n1",)]
        with patch.object(eng, "get_node", return_value=None):
            out_path = str(tmp_path / "empty.ndjson")
            result = eng.export_graph_ndjson(out_path)
        assert result["nodes"] == 0
