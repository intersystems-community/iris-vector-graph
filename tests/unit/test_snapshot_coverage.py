"""
Coverage tests for _engine/snapshot.py.
Targets lines 52, 60-64, 86-90, 114-263 (import_rdf),
359-361, 388-389, 418-419, 426-428, 443, 515-526, 534-535,
538-557, 570, 592-593, 596-597, 633-634, 643, 667-668, 673-674,
707, 778-795 (load_obo).
"""
import io
import json
import zipfile
import tempfile
import os
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_engine():
    from iris_vector_graph.engine import IRISGraphEngine
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = []
    cur.fetchone.return_value = None
    cur.description = []
    conn.cursor.return_value = cur
    conn.commit.return_value = None
    conn.rollback.return_value = None
    eng.conn = conn
    eng._schema_prefix = "Graph_KG"
    eng._native_vec_available = False
    eng._embedding_function_available = False
    eng._arno_available = False
    eng._arno_capabilities = {}
    eng.embedding_dimension = 128
    eng._nkg_dirty = False
    eng.vector_dtype = "DOUBLE"
    _stub_eraser(eng)
    return eng, cur


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


def make_snapshot_zip(
    metadata=None,
    sql_files=None,
    global_files=None,
) -> bytes:
    """Build a minimal in-memory snapshot zip."""
    if metadata is None:
        metadata = {
            "version": "2.5.0",
            "created_ts": 0,
            "tables": {},
            "globals": {},
            "has_vector_sql": False,
        }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("metadata.json", json.dumps(metadata))
        for name, content in (sql_files or {}).items():
            zf.writestr(name, content)
        for name, content in (global_files or {}).items():
            zf.writestr(name, content)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# load_networkx: progress callback and long-prop truncation (lines 52, 60-64)
# ---------------------------------------------------------------------------

def test_load_networkx_long_property_truncated():
    """Properties > 60000 chars are truncated."""
    eng, cur = make_engine()
    eng.create_node = MagicMock(return_value=True)
    eng.create_edge = MagicMock(return_value=True)
    eng.sync = MagicMock()

    import networkx as nx
    G = nx.DiGraph()
    G.add_node("n1", bigprop="x" * 70000)
    eng.load_networkx(G, auto_sync=False)
    call_kwargs = eng.create_node.call_args[1]
    assert len(call_kwargs["properties"]["bigprop"]) == 60000


def test_load_networkx_progress_callback_called():
    """Progress callback fires at end even for small graphs."""
    eng, cur = make_engine()
    eng.create_node = MagicMock(return_value=True)
    eng.create_edge = MagicMock(return_value=True)
    eng.sync = MagicMock()

    import networkx as nx
    G = nx.DiGraph()
    G.add_node("n1", type="Person")
    G.add_node("n2", type="Drug")
    G.add_edge("n1", "n2", predicate="TREATS")

    calls = []
    def cb(nodes, edges):
        calls.append((nodes, edges))

    eng.load_networkx(G, progress_callback=cb, auto_sync=False)
    assert len(calls) >= 1


def test_load_networkx_namespace_label():
    """Nodes with 'namespace' attribute use it as label."""
    eng, cur = make_engine()
    eng.create_node = MagicMock(return_value=True)
    eng.create_edge = MagicMock(return_value=True)
    eng.sync = MagicMock()

    import networkx as nx
    G = nx.DiGraph()
    G.add_node("n1", namespace="Drug")
    eng.load_networkx(G, auto_sync=False)

    call_kwargs = eng.create_node.call_args[1]
    assert "Drug" in call_kwargs["labels"]


def test_load_networkx_auto_sync_called():
    """auto_sync=True calls sync() when nodes added."""
    eng, cur = make_engine()
    eng.create_node = MagicMock(return_value=True)
    eng.create_edge = MagicMock(return_value=False)
    eng.sync = MagicMock()

    import networkx as nx
    G = nx.DiGraph()
    G.add_node("n1", type="Person")
    eng.load_networkx(G, auto_sync=True)
    eng.sync.assert_called_once()


# ---------------------------------------------------------------------------
# import_rdf: ImportError path (line 114-124)
# ---------------------------------------------------------------------------

def test_import_rdf_no_rdflib_raises():
    """import_rdf raises ImportError when rdflib missing."""
    eng, cur = make_engine()
    import sys
    with patch.dict(sys.modules, {"rdflib": None}):
        with pytest.raises(ImportError, match="rdflib"):
            eng.import_rdf("/tmp/fake.ttl")


# ---------------------------------------------------------------------------
# restore_snapshot: basic restore with ndjson SQL data
# ---------------------------------------------------------------------------

def test_restore_snapshot_basic():
    """restore_snapshot processes nodes NDJSON and inserts rows."""
    eng, cur = make_engine()

    node_row = json.dumps({"id": "n1", "name": "Alice"})
    snapshot_data = make_snapshot_zip(
        sql_files={
            "sql/Graph_KG_nodes.ndjson": node_row,
        },
        metadata={
            "version": "2.5.0",
            "created_ts": 100,
            "tables": {},
            "globals": {},
        },
    )

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        f.write(snapshot_data)
        path = f.name

    try:
        result = eng.restore_snapshot(path, merge=False)
        assert "restored_tables" in result
    finally:
        os.unlink(path)


def test_restore_snapshot_merge_mode():
    """restore_snapshot merge=True uses WHERE NOT EXISTS insert."""
    eng, cur = make_engine()

    node_row = json.dumps({"id": "n1", "name": "Alice"})
    snapshot_data = make_snapshot_zip(
        sql_files={"sql/Graph_KG_nodes.ndjson": node_row},
        metadata={
            "version": "2.5.0",
            "created_ts": 0,
            "tables": {},
            "globals": {},
        },
    )

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        f.write(snapshot_data)
        path = f.name

    try:
        result = eng.restore_snapshot(path, merge=True)
        assert "restored_tables" in result
    finally:
        os.unlink(path)


def test_restore_snapshot_rdf_edges_strips_id():
    """restore_snapshot strips 'id' column from rdf_edges rows."""
    eng, cur = make_engine()

    edge_row = json.dumps({"id": 99, "s": "n1", "p": "KNOWS", "o_id": "n2"})
    snapshot_data = make_snapshot_zip(
        sql_files={"sql/Graph_KG_rdf_edges.ndjson": edge_row},
        metadata={
            "version": "2.5.0",
            "created_ts": 0,
            "tables": {},
            "globals": {},
        },
    )

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        f.write(snapshot_data)
        path = f.name

    try:
        result = eng.restore_snapshot(path, merge=False)
        # Check that the INSERT SQL does not include 'id'
        executed_sqls = [str(c[0][0]) for c in cur.execute.call_args_list]
        edge_inserts = [s for s in executed_sqls if "rdf_edges" in s and "INSERT" in s]
        for sql in edge_inserts:
            cols_part = sql.split("(")[1].split(")")[0]
            col_names = [c.strip() for c in cols_part.split(",")]
            assert "id" not in col_names
    finally:
        os.unlink(path)


def test_restore_snapshot_insert_failure_logged():
    """restore_snapshot continues when row insert fails."""
    eng, cur = make_engine()
    # Make all execute calls except DELETE raise
    call_count = [0]
    def selective_raise(sql, *args, **kwargs):
        call_count[0] += 1
        if "INSERT" in str(sql):
            raise Exception("insert failed")
    cur.execute.side_effect = selective_raise

    node_row = json.dumps({"id": "n1", "name": "Alice"})
    snapshot_data = make_snapshot_zip(
        sql_files={"sql/Graph_KG_nodes.ndjson": node_row},
        metadata={
            "version": "2.5.0",
            "created_ts": 0,
            "tables": {},
            "globals": {},
        },
    )

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        f.write(snapshot_data)
        path = f.name

    try:
        result = eng.restore_snapshot(path, merge=False)
        # Should not raise — just log the failure
        assert "restored_tables" in result
    finally:
        os.unlink(path)


def test_restore_snapshot_empty_lines_skipped():
    """restore_snapshot ignores empty lines in NDJSON."""
    eng, cur = make_engine()

    content = "\n\n" + json.dumps({"id": "n1"}) + "\n\n"
    snapshot_data = make_snapshot_zip(
        sql_files={"sql/Graph_KG_nodes.ndjson": content},
        metadata={
            "version": "2.5.0",
            "created_ts": 0,
            "tables": {},
            "globals": {},
        },
    )

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        f.write(snapshot_data)
        path = f.name

    try:
        result = eng.restore_snapshot(path, merge=False)
        assert "restored_tables" in result
    finally:
        os.unlink(path)


def test_restore_snapshot_clears_through_erase_all():
    """Non-merge restore clears by calling erase_all, not by emitting its own SQL.

    It used to name six tables inline here and kill whichever globals the
    snapshot's metadata happened to list. A restore whose clear list is shorter
    than the inventory leaves content the restored rows then inherit, which is the
    drift the Eraser exists to end (ADR-0004).
    """
    eng, cur = make_engine()
    iris_obj = eng._iris_obj()

    snapshot_data = make_snapshot_zip(
        metadata={
            "version": "2.5.0",
            "created_ts": 0,
            "tables": {},
            "globals": {},
        }
    )

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        f.write(snapshot_data)
        path = f.name

    try:
        eng.restore_snapshot(path, merge=False)
        iris_obj.classMethodValue.assert_any_call("Graph.KG.Eraser", "EraseAll")
        executed = [str(c[0][0]) for c in cur.execute.call_args_list]
        assert not [s for s in executed if s.strip().upper().startswith("DELETE")], (
            "restore still deletes outside the Eraser's transaction"
        )
    finally:
        os.unlink(path)


def test_restore_snapshot_raises_when_the_clear_fails():
    """A restore that cannot clear must not go on to insert.

    The old code cleared table by table and committed after each, swallowing the
    failures, so a clear that failed halfway left the database in neither state and
    the restored rows merged into whatever survived. erase_all is one transaction:
    it either clears or leaves the database untouched, and a failure here is a
    failed restore.
    """
    eng, cur = make_engine()
    eng._iris_obj().classMethodValue.side_effect = Exception("erase failed on nodes")

    snapshot_data = make_snapshot_zip(
        metadata={
            "version": "2.5.0",
            "created_ts": 0,
            "tables": {"Graph_KG.nodes": 1},
            "globals": {},
        },
        sql_files={"sql/Graph_KG_nodes.ndjson": json.dumps({"node_id": "n1"})},
    )

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        f.write(snapshot_data)
        path = f.name

    try:
        with pytest.raises(Exception, match="erase failed"):
            eng.restore_snapshot(path, merge=False)
        inserts = [
            str(c[0][0])
            for c in cur.execute.call_args_list
            if str(c[0][0]).strip().upper().startswith("INSERT")
        ]
        assert not inserts, "restore inserted rows into a database it failed to clear"
    finally:
        os.unlink(path)


def test_restore_snapshot_globals_kill_error_ignored():
    """Global kill errors are logged but restore continues."""
    eng, cur = make_engine()

    iris_obj = MagicMock()
    iris_obj.kill.side_effect = Exception("kill failed")
    eng._iris_obj = MagicMock(return_value=iris_obj)

    snapshot_data = make_snapshot_zip(
        metadata={
            "version": "2.5.0",
            "created_ts": 0,
            "tables": {},
            "globals": {"KG": {"subscripts": [["out", 0]]}},
        }
    )

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        f.write(snapshot_data)
        path = f.name

    try:
        result = eng.restore_snapshot(path, merge=False)
        assert result is not None
    finally:
        os.unlink(path)


def test_restore_snapshot_globals_import_ndjson():
    """Global NDJSON files are imported via _iris_obj."""
    eng, cur = make_engine()

    iris_obj = MagicMock()
    iris_obj.set.return_value = None
    eng._iris_obj = MagicMock(return_value=iris_obj)

    global_content = json.dumps({"k": ["out", 0, "n1"], "v": "1"}).encode()
    snapshot_data = make_snapshot_zip(
        metadata={
            "version": "2.5.0",
            "created_ts": 0,
            "tables": {},
            "globals": {"KG": {"format": "ndjson", "subscripts": []}},
        },
        global_files={"globals/KG.ndjson": global_content},
    )

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        f.write(snapshot_data)
        path = f.name

    try:
        result = eng.restore_snapshot(path, merge=False)
        # iris_obj.set should have been called for the global entry
        iris_obj.set.assert_called()
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# load_obo: ImportError path (lines 778-795)
# ---------------------------------------------------------------------------

def test_load_obo_no_obonet_raises():
    """load_obo raises ImportError when obonet absent."""
    eng, cur = make_engine()
    import sys
    with patch.dict(sys.modules, {"obonet": None}):
        with pytest.raises(ImportError, match="obonet"):
            eng.load_obo("/tmp/fake.obo")


def test_load_obo_with_prefix_remaps_nodes():
    """load_obo with prefix= prepends to node ids."""
    eng, cur = make_engine()
    eng.load_networkx = MagicMock(return_value={"nodes": 2, "edges": 0})

    obonet_mock = MagicMock()
    import networkx as nx
    G = nx.DiGraph()
    G.add_node("HP:0001234", namespace="HP")
    G.add_node("HP:0005678", namespace="HP")
    obonet_mock.read_obo.return_value = G

    import sys
    with patch.dict(sys.modules, {"obonet": obonet_mock}):
        with tempfile.NamedTemporaryFile(suffix=".obo", delete=False, mode="w") as f:
            f.write("[Term]\nid: HP:0001234\n")
            path = f.name
        try:
            eng.load_obo(path, prefix="myprefix")
        finally:
            os.unlink(path)

    eng.load_networkx.assert_called_once()
    call_kwargs = eng.load_networkx.call_args
    G_passed = call_kwargs[0][0]
    # All nodes should have prefix prepended
    for nid in G_passed.nodes():
        assert nid.startswith("myprefix:")


# ---------------------------------------------------------------------------
# _import_global_from_ndjson: happy path and error path (lines 749-767)
# ---------------------------------------------------------------------------

def test_import_global_from_ndjson_basic():
    """_import_global_from_ndjson calls iris_obj.set for each valid line."""
    eng, cur = make_engine()
    iris_obj = MagicMock()
    ndjson = json.dumps({"k": ["out", 0, "n1"], "v": "1"}) + "\n"
    count = eng._import_global_from_ndjson(iris_obj, "^KG", ndjson)
    assert count == 1
    iris_obj.set.assert_called_once_with("1", "^KG", "out", 0, "n1")


def test_import_global_from_ndjson_error_line_skipped():
    """_import_global_from_ndjson skips malformed lines."""
    eng, cur = make_engine()
    iris_obj = MagicMock()
    ndjson = "not json\n" + json.dumps({"k": ["a"], "v": "x"}) + "\n"
    count = eng._import_global_from_ndjson(iris_obj, "^KG", ndjson)
    assert count == 1  # only valid line counted


def test_import_global_from_ndjson_empty_lines_skipped():
    """_import_global_from_ndjson ignores blank lines."""
    eng, cur = make_engine()
    iris_obj = MagicMock()
    count = eng._import_global_from_ndjson(iris_obj, "^KG", "\n\n\n")
    assert count == 0
