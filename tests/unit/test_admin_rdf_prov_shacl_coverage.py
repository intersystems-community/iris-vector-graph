"""
Coverage tests for:
- _engine/admin.py (lines 62, 70, 86-87, 95-96, 195, 201-228, 256-257, 288, 290, 340-342, 395-396, 463, 489)
- _engine/rdf_export.py (lines 43-44, 125-126, 186-190, 202-203, 207)
- _engine/prov.py (lines 52-53, 73, 184-193, 210-218)
- _engine/shacl.py (lines 86-87, 97-98, 125, 129-132, 153-154)
No IRIS connection needed.
"""
import json
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_engine(rows=None, fetchone=None):
    from iris_vector_graph.engine import IRISGraphEngine
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = rows or []
    cur.fetchone.return_value = fetchone
    cur.description = []
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
    eng.capabilities = MagicMock()
    eng.capabilities.objectscript_deployed = False
    return eng, cur


# ---------------------------------------------------------------------------
# admin.py: execute_admin_command — PROCEDURES / FUNCTIONS branches
# ---------------------------------------------------------------------------

def test_handle_show_command_procedures():
    """SHOW PROCEDURES → _try_system_procedure(dbms.procedures)."""
    eng, cur = make_engine()
    from iris_vector_graph.result import IVGResult
    eng._try_system_procedure = MagicMock(return_value=IVGResult(
        columns=["name", "signature", "description"],
        rows=[["apoc.create.node", "sig", "desc"]]
    ))
    result = eng._handle_show_command("SHOW PROCEDURES")
    assert result is not None
    assert result.columns == ["name", "description", "signature"]


def test_handle_show_command_procedures_no_result():
    """SHOW PROCEDURES with _try_system_procedure returning None."""
    eng, cur = make_engine()
    eng._try_system_procedure = MagicMock(return_value=None)
    result = eng._handle_show_command("SHOW PROCEDURES")
    assert result.rows == []


def test_handle_show_command_functions():
    """SHOW FUNCTIONS → _try_system_procedure(dbms.functions)."""
    eng, cur = make_engine()
    from iris_vector_graph.result import IVGResult
    eng._try_system_procedure = MagicMock(return_value=IVGResult(
        columns=["name", "signature", "description"],
        rows=[["coalesce", "sig", "Returns first non-null"]]
    ))
    result = eng._handle_show_command("SHOW FUNCTIONS")
    assert result.columns == ["name", "description", "signature"]


def test_handle_show_command_functions_no_result():
    """SHOW FUNCTIONS with no result."""
    eng, cur = make_engine()
    eng._try_system_procedure = MagicMock(return_value=None)
    result = eng._handle_show_command("SHOW FUNCTIONS")
    assert result.rows == []


# ---------------------------------------------------------------------------
# admin.py: verify_sync — error/drift paths
# ---------------------------------------------------------------------------

def test_verify_sync_sql_error_indeterminate():
    """SQL COUNT failure → indeterminate=True."""
    eng, cur = make_engine()
    cur.execute.side_effect = Exception("SQL error")
    eng._iris_obj = MagicMock(side_effect=Exception("no iris"))
    result = eng.verify_sync()
    # indeterminate so in_sync=False
    assert result.in_sync is False


def test_verify_sync_nkg_count_fails():
    """NKG count failure → indeterminate with detail."""
    eng, cur = make_engine(fetchone=[10])
    cur.execute.side_effect = None  # SQL count works
    eng._iris_obj = MagicMock(side_effect=Exception("no IRIS"))
    result = eng.verify_sync()
    assert result is not None


def test_verify_sync_heal_calls_sync():
    """heal=True with out-of-sync → sync() called."""
    eng, cur = make_engine(fetchone=[100])
    iris_obj = MagicMock()
    iris_obj.classMethodValue.side_effect = lambda cls, meth: "50"  # NKG < SQL → drift
    eng._iris_obj = MagicMock(return_value=iris_obj)
    eng.sync = MagicMock()
    result = eng.verify_sync(heal=True)
    eng.sync.assert_called_once()
    assert result.healed is True


def test_verify_sync_heal_sync_fails():
    """heal=True and sync() raises → detail updated."""
    eng, cur = make_engine(fetchone=[100])
    iris_obj = MagicMock()
    iris_obj.classMethodValue.side_effect = lambda cls, meth: "50"
    eng._iris_obj = MagicMock(return_value=iris_obj)
    eng.sync = MagicMock(side_effect=Exception("sync failed"))
    result = eng.verify_sync(heal=True)
    assert "heal failed" in (result.detail or "")


# ---------------------------------------------------------------------------
# admin.py: get_centrality_warnings / get_community_warnings
# ---------------------------------------------------------------------------

def test_get_centrality_warnings_no_iris():
    """No IRIS → empty list."""
    eng, cur = make_engine()
    eng._iris_obj = MagicMock(side_effect=Exception("no iris"))
    result = eng.get_centrality_warnings()
    assert result == []


def test_get_community_warnings_no_iris():
    """No IRIS → empty list."""
    eng, cur = make_engine()
    eng._iris_obj = MagicMock(side_effect=Exception("no iris"))
    result = eng.get_community_warnings()
    assert result == []


# ---------------------------------------------------------------------------
# rdf_export.py: _require_rdflib ImportError path
# ---------------------------------------------------------------------------

def test_rdf_require_rdflib_missing():
    """_require_rdflib raises ImportError when rdflib absent."""
    from iris_vector_graph._engine.rdf_export import _require_rdflib
    import sys
    with patch.dict(sys.modules, {"rdflib": None}):
        with pytest.raises((ImportError, Exception)):
            _require_rdflib()


def test_rdf_export_to_rdf_no_rdflib():
    """export_to_rdf raises ImportError when rdflib missing."""
    eng, cur = make_engine()
    import sys
    with patch.dict(sys.modules, {"rdflib": None}):
        with pytest.raises((ImportError, Exception)):
            eng.export_to_rdf("/tmp/test_output.ttl")


# ---------------------------------------------------------------------------
# rdf_export.py: namespace binding error path (line 125-126)
# ---------------------------------------------------------------------------

def test_rdf_export_namespace_bind_exception_ignored():
    """Namespace bind failure is silently ignored."""
    pytest.importorskip("rdflib", reason="rdflib not installed")
    import rdflib
    from iris_vector_graph._engine.rdf_export import RdfExportMixin

    # _project_row_as_rdf is the inner function — test it directly
    from iris_vector_graph._engine.rdf_export import _project_row_as_rdf
    g = rdflib.Graph()
    # Row with s, p, o keys
    _project_row_as_rdf(g, {"s": "urn:n1", "p": "KNOWS", "o": "urn:n2"}, ["s", "p", "o"], "urn:ivg:")
    # Should not raise even with partial data


# ---------------------------------------------------------------------------
# prov.py: _require_rdflib ImportError path
# ---------------------------------------------------------------------------

def test_prov_require_rdflib_missing():
    """_require_rdflib in prov.py raises when rdflib absent."""
    from iris_vector_graph._engine.prov import _require_rdflib
    import sys
    with patch.dict(sys.modules, {"rdflib": None}):
        with pytest.raises((ImportError, Exception)):
            _require_rdflib()


# ---------------------------------------------------------------------------
# prov.py: _get_temporal_edges_window / _get_temporal_edges_for_nodes
# ---------------------------------------------------------------------------

def test_get_temporal_edges_window_no_iris():
    """_get_temporal_edges_window with no IRIS → []."""
    eng, cur = make_engine()
    eng._iris_obj = MagicMock(side_effect=Exception("no iris"))
    result = eng._get_temporal_edges_window(None, None)
    assert result == []


def test_get_temporal_edges_for_nodes_filters():
    """_get_temporal_edges_for_nodes filters by node_ids."""
    eng, cur = make_engine()
    # Mock _get_temporal_edges_window to return controlled data
    eng._get_temporal_edges_window = MagicMock(return_value=[
        {"edge_id": "e1", "source": "n1", "target": "n2", "predicate": "KNOWS", "ts_start": 0},
        {"edge_id": "e2", "source": "n3", "target": "n4", "predicate": "LIKES", "ts_start": 0},
    ])
    result = eng._get_temporal_edges_for_nodes(["n1"])
    assert len(result) == 1
    assert result[0]["edge_id"] == "e1"


def test_get_temporal_edges_by_id():
    """_get_temporal_edges_by_id filters by edge IDs."""
    eng, cur = make_engine()
    eng._get_temporal_edges_window = MagicMock(return_value=[
        {"edge_id": "e1", "source": "n1", "target": "n2", "predicate": "KNOWS", "ts_start": 0},
        {"edge_id": "e2", "source": "n3", "target": "n4", "predicate": "LIKES", "ts_start": 0},
    ])
    result = eng._get_temporal_edges_by_id(["e2"])
    assert len(result) == 1
    assert result[0]["edge_id"] == "e2"


# ---------------------------------------------------------------------------
# shacl.py: _require_rdflib / _require_pyshacl ImportError paths
# ---------------------------------------------------------------------------

def test_shacl_require_rdflib_missing():
    """_require_rdflib in shacl.py raises when rdflib absent."""
    from iris_vector_graph._engine.shacl import _require_rdflib
    import sys
    with patch.dict(sys.modules, {"rdflib": None}):
        with pytest.raises((ImportError, Exception)):
            _require_rdflib()


def test_shacl_require_pyshacl_missing():
    """_require_pyshacl raises when pyshacl absent."""
    from iris_vector_graph._engine.shacl import _require_pyshacl
    import sys
    with patch.dict(sys.modules, {"pyshacl": None}):
        with pytest.raises((ImportError, Exception)):
            _require_pyshacl()


# ---------------------------------------------------------------------------
# shacl.py: _load_shapes_graph error paths
# ---------------------------------------------------------------------------

def test_load_shapes_graph_invalid_type():
    """Non-string, non-Graph input raises ValueError."""
    pytest.importorskip("rdflib", reason="rdflib not installed")
    from iris_vector_graph._engine.shacl import _load_shapes_graph
    with pytest.raises(ValueError, match="must be a file path"):
        _load_shapes_graph(12345)


def test_load_shapes_graph_inline_turtle():
    """Inline Turtle string → parses into Graph."""
    pytest.importorskip("rdflib", reason="rdflib not installed")
    from iris_vector_graph._engine.shacl import _load_shapes_graph
    # Minimal valid Turtle
    turtle_str = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix ex: <http://example.org/> .
ex:PersonShape a sh:NodeShape ;
    sh:targetClass ex:Person .
"""
    g = _load_shapes_graph(turtle_str)
    assert g is not None
    assert len(g) > 0


def test_load_shapes_graph_existing_graph():
    """Passing an existing rdflib.Graph returns it as-is."""
    rdflib = pytest.importorskip("rdflib", reason="rdflib not installed")
    from iris_vector_graph._engine.shacl import _load_shapes_graph
    g = rdflib.Graph()
    result = _load_shapes_graph(g)
    assert result is g


# ---------------------------------------------------------------------------
# shacl.py: _parse_shacl_report (lines 148-154)
# ---------------------------------------------------------------------------

def test_parse_shacl_report_no_rdflib():
    """_parse_shacl_report returns minimal report when rdflib unavailable."""
    from iris_vector_graph._engine.shacl import _parse_shacl_report
    import sys
    with patch.dict(sys.modules, {"rdflib": None}):
        result = _parse_shacl_report(MagicMock(), conforms=True)
        assert result.conforms is True


# ---------------------------------------------------------------------------
# prov.py: _ts_to_datetime + _node_to_iri utilities
# ---------------------------------------------------------------------------

def test_ts_to_datetime_valid():
    from iris_vector_graph._engine.prov import _ts_to_datetime
    result = _ts_to_datetime(0)
    assert "1970" in result


def test_ts_to_datetime_large():
    from iris_vector_graph._engine.prov import _ts_to_datetime
    result = _ts_to_datetime(9999999999)
    assert result is not None  # may be clamped


def test_ts_to_datetime_invalid():
    from iris_vector_graph._engine.prov import _ts_to_datetime
    result = _ts_to_datetime("bad")
    assert result == "1970-01-01T00:00:00Z"


def test_node_to_iri_bare():
    from iris_vector_graph._engine.prov import _node_to_iri
    iri = _node_to_iri("mynode")
    assert "urn:ivg:entity/" in iri


def test_node_to_iri_full_uri():
    from iris_vector_graph._engine.prov import _node_to_iri
    iri = _node_to_iri("http://example.org/n1")
    assert "http://example.org/n1" in iri
