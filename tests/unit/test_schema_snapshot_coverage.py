"""
Coverage tests for schema.py and _engine/snapshot.py uncovered paths.
Uses mocked cursors — no IRIS connection needed.
"""
import io
import json
import tempfile
import zipfile
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_cursor(fetchall=None, fetchone=None, side_effect=None):
    cur = MagicMock()
    cur.fetchall.return_value = fetchall or []
    cur.fetchone.return_value = fetchone
    if side_effect:
        cur.execute.side_effect = side_effect
    else:
        cur.execute.return_value = None
    cur.description = []
    return cur


def make_engine(cursor=None):
    from iris_vector_graph.engine import IRISGraphEngine
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    conn = MagicMock()
    cur = cursor or make_cursor()
    conn.cursor.return_value = cur
    conn.commit.return_value = None
    eng.conn = conn
    eng._schema_prefix = "Graph_KG"
    eng._native_vec_available = False
    eng._embedding_function_available = False
    eng._arno_available = False
    eng._arno_capabilities = {}
    eng.embedding_dimension = 128
    eng._nkg_dirty = False
    return eng, cur


# ---------------------------------------------------------------------------
# schema.py: _call_classmethod_large (29-38)
# ---------------------------------------------------------------------------

def test_call_classmethod_large_no_chunk():
    from iris_vector_graph.schema import _call_classmethod_large
    iris_obj = MagicMock()
    iris_obj.classMethodValue.return_value = "plain_result"
    result = _call_classmethod_large(iris_obj, "Cls", "Method")
    assert result == "plain_result"


def test_call_classmethod_large_chunked():
    from iris_vector_graph.schema import _call_classmethod_large
    iris_obj = MagicMock()
    def cm_value(cls, method, *args):
        if method == "Method":
            return "CHUNKED:abc123:3"
        if method == "ReadLargeOutChunk":
            tag, i = args
            return f"part{i}"
        return ""
    iris_obj.classMethodValue.side_effect = cm_value
    result = _call_classmethod_large(iris_obj, "Cls", "Method")
    assert result == "part1part2part3"


# ---------------------------------------------------------------------------
# schema.py: GraphSchema.get_base_schema_sql (45-55)
# ---------------------------------------------------------------------------

def test_get_base_schema_sql_default():
    from iris_vector_graph.schema import GraphSchema
    sql = GraphSchema.get_base_schema_sql()
    assert "Graph_KG.nodes" in sql
    assert "rdf_labels" in sql
    assert "rdf_props" in sql
    assert "rdf_edges" in sql


def test_get_base_schema_sql_custom_dim():
    from iris_vector_graph.schema import GraphSchema
    sql = GraphSchema.get_base_schema_sql(embedding_dimension=384)
    assert "384" in sql


# ---------------------------------------------------------------------------
# schema.py: disable_indexes / rebuild_indexes (370-465)
# ---------------------------------------------------------------------------

def test_disable_indexes_success():
    from iris_vector_graph.schema import GraphSchema
    cur = make_cursor()
    result = GraphSchema.disable_indexes(cur)
    assert isinstance(result, dict)


def test_disable_indexes_not_found():
    from iris_vector_graph.schema import GraphSchema
    def side_effect(sql):
        raise Exception("does not exist")
    cur = make_cursor(side_effect=side_effect)
    result = GraphSchema.disable_indexes(cur)
    assert all(v is True for v in result.values())


def test_disable_indexes_other_error():
    from iris_vector_graph.schema import GraphSchema
    calls = [0]
    def side_effect(sql):
        calls[0] += 1
        if calls[0] <= 3:
            raise Exception("permission denied")
    cur = make_cursor(side_effect=side_effect)
    result = GraphSchema.disable_indexes(cur)
    assert isinstance(result, dict)


def test_rebuild_indexes_success():
    from iris_vector_graph.schema import GraphSchema
    cur = make_cursor()
    result = GraphSchema.rebuild_indexes(cur)
    assert isinstance(result, dict)
    assert len(result) > 0


def test_rebuild_indexes_already_exists():
    from iris_vector_graph.schema import GraphSchema
    def side_effect(sql):
        raise Exception("already exists")
    cur = make_cursor(side_effect=side_effect)
    result = GraphSchema.rebuild_indexes(cur)
    assert all(v is True for v in result.values())


def test_rebuild_indexes_sqlcode_400():
    from iris_vector_graph.schema import GraphSchema
    calls = [0]
    def side_effect(sql):
        calls[0] += 1
        if calls[0] % 2 == 1:
            raise Exception("sqlcode: <-400>")
        # second call (ALTER fallback) succeeds
        return None
    cur = make_cursor(side_effect=side_effect)
    result = GraphSchema.rebuild_indexes(cur)
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# schema.py: get_bulk_insert_sql (468-492)
# ---------------------------------------------------------------------------

def test_get_bulk_insert_sql_nodes():
    from iris_vector_graph.schema import GraphSchema
    sql = GraphSchema.get_bulk_insert_sql("nodes")
    assert "nodes" in sql
    assert "INSERT" in sql


def test_get_bulk_insert_sql_rdf_props():
    from iris_vector_graph.schema import GraphSchema
    sql = GraphSchema.get_bulk_insert_sql("rdf_props")
    assert "rdf_props" in sql


def test_get_bulk_insert_sql_kg_embeddings():
    from iris_vector_graph.schema import GraphSchema
    sql = GraphSchema.get_bulk_insert_sql("kg_NodeEmbeddings")
    assert "kg_NodeEmbeddings" in sql
    assert "TO_VECTOR" in sql


def test_get_bulk_insert_sql_unknown_raises():
    from iris_vector_graph.schema import GraphSchema
    with pytest.raises(ValueError, match="Unknown table"):
        GraphSchema.get_bulk_insert_sql("nonexistent_table")


def test_get_bulk_insert_sql_rdf_edges_with_graph():
    from iris_vector_graph.schema import GraphSchema
    sql = GraphSchema.get_bulk_insert_sql("rdf_edges_with_graph")
    assert "graph_id" in sql


# ---------------------------------------------------------------------------
# schema.py: upgrade_val_column (494-514)
# ---------------------------------------------------------------------------

def test_upgrade_val_column_success():
    from iris_vector_graph.schema import GraphSchema
    cur = make_cursor()
    result = GraphSchema.upgrade_val_column(cur)
    assert result is True


def test_upgrade_val_column_already_done():
    from iris_vector_graph.schema import GraphSchema
    def side_effect(sql):
        raise Exception("already correct size")
    cur = make_cursor(side_effect=side_effect)
    result = GraphSchema.upgrade_val_column(cur)
    assert result is True


def test_upgrade_val_column_error():
    from iris_vector_graph.schema import GraphSchema
    def side_effect(sql):
        raise Exception("permission denied")
    cur = make_cursor(side_effect=side_effect)
    result = GraphSchema.upgrade_val_column(cur)
    assert result is False


# ---------------------------------------------------------------------------
# schema.py: update_spo_unique_constraint (322-335)
# ---------------------------------------------------------------------------

def test_update_spo_unique_constraint_success():
    from iris_vector_graph.schema import GraphSchema
    cur = make_cursor()
    result = GraphSchema.update_spo_unique_constraint(cur)
    assert result is True


def test_update_spo_unique_constraint_already_exists():
    from iris_vector_graph.schema import GraphSchema
    calls = [0]
    def side_effect(sql):
        calls[0] += 1
        if calls[0] == 2:
            raise Exception("already exists")
    cur = make_cursor(side_effect=side_effect)
    result = GraphSchema.update_spo_unique_constraint(cur)
    assert result is True


# ---------------------------------------------------------------------------
# snapshot.py: load_networkx (16-102)
# ---------------------------------------------------------------------------

def test_load_networkx_basic():
    try:
        import networkx as nx
    except ImportError:
        pytest.skip("networkx not installed")
    eng, cur = make_engine()
    # Mock create_node and create_edge
    eng.create_node = MagicMock(return_value=True)
    eng.create_edge = MagicMock(return_value=True)
    eng.sync = MagicMock()

    G = nx.DiGraph()
    G.add_node("n1", type="Person", name="Alice")
    G.add_node("n2", type="Person", name="Bob")
    G.add_edge("n1", "n2", predicate="KNOWS", weight="0.9")

    result = eng.load_networkx(G, auto_sync=False)
    assert result["nodes"] == 2
    assert result["edges"] == 1


def test_load_networkx_skip_existing():
    try:
        import networkx as nx
    except ImportError:
        pytest.skip("networkx not installed")
    eng, cur = make_engine()
    eng.create_node = MagicMock(return_value=False)  # all skipped
    eng.create_edge = MagicMock(return_value=False)
    eng.sync = MagicMock()

    G = nx.DiGraph()
    G.add_node("n1")
    G.add_edge("n1", "n1", label="self")

    result = eng.load_networkx(G, auto_sync=False)
    assert result["skipped_nodes"] == 1
    assert result["skipped_edges"] == 1


def test_load_networkx_with_auto_sync():
    try:
        import networkx as nx
    except ImportError:
        pytest.skip("networkx not installed")
    eng, cur = make_engine()
    eng.create_node = MagicMock(return_value=True)
    eng.create_edge = MagicMock(return_value=True)
    eng.sync = MagicMock()

    G = nx.DiGraph()
    G.add_node("n1")

    result = eng.load_networkx(G, auto_sync=True)
    eng.sync.assert_called_once()


def test_load_networkx_auto_rebuild_kg_deprecated():
    try:
        import networkx as nx
    except ImportError:
        pytest.skip("networkx not installed")
    eng, cur = make_engine()
    eng.create_node = MagicMock(return_value=True)
    eng.create_edge = MagicMock(return_value=False)
    eng.sync = MagicMock()

    G = nx.DiGraph()
    G.add_node("n1")

    with pytest.warns(DeprecationWarning, match="auto_rebuild_kg"):
        result = eng.load_networkx(G, auto_rebuild_kg=False)


def test_load_networkx_namespace_label():
    try:
        import networkx as nx
    except ImportError:
        pytest.skip("networkx not installed")
    eng, cur = make_engine()
    eng.create_node = MagicMock(return_value=True)
    eng.create_edge = MagicMock(return_value=True)
    eng.sync = MagicMock()

    G = nx.DiGraph()
    G.add_node("n1", namespace="Drug")

    result = eng.load_networkx(G, label_attr="type", auto_sync=False)
    call_args = eng.create_node.call_args
    assert "Drug" in call_args.kwargs.get("labels", [])


def test_load_networkx_progress_callback():
    try:
        import networkx as nx
    except ImportError:
        pytest.skip("networkx not installed")
    eng, cur = make_engine()
    eng.create_node = MagicMock(return_value=True)
    eng.create_edge = MagicMock(return_value=True)
    eng.sync = MagicMock()

    G = nx.DiGraph()
    for i in range(5):
        G.add_node(f"n{i}")

    called = []
    eng.load_networkx(G, auto_sync=False, progress_callback=lambda n, e: called.append((n, e)))
    assert len(called) > 0


# ---------------------------------------------------------------------------
# snapshot.py: snapshot_info (452-468)
# ---------------------------------------------------------------------------

def test_snapshot_info_reads_metadata():
    from iris_vector_graph._engine.snapshot import SnapshotMixin
    metadata = {
        "version": "1.1",
        "tables": {"Graph_KG.nodes": 5},
        "has_vector_sql": True,
        "created_ts": 1234567890,
        "globals": {},
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("metadata.json", json.dumps(metadata))
    buf.seek(0)
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        f.write(buf.read())
        path = f.name

    info = SnapshotMixin.snapshot_info(path)
    assert info["version"] == "1.1"
    assert info["has_vector_sql"] is True
    assert info["tables"]["Graph_KG.nodes"] == 5


# ---------------------------------------------------------------------------
# snapshot.py: save_snapshot (266-450)
# ---------------------------------------------------------------------------

def test_save_snapshot_sql_layer():
    eng, cur = make_engine()
    # cursor returns empty result for each table
    cur.description = [("node_id",)]
    cur.fetchall.return_value = []
    eng._iris_obj = MagicMock(side_effect=Exception("no IRIS"))

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        path = f.name

    result = eng.save_snapshot(path, layers=["sql"])
    assert "path" in result
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    assert "metadata.json" in names


def test_save_snapshot_no_globals_layer():
    eng, cur = make_engine()
    cur.description = [("node_id",)]
    cur.fetchall.return_value = []
    eng._iris_obj = MagicMock(side_effect=Exception("no IRIS"))

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        path = f.name

    result = eng.save_snapshot(path, layers=["sql"])
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    # No globals/ entries when globals layer not requested
    assert not any(n.startswith("globals/") for n in names)


# ---------------------------------------------------------------------------
# schema.py: add_graph_id_column / add_graph_id_index (337-369)
# ---------------------------------------------------------------------------

def test_add_graph_id_column_success():
    from iris_vector_graph.schema import GraphSchema
    cur = make_cursor()
    result = GraphSchema.add_graph_id_column(cur)
    assert result is True


def test_add_graph_id_column_already_exists():
    from iris_vector_graph.schema import GraphSchema
    def side_effect(sql):
        raise Exception("column already exists")
    cur = make_cursor(side_effect=side_effect)
    result = GraphSchema.add_graph_id_column(cur)
    assert result is True


def test_add_graph_id_index_success():
    from iris_vector_graph.schema import GraphSchema
    cur = make_cursor()
    result = GraphSchema.add_graph_id_index(cur)
    assert result is True
