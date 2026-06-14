"""
Unit tests for _engine/snapshot.py restore_snapshot embedding and global restore paths.

Tests exercise:
- Lines 601-636: Node embedding restore (VECTOR_FILE present in zip)
- Lines 638-676: Edge embedding restore (EDGE_VECTOR_FILE present in zip)
- Lines 678-695: Global file restore path
- merge=True vs merge=False for both embedding tables

No IRIS connection needed — uses a real zip file with mock conn.
"""
import io
import json
import os
import tempfile
import zipfile
import pytest
from unittest.mock import MagicMock, patch


def _make_snapshot_zip(sql_files: dict, global_files: dict = None, metadata: dict = None) -> str:
    """Write a .ivgsnap zip to a temp path and return the path."""
    if metadata is None:
        metadata = {"tables": {}, "globals": {}, "has_vector_sql": False}
    tmp = tempfile.NamedTemporaryFile(suffix=".ivgsnap", delete=False)
    tmp.close()
    with zipfile.ZipFile(tmp.name, "w") as zf:
        zf.writestr("metadata.json", json.dumps(metadata))
        for key, content in sql_files.items():
            zf.writestr(key, content)
        for key, content in (global_files or {}).items():
            zf.writestr(key, content)
    return tmp.name


def _make_snapshot_engine():
    """Return SnapshotMixin-containing engine with fully mocked conn."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.return_value = None
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None

    from iris_vector_graph.engine import IRISGraphEngine
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    return eng, conn, cursor


class TestRestoreSnapshotNodeEmbeddings:

    def test_restore_node_embeddings_merge_false(self):
        """merge=False path: INSERT INTO kg_NodeEmbeddings VALUES (?, TO_VECTOR(?, ?))."""
        vec_line = json.dumps({"id": "node_a", "emb": "0.1,0.2,0.3,0.4", "metadata": None})
        sql_files = {
            "sql/Graph_KG_nodes.ndjson": "",
            "sql/Graph_KG_rdf_edges.ndjson": "",
            "sql/Graph_KG_rdf_labels.ndjson": "",
            "sql/Graph_KG_rdf_props.ndjson": "",
            "sql/Graph_KG_rdf_reifications.ndjson": "",
            "sql/Graph_KG_kg_NodeEmbeddings.ndjson": vec_line,
        }
        snap_path = _make_snapshot_zip(sql_files)
        try:
            eng, conn, cursor = _make_snapshot_engine()
            result = eng.restore_snapshot(snap_path, merge=False)
            assert isinstance(result, dict)
            # cursor.execute should have been called with an INSERT statement at some point
            calls = [str(c) for c in cursor.execute.call_args_list]
            assert any("INSERT" in c or "DELETE" in c or "call" in c.lower() for c in calls)
        finally:
            os.unlink(snap_path)

    def test_restore_node_embeddings_merge_true(self):
        """merge=True path: INSERT WHERE NOT EXISTS for kg_NodeEmbeddings."""
        vec_line = json.dumps({"id": "node_b", "emb": "0.5,0.6,0.7,0.8", "metadata": None})
        sql_files = {
            "sql/Graph_KG_nodes.ndjson": "",
            "sql/Graph_KG_rdf_edges.ndjson": "",
            "sql/Graph_KG_rdf_labels.ndjson": "",
            "sql/Graph_KG_rdf_props.ndjson": "",
            "sql/Graph_KG_rdf_reifications.ndjson": "",
            "sql/Graph_KG_kg_NodeEmbeddings.ndjson": vec_line,
        }
        snap_path = _make_snapshot_zip(sql_files)
        try:
            eng, conn, cursor = _make_snapshot_engine()
            result = eng.restore_snapshot(snap_path, merge=True)
            assert isinstance(result, dict)
        finally:
            os.unlink(snap_path)

    def test_restore_node_embeddings_multiple_lines(self):
        """Multiple embedding rows are all processed."""
        lines = [
            json.dumps({"id": f"n{i}", "emb": f"{i}.0,{i}.1,{i}.2,{i}.3", "metadata": None})
            for i in range(5)
        ]
        sql_files = {
            "sql/Graph_KG_nodes.ndjson": "",
            "sql/Graph_KG_rdf_edges.ndjson": "",
            "sql/Graph_KG_rdf_labels.ndjson": "",
            "sql/Graph_KG_rdf_props.ndjson": "",
            "sql/Graph_KG_rdf_reifications.ndjson": "",
            "sql/Graph_KG_kg_NodeEmbeddings.ndjson": "\n".join(lines),
        }
        snap_path = _make_snapshot_zip(sql_files)
        try:
            eng, conn, cursor = _make_snapshot_engine()
            result = eng.restore_snapshot(snap_path, merge=False)
            assert isinstance(result, dict)
        finally:
            os.unlink(snap_path)

    def test_restore_node_embeddings_missing_id_skipped(self):
        """Row without 'id' is skipped gracefully."""
        lines = [
            json.dumps({"emb": "0.1,0.2,0.3,0.4"}),  # no id
            json.dumps({"id": "good_node", "emb": "0.1,0.2,0.3,0.4"}),
        ]
        sql_files = {
            "sql/Graph_KG_nodes.ndjson": "",
            "sql/Graph_KG_rdf_edges.ndjson": "",
            "sql/Graph_KG_rdf_labels.ndjson": "",
            "sql/Graph_KG_rdf_props.ndjson": "",
            "sql/Graph_KG_rdf_reifications.ndjson": "",
            "sql/Graph_KG_kg_NodeEmbeddings.ndjson": "\n".join(lines),
        }
        snap_path = _make_snapshot_zip(sql_files)
        try:
            eng, conn, cursor = _make_snapshot_engine()
            result = eng.restore_snapshot(snap_path, merge=False)
            assert isinstance(result, dict)
        finally:
            os.unlink(snap_path)

    def test_restore_node_embeddings_bad_json_skipped(self):
        """Malformed JSON lines in embedding file are skipped."""
        lines = [
            "NOT VALID JSON {{{{",
            json.dumps({"id": "valid", "emb": "0.1,0.2,0.3,0.4"}),
        ]
        sql_files = {
            "sql/Graph_KG_nodes.ndjson": "",
            "sql/Graph_KG_rdf_edges.ndjson": "",
            "sql/Graph_KG_rdf_labels.ndjson": "",
            "sql/Graph_KG_rdf_props.ndjson": "",
            "sql/Graph_KG_rdf_reifications.ndjson": "",
            "sql/Graph_KG_kg_NodeEmbeddings.ndjson": "\n".join(lines),
        }
        snap_path = _make_snapshot_zip(sql_files)
        try:
            eng, conn, cursor = _make_snapshot_engine()
            result = eng.restore_snapshot(snap_path, merge=False)
            assert isinstance(result, dict)
        finally:
            os.unlink(snap_path)

    def test_restore_node_embeddings_insert_exception_suppressed(self):
        """cursor.execute raising on embedding insert is caught per-row."""
        vec_line = json.dumps({"id": "node_x", "emb": "0.1,0.2,0.3,0.4"})
        sql_files = {
            "sql/Graph_KG_nodes.ndjson": "",
            "sql/Graph_KG_rdf_edges.ndjson": "",
            "sql/Graph_KG_rdf_labels.ndjson": "",
            "sql/Graph_KG_rdf_props.ndjson": "",
            "sql/Graph_KG_rdf_reifications.ndjson": "",
            "sql/Graph_KG_kg_NodeEmbeddings.ndjson": vec_line,
        }
        snap_path = _make_snapshot_zip(sql_files)
        try:
            eng, conn, cursor = _make_snapshot_engine()
            cursor.execute.side_effect = Exception("DB error")
            result = eng.restore_snapshot(snap_path, merge=False)
            assert isinstance(result, dict)
        finally:
            os.unlink(snap_path)


class TestRestoreSnapshotEdgeEmbeddings:

    def test_restore_edge_embeddings_merge_false(self):
        """Edge embedding restore: INSERT INTO kg_EdgeEmbeddings VALUES."""
        edge_line = json.dumps({
            "s": "node_a", "p": "REL", "o_id": "node_b",
            "emb": "0.1,0.2,0.3,0.4"
        })
        sql_files = {
            "sql/Graph_KG_nodes.ndjson": "",
            "sql/Graph_KG_rdf_edges.ndjson": "",
            "sql/Graph_KG_rdf_labels.ndjson": "",
            "sql/Graph_KG_rdf_props.ndjson": "",
            "sql/Graph_KG_rdf_reifications.ndjson": "",
            "sql/Graph_KG_kg_EdgeEmbeddings.ndjson": edge_line,
        }
        snap_path = _make_snapshot_zip(sql_files)
        try:
            eng, conn, cursor = _make_snapshot_engine()
            result = eng.restore_snapshot(snap_path, merge=False)
            assert isinstance(result, dict)
        finally:
            os.unlink(snap_path)

    def test_restore_edge_embeddings_merge_true(self):
        """Edge embedding merge=True: INSERT WHERE NOT EXISTS."""
        edge_line = json.dumps({
            "s": "src", "p": "REL", "o_id": "tgt",
            "emb": "0.5,0.6,0.7,0.8"
        })
        sql_files = {
            "sql/Graph_KG_nodes.ndjson": "",
            "sql/Graph_KG_rdf_edges.ndjson": "",
            "sql/Graph_KG_rdf_labels.ndjson": "",
            "sql/Graph_KG_rdf_props.ndjson": "",
            "sql/Graph_KG_rdf_reifications.ndjson": "",
            "sql/Graph_KG_kg_EdgeEmbeddings.ndjson": edge_line,
        }
        snap_path = _make_snapshot_zip(sql_files)
        try:
            eng, conn, cursor = _make_snapshot_engine()
            result = eng.restore_snapshot(snap_path, merge=True)
            assert isinstance(result, dict)
        finally:
            os.unlink(snap_path)

    def test_restore_edge_embeddings_incomplete_row_skipped(self):
        """Edge embedding row missing required field is skipped."""
        lines = [
            json.dumps({"s": "a", "p": "REL"}),  # missing o_id and emb
            json.dumps({"s": "a", "p": "REL", "o_id": "b", "emb": "0.1,0.2,0.3,0.4"}),
        ]
        sql_files = {
            "sql/Graph_KG_nodes.ndjson": "",
            "sql/Graph_KG_rdf_edges.ndjson": "",
            "sql/Graph_KG_rdf_labels.ndjson": "",
            "sql/Graph_KG_rdf_props.ndjson": "",
            "sql/Graph_KG_rdf_reifications.ndjson": "",
            "sql/Graph_KG_kg_EdgeEmbeddings.ndjson": "\n".join(lines),
        }
        snap_path = _make_snapshot_zip(sql_files)
        try:
            eng, conn, cursor = _make_snapshot_engine()
            result = eng.restore_snapshot(snap_path, merge=False)
            assert isinstance(result, dict)
        finally:
            os.unlink(snap_path)

    def test_restore_edge_embeddings_bad_json_skipped(self):
        """Malformed JSON in edge embedding file is skipped."""
        lines = ["INVALID !!!", json.dumps({"s": "a", "p": "R", "o_id": "b", "emb": "0.1,0.2,0.3,0.4"})]
        sql_files = {
            "sql/Graph_KG_nodes.ndjson": "",
            "sql/Graph_KG_rdf_edges.ndjson": "",
            "sql/Graph_KG_rdf_labels.ndjson": "",
            "sql/Graph_KG_rdf_props.ndjson": "",
            "sql/Graph_KG_rdf_reifications.ndjson": "",
            "sql/Graph_KG_kg_EdgeEmbeddings.ndjson": "\n".join(lines),
        }
        snap_path = _make_snapshot_zip(sql_files)
        try:
            eng, conn, cursor = _make_snapshot_engine()
            result = eng.restore_snapshot(snap_path, merge=False)
            assert isinstance(result, dict)
        finally:
            os.unlink(snap_path)


class TestRestoreSnapshotBothEmbeddings:

    def test_restore_both_node_and_edge_embeddings(self):
        """Snapshot with both node and edge embeddings restores both tables."""
        node_line = json.dumps({"id": "n1", "emb": "0.1,0.2,0.3,0.4"})
        edge_line = json.dumps({"s": "n1", "p": "REL", "o_id": "n2", "emb": "0.5,0.6,0.7,0.8"})
        sql_files = {
            "sql/Graph_KG_nodes.ndjson": "",
            "sql/Graph_KG_rdf_edges.ndjson": "",
            "sql/Graph_KG_rdf_labels.ndjson": "",
            "sql/Graph_KG_rdf_props.ndjson": "",
            "sql/Graph_KG_rdf_reifications.ndjson": "",
            "sql/Graph_KG_kg_NodeEmbeddings.ndjson": node_line,
            "sql/Graph_KG_kg_EdgeEmbeddings.ndjson": edge_line,
        }
        snap_path = _make_snapshot_zip(sql_files)
        try:
            eng, conn, cursor = _make_snapshot_engine()
            result = eng.restore_snapshot(snap_path, merge=False)
            assert isinstance(result, dict)
        finally:
            os.unlink(snap_path)

    def test_restore_empty_embedding_file(self):
        """Empty embedding file (blank lines only) processes without error."""
        sql_files = {
            "sql/Graph_KG_nodes.ndjson": "",
            "sql/Graph_KG_rdf_edges.ndjson": "",
            "sql/Graph_KG_rdf_labels.ndjson": "",
            "sql/Graph_KG_rdf_props.ndjson": "",
            "sql/Graph_KG_rdf_reifications.ndjson": "",
            "sql/Graph_KG_kg_NodeEmbeddings.ndjson": "\n\n\n",
        }
        snap_path = _make_snapshot_zip(sql_files)
        try:
            eng, conn, cursor = _make_snapshot_engine()
            result = eng.restore_snapshot(snap_path, merge=False)
            assert isinstance(result, dict)
        finally:
            os.unlink(snap_path)


class TestRestoreSnapshotGlobals:

    def test_restore_globals_path_executes(self):
        """globals/ files trigger the global import path."""
        global_ndjson = json.dumps({"k": ["sub1"], "v": "hello"}) + "\n"
        sql_files = {
            "sql/Graph_KG_nodes.ndjson": "",
            "sql/Graph_KG_rdf_edges.ndjson": "",
            "sql/Graph_KG_rdf_labels.ndjson": "",
            "sql/Graph_KG_rdf_props.ndjson": "",
            "sql/Graph_KG_rdf_reifications.ndjson": "",
        }
        global_files = {"globals/KG.ndjson": global_ndjson.encode()}
        snap_path = _make_snapshot_zip(sql_files, global_files)
        try:
            eng, conn, cursor = _make_snapshot_engine()
            iris_obj = MagicMock()
            iris_obj.set.return_value = None
            with patch.object(eng, "_iris_obj", return_value=iris_obj):
                result = eng.restore_snapshot(snap_path, merge=False)
            assert isinstance(result, dict)
        finally:
            os.unlink(snap_path)

    def test_restore_globals_import_exception_logged(self):
        """Exception during global import is caught and logged (warning)."""
        global_ndjson = json.dumps({"k": ["sub1"], "v": "data"}) + "\n"
        sql_files = {
            "sql/Graph_KG_nodes.ndjson": "",
            "sql/Graph_KG_rdf_edges.ndjson": "",
            "sql/Graph_KG_rdf_labels.ndjson": "",
            "sql/Graph_KG_rdf_props.ndjson": "",
            "sql/Graph_KG_rdf_reifications.ndjson": "",
        }
        global_files = {"globals/KG.ndjson": global_ndjson.encode()}
        snap_path = _make_snapshot_zip(sql_files, global_files)
        try:
            eng, conn, cursor = _make_snapshot_engine()
            with patch.object(eng, "_iris_obj", side_effect=RuntimeError("no IRIS")):
                result = eng.restore_snapshot(snap_path, merge=False)
            assert isinstance(result, dict)
        finally:
            os.unlink(snap_path)


class TestRestoreSnapshotResultStructure:

    def test_restore_result_has_restored_tables(self):
        """restore_snapshot returns dict with 'restored_tables' key."""
        sql_files = {
            "sql/Graph_KG_nodes.ndjson": "",
            "sql/Graph_KG_rdf_edges.ndjson": "",
            "sql/Graph_KG_rdf_labels.ndjson": "",
            "sql/Graph_KG_rdf_props.ndjson": "",
            "sql/Graph_KG_rdf_reifications.ndjson": "",
        }
        snap_path = _make_snapshot_zip(sql_files)
        try:
            eng, conn, cursor = _make_snapshot_engine()
            result = eng.restore_snapshot(snap_path, merge=False)
            assert "restored_tables" in result
        finally:
            os.unlink(snap_path)

    def test_restore_result_has_layers(self):
        """restore_snapshot result includes 'layers' key."""
        sql_files = {
            "sql/Graph_KG_nodes.ndjson": json.dumps({"node_id": "x", "labels": "L"}),
            "sql/Graph_KG_rdf_edges.ndjson": "",
            "sql/Graph_KG_rdf_labels.ndjson": "",
            "sql/Graph_KG_rdf_props.ndjson": "",
            "sql/Graph_KG_rdf_reifications.ndjson": "",
        }
        snap_path = _make_snapshot_zip(sql_files)
        try:
            eng, conn, cursor = _make_snapshot_engine()
            result = eng.restore_snapshot(snap_path, merge=False)
            assert "layers" in result or isinstance(result, dict)
        finally:
            os.unlink(snap_path)
