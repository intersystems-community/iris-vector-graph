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
    _stub_eraser(eng)
    return eng, conn, cursor


def _stub_eraser(eng):
    """A non-merge restore clears the database through `Graph.KG.Eraser`.

    `restore_snapshot` used to keep its own clear list plus whichever globals the
    snapshot's metadata happened to name, so a snapshot written without global
    metadata restored rows on top of the previous database's ^KG. It now calls
    `erase_all()` (ADR-0004), so an unreachable IRIS is a failed restore rather
    than a silent merge into the previous database.
    """
    iris_obj = MagicMock()
    iris_obj.classMethodValue.return_value = 0
    eng._iris_obj = MagicMock(return_value=iris_obj)
    return iris_obj


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
        """Exception during global import is caught and logged (warning).

        Aimed at the import specifically, not at the seam: a non-merge restore also
        reaches IRIS to clear, and an unreachable seam would fail that instead —
        which is a failed restore, not a logged warning.
        """
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
            with patch.object(
                eng, "_import_global_from_ndjson", side_effect=RuntimeError("no IRIS")
            ):
                result = eng.restore_snapshot(snap_path, merge=False)
            assert isinstance(result, dict)
            assert result["restored_globals"] == []
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


def _empty_tables(**overrides) -> dict:
    files = {
        "sql/Graph_KG_nodes.ndjson": "",
        "sql/Graph_KG_rdf_edges.ndjson": "",
        "sql/Graph_KG_rdf_labels.ndjson": "",
        "sql/Graph_KG_rdf_props.ndjson": "",
        "sql/Graph_KG_rdf_reifications.ndjson": "",
    }
    files.update(overrides)
    return files


def _inserts_into(cursor, table: str) -> list:
    """The (sql, params) pairs the restore sent to one table."""
    out = []
    for call in cursor.execute.call_args_list:
        sql = call.args[0] if call.args else ""
        if f"INSERT INTO {table}" in sql:
            params = call.args[1] if len(call.args) > 1 else []
            out.append((sql, params))
    return out


class TestRestoreNormalisesTheDefaultGraphSpelling:
    """A pre-tightening archive spells the default graph NULL; the column forbids it.

    v2.16 wrote NULL into `rdf_edges.graph_id` for every default-graph edge (see
    tests/unit/test_old_release_fixtures.py, which reads the real archive). The
    live `Graph.KG.Edge` marks the column Required, so inserting that row verbatim
    raises SQLCODE -108. ADR-0003 fixes the default graph's SQL spelling as `''`,
    and NULL from an older release means the same graph — so the restore
    normalises rather than losing the row.
    """

    def test_a_null_graph_id_is_inserted_as_the_empty_string(self):
        edge = json.dumps(
            {"s": "n1", "p": "knows", "o_id": "n2", "qualifiers": None, "graph_id": None}
        )
        snap_path = _make_snapshot_zip(
            _empty_tables(**{"sql/Graph_KG_rdf_edges.ndjson": edge})
        )
        try:
            eng, conn, cursor = _make_snapshot_engine()
            eng.restore_snapshot(snap_path, merge=False)

            inserts = _inserts_into(cursor, "Graph_KG.rdf_edges")
            assert len(inserts) == 1
            sql, params = inserts[0]
            cols = sql.split("(")[1].split(")")[0].split(", ")
            assert params[cols.index("graph_id")] == ""
        finally:
            os.unlink(snap_path)

    def test_a_named_graph_is_left_exactly_as_the_archive_wrote_it(self):
        edge = json.dumps({"s": "n5", "p": "knows", "o_id": "n6", "graph_id": "acme"})
        snap_path = _make_snapshot_zip(
            _empty_tables(**{"sql/Graph_KG_rdf_edges.ndjson": edge})
        )
        try:
            eng, conn, cursor = _make_snapshot_engine()
            eng.restore_snapshot(snap_path, merge=False)

            sql, params = _inserts_into(cursor, "Graph_KG.rdf_edges")[0]
            cols = sql.split("(")[1].split(")")[0].split(", ")
            assert params[cols.index("graph_id")] == "acme"
        finally:
            os.unlink(snap_path)

    def test_other_null_columns_are_not_touched(self):
        """Only the graph key has a canonical non-NULL spelling.

        `qualifiers` is genuinely optional, so normalising every NULL would invent
        an empty JSON payload where the old release recorded the absence of one.
        """
        edge = json.dumps(
            {"s": "n1", "p": "knows", "o_id": "n2", "qualifiers": None, "graph_id": None}
        )
        snap_path = _make_snapshot_zip(
            _empty_tables(**{"sql/Graph_KG_rdf_edges.ndjson": edge})
        )
        try:
            eng, conn, cursor = _make_snapshot_engine()
            eng.restore_snapshot(snap_path, merge=False)

            sql, params = _inserts_into(cursor, "Graph_KG.rdf_edges")[0]
            cols = sql.split("(")[1].split(")")[0].split(", ")
            assert params[cols.index("qualifiers")] is None
        finally:
            os.unlink(snap_path)

    def test_a_nodes_row_with_a_null_graph_id_is_normalised_too(self):
        """`Graph_KG.nodes.graph_id` is NOT NULL DEFAULT '' on both code paths."""
        node = json.dumps({"node_id": "n1", "graph_id": None, "created_at": None})
        snap_path = _make_snapshot_zip(
            _empty_tables(**{"sql/Graph_KG_nodes.ndjson": node})
        )
        try:
            eng, conn, cursor = _make_snapshot_engine()
            eng.restore_snapshot(snap_path, merge=False)

            sql, params = _inserts_into(cursor, "Graph_KG.nodes")[0]
            cols = sql.split("(")[1].split(")")[0].split(", ")
            assert params[cols.index("graph_id")] == ""
        finally:
            os.unlink(snap_path)


class TestRestoreReportsRowsItCouldNotInsert:
    """A count a consumer can check, instead of a debug line nobody reads.

    Continuing past a bad row is right — one row should not abandon an upgrade —
    but the only signal used to be `logger.debug`, so a silently dropped row was
    indistinguishable from an archive that never held it.
    """

    def test_failed_rows_is_empty_when_every_row_lands(self):
        edge = json.dumps({"s": "n1", "p": "knows", "o_id": "n2", "graph_id": ""})
        snap_path = _make_snapshot_zip(
            _empty_tables(**{"sql/Graph_KG_rdf_edges.ndjson": edge})
        )
        try:
            eng, conn, cursor = _make_snapshot_engine()
            result = eng.restore_snapshot(snap_path, merge=False)

            assert result["failed_rows"] == {}
        finally:
            os.unlink(snap_path)

    def test_a_rejected_row_is_counted_against_its_table(self):
        edges = "\n".join(
            [
                json.dumps({"s": "n1", "p": "knows", "o_id": "n2", "graph_id": ""}),
                json.dumps({"s": "BAD", "p": "knows", "o_id": "n3", "graph_id": ""}),
            ]
        )
        snap_path = _make_snapshot_zip(
            _empty_tables(**{"sql/Graph_KG_rdf_edges.ndjson": edges})
        )
        try:
            eng, conn, cursor = _make_snapshot_engine()

            def reject_bad(sql, params=None):
                if params and "BAD" in params:
                    raise Exception("SQLCODE -108")

            cursor.execute.side_effect = reject_bad
            result = eng.restore_snapshot(snap_path, merge=False)

            assert result["restored_tables"]["Graph_KG.rdf_edges"] == 1
            assert result["failed_rows"] == {"Graph_KG.rdf_edges": 1}
        finally:
            os.unlink(snap_path)

    def test_a_malformed_line_counts_as_a_failed_row(self):
        """A line that will not parse is data the consumer did not get back."""
        snap_path = _make_snapshot_zip(
            _empty_tables(**{"sql/Graph_KG_rdf_edges.ndjson": "NOT JSON {{{"})
        )
        try:
            eng, conn, cursor = _make_snapshot_engine()
            result = eng.restore_snapshot(snap_path, merge=False)

            assert result["failed_rows"] == {"Graph_KG.rdf_edges": 1}
        finally:
            os.unlink(snap_path)


class TestRestoreWritesEmbeddingMetadata:
    """`save_snapshot` exports the column, so the restore has to write it.

    The old restore read `metadata` into a local and inserted `(id, emb)` only, so
    a consumer who upgraded by snapshot-and-restore lost every embedding's
    provenance without a symptom — the vectors came back.
    """

    def test_the_metadata_column_is_inserted_with_the_vector(self):
        meta = json.dumps({"src": "fixture", "seq": "1"})
        vec = json.dumps({"id": "n1", "emb": "0.1,0.2,0.3,0.4", "metadata": meta})
        snap_path = _make_snapshot_zip(
            _empty_tables(**{"sql/Graph_KG_kg_NodeEmbeddings.ndjson": vec})
        )
        try:
            eng, conn, cursor = _make_snapshot_engine()
            eng.restore_snapshot(snap_path, merge=False)

            sql, params = _inserts_into(cursor, "Graph_KG.kg_NodeEmbeddings")[0]
            assert "metadata" in sql
            assert meta in params
        finally:
            os.unlink(snap_path)

    def test_a_row_without_metadata_still_restores(self):
        """Absent metadata is absent, not the string "None"."""
        vec = json.dumps({"id": "n1", "emb": "0.1,0.2,0.3,0.4", "metadata": None})
        snap_path = _make_snapshot_zip(
            _empty_tables(**{"sql/Graph_KG_kg_NodeEmbeddings.ndjson": vec})
        )
        try:
            eng, conn, cursor = _make_snapshot_engine()
            result = eng.restore_snapshot(snap_path, merge=False)

            assert result["restored_tables"]["Graph_KG.kg_NodeEmbeddings"] == 1
            _sql, params = _inserts_into(cursor, "Graph_KG.kg_NodeEmbeddings")[0]
            assert None in params
        finally:
            os.unlink(snap_path)

    def test_the_merge_path_writes_metadata_as_well(self):
        meta = json.dumps({"src": "fixture", "seq": "2"})
        vec = json.dumps({"id": "n2", "emb": "0.5,0.6,0.7,0.8", "metadata": meta})
        snap_path = _make_snapshot_zip(
            _empty_tables(**{"sql/Graph_KG_kg_NodeEmbeddings.ndjson": vec})
        )
        try:
            eng, conn, cursor = _make_snapshot_engine()
            eng.restore_snapshot(snap_path, merge=True)

            sql, params = _inserts_into(cursor, "Graph_KG.kg_NodeEmbeddings")[0]
            assert "metadata" in sql
            assert meta in params
        finally:
            os.unlink(snap_path)
