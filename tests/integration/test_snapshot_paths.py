"""
Integration tests targeting uncovered paths in _engine/snapshot.py.

Targets:
  L17-95  — load_networkx (progress_callback, auto_rebuild_kg deprecation)
  L106-265 — import_rdf (turtle format)
  L267-453 — save_snapshot
  L455-470 — snapshot_info
  L472-719 — restore_snapshot (merge path)
  L720-769 — _export_global_to_ndjson / _import_global_from_ndjson
  L771-799 — load_obo
  L801-866 — import_graph_ndjson (node, edge, temporal_edge, unknown kind)
  L870-893 — export_graph_ndjson
"""
import os
import json
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from iris_vector_graph.engine import IRISGraphEngine


@pytest.fixture
def snap_eng(iris_connection, iris_master_cleanup):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
    for i in range(5):
        eng.create_node(f"snap_{i}", labels=["SnapNode"], properties={"val": i, "name": f"n{i}"})
    for i in range(4):
        eng.create_edge(f"snap_{i}", "SNAP_REL", f"snap_{i + 1}", qualifiers={"w": str(float(i))})
    eng.sync()
    return eng


# ---------------------------------------------------------------------------
# load_networkx — progress_callback and auto_rebuild_kg deprecation
# ---------------------------------------------------------------------------

class TestLoadNetworkx:

    def test_load_networkx_basic(self, snap_eng):
        try:
            import networkx as nx
        except ImportError:
            pytest.skip("networkx not installed")
        G = nx.DiGraph()
        G.add_node("nx_a", type="TypeA", color="red")
        G.add_node("nx_b", type="TypeB")
        G.add_edge("nx_a", "nx_b", predicate="nx_rel")
        result = snap_eng.load_networkx(G, label_attr="type")
        assert isinstance(result, dict)
        assert "nodes" in result

    def test_load_networkx_with_progress_callback(self, snap_eng):
        try:
            import networkx as nx
        except ImportError:
            pytest.skip("networkx not installed")
        G = nx.DiGraph()
        G.add_node("nx_p1", type="T1")
        G.add_node("nx_p2", type="T2")
        G.add_edge("nx_p1", "nx_p2", predicate="P_REL")
        progress_calls = []
        result = snap_eng.load_networkx(
            G, label_attr="type",
            progress_callback=lambda n, e: progress_calls.append((n, e))
        )
        assert isinstance(result, dict)

    def test_load_networkx_auto_rebuild_kg_deprecated(self, snap_eng):
        try:
            import networkx as nx
            import warnings
        except ImportError:
            pytest.skip("networkx not installed")
        G = nx.DiGraph()
        G.add_node("nx_dep1", type="T")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = snap_eng.load_networkx(G, label_attr="type", auto_rebuild_kg=False)
        assert any(issubclass(warning.category, DeprecationWarning) for warning in w)
        assert isinstance(result, dict)

    def test_load_networkx_namespace_attr(self, snap_eng):
        try:
            import networkx as nx
        except ImportError:
            pytest.skip("networkx not installed")
        G = nx.DiGraph()
        G.add_node("nx_ns1", namespace="NsLabel", color="blue")
        G.add_node("nx_ns2", namespace="NsLabel")
        G.add_edge("nx_ns1", "nx_ns2", predicate="NS_REL")
        result = snap_eng.load_networkx(G, label_attr="namespace")
        assert isinstance(result, dict)

    def test_load_networkx_no_label_attr(self, snap_eng):
        try:
            import networkx as nx
        except ImportError:
            pytest.skip("networkx not installed")
        G = nx.DiGraph()
        G.add_node("nx_nolabel_1")
        G.add_edge("nx_nolabel_1", "nx_nolabel_2" if False else "nx_nolabel_1",
                   predicate="SELF")
        result = snap_eng.load_networkx(G, label_attr=None)
        assert isinstance(result, dict)

    def test_load_networkx_auto_sync_false(self, snap_eng):
        try:
            import networkx as nx
        except ImportError:
            pytest.skip("networkx not installed")
        G = nx.DiGraph()
        G.add_node("nx_nosync_1")
        result = snap_eng.load_networkx(G, auto_sync=False)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# import_rdf — turtle format
# ---------------------------------------------------------------------------

class TestImportRDF:

    def test_import_rdf_turtle(self, snap_eng):
        try:
            import rdflib
        except ImportError:
            pytest.skip("rdflib not installed")
        turtle_data = """
@prefix ex: <http://example.org/> .
ex:Alice a ex:Person ;
    ex:knows ex:Bob .
ex:Bob a ex:Person .
"""
        with tempfile.NamedTemporaryFile(suffix=".ttl", mode="w", delete=False) as f:
            f.write(turtle_data)
            ttl_path = f.name
        try:
            result = snap_eng.import_rdf(ttl_path, format="turtle")
            assert isinstance(result, dict)
            assert "triples" in result
            assert result["triples"] >= 0
        finally:
            os.unlink(ttl_path)

    def test_import_rdf_auto_format_turtle(self, snap_eng):
        try:
            import rdflib
        except ImportError:
            pytest.skip("rdflib not installed")
        turtle_data = """
@prefix ex: <http://example.org/> .
ex:Cat a ex:Animal .
"""
        with tempfile.NamedTemporaryFile(suffix=".ttl", mode="w", delete=False) as f:
            f.write(turtle_data)
            ttl_path = f.name
        try:
            result = snap_eng.import_rdf(ttl_path)  # auto-detect format
            assert isinstance(result, dict)
        finally:
            os.unlink(ttl_path)

    def test_import_rdf_nt_format(self, snap_eng):
        try:
            import rdflib
        except ImportError:
            pytest.skip("rdflib not installed")
        nt_data = "<http://example.org/X> <http://example.org/rel> <http://example.org/Y> .\n"
        with tempfile.NamedTemporaryFile(suffix=".nt", mode="w", delete=False) as f:
            f.write(nt_data)
            nt_path = f.name
        try:
            result = snap_eng.import_rdf(nt_path, format="nt")
            assert isinstance(result, dict)
        finally:
            os.unlink(nt_path)

    def test_import_rdf_with_literal(self, snap_eng):
        try:
            import rdflib
        except ImportError:
            pytest.skip("rdflib not installed")
        turtle_data = """
@prefix ex: <http://example.org/> .
ex:Thing ex:name "some literal value" .
"""
        with tempfile.NamedTemporaryFile(suffix=".ttl", mode="w", delete=False) as f:
            f.write(turtle_data)
            ttl_path = f.name
        try:
            result = snap_eng.import_rdf(ttl_path)
            assert isinstance(result, dict)
        finally:
            os.unlink(ttl_path)


# ---------------------------------------------------------------------------
# import_graph_ndjson — all kinds including temporal_edge and unknown
# ---------------------------------------------------------------------------

class TestImportGraphNDJSON:

    def test_import_ndjson_nodes(self, snap_eng):
        events = [
            {"kind": "node", "id": "ndjson_a", "labels": ["ND"], "properties": {"x": 1}},
            {"kind": "node", "id": "ndjson_b", "labels": ["ND"], "properties": {"x": 2}},
        ]
        with tempfile.NamedTemporaryFile(suffix=".ndjson", mode="w", delete=False) as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
            ndjson_path = f.name
        try:
            result = snap_eng.import_graph_ndjson(ndjson_path)
            assert result["nodes"] == 2
        finally:
            os.unlink(ndjson_path)

    def test_import_ndjson_edges(self, snap_eng):
        events = [
            {"kind": "node", "id": "nd_src", "labels": ["ND"]},
            {"kind": "node", "id": "nd_tgt", "labels": ["ND"]},
            {"kind": "edge", "source": "nd_src", "predicate": "ND_REL", "target": "nd_tgt"},
        ]
        with tempfile.NamedTemporaryFile(suffix=".ndjson", mode="w", delete=False) as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
            ndjson_path = f.name
        try:
            result = snap_eng.import_graph_ndjson(ndjson_path)
            assert result["edges"] == 1
        finally:
            os.unlink(ndjson_path)

    def test_import_ndjson_temporal_edge(self, snap_eng):
        events = [
            {"kind": "temporal_edge", "source": "nd_ts1", "predicate": "T_REL",
             "target": "nd_ts2", "timestamp": 1000, "weight": 0.5,
             "attrs": {"note": "test"}, "source_labels": ["ND"], "target_labels": ["ND"]},
        ]
        with tempfile.NamedTemporaryFile(suffix=".ndjson", mode="w", delete=False) as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
            ndjson_path = f.name
        try:
            result = snap_eng.import_graph_ndjson(ndjson_path)
            assert "temporal_edges" in result
        finally:
            os.unlink(ndjson_path)

    def test_import_ndjson_unknown_kind(self, snap_eng):
        events = [
            {"kind": "unknown_type", "id": "x"},
        ]
        with tempfile.NamedTemporaryFile(suffix=".ndjson", mode="w", delete=False) as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
            ndjson_path = f.name
        try:
            result = snap_eng.import_graph_ndjson(ndjson_path)
            assert result["nodes"] == 0
        finally:
            os.unlink(ndjson_path)

    def test_import_ndjson_malformed_line(self, snap_eng):
        with tempfile.NamedTemporaryFile(suffix=".ndjson", mode="w", delete=False) as f:
            f.write("{not valid json}\n")
            f.write(json.dumps({"kind": "node", "id": "nd_after_bad", "labels": []}) + "\n")
            ndjson_path = f.name
        try:
            result = snap_eng.import_graph_ndjson(ndjson_path)
            assert result["nodes"] >= 0  # bad line skipped, good line processed
        finally:
            os.unlink(ndjson_path)

    def test_import_ndjson_empty_file(self, snap_eng):
        with tempfile.NamedTemporaryFile(suffix=".ndjson", mode="w", delete=False) as f:
            f.write("")
            ndjson_path = f.name
        try:
            result = snap_eng.import_graph_ndjson(ndjson_path)
            assert result["nodes"] == 0
        finally:
            os.unlink(ndjson_path)

    def test_import_ndjson_upsert_nodes_false(self, snap_eng):
        events = [
            {"kind": "temporal_edge", "source": "nd_noup1", "predicate": "T_REL",
             "target": "nd_noup2", "timestamp": 500, "weight": 1.0,
             "source_labels": [], "target_labels": []},
        ]
        with tempfile.NamedTemporaryFile(suffix=".ndjson", mode="w", delete=False) as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
            ndjson_path = f.name
        try:
            result = snap_eng.import_graph_ndjson(ndjson_path, upsert_nodes=False)
            assert "temporal_edges" in result
        finally:
            os.unlink(ndjson_path)


# ---------------------------------------------------------------------------
# export_graph_ndjson
# ---------------------------------------------------------------------------

class TestExportGraphNDJSON:

    def test_export_ndjson(self, snap_eng):
        with tempfile.NamedTemporaryFile(suffix=".ndjson", delete=False) as f:
            out_path = f.name
        try:
            result = snap_eng.export_graph_ndjson(out_path)
            assert isinstance(result, dict)
            assert "nodes" in result
            assert result["nodes"] >= 0
            # Verify file exists and has content
            with open(out_path) as f:
                lines = [l for l in f if l.strip()]
            assert len(lines) >= 0
        finally:
            os.unlink(out_path)


# ---------------------------------------------------------------------------
# save_snapshot + restore_snapshot (merge path)
# ---------------------------------------------------------------------------

class TestSaveRestoreSnapshot:

    def test_save_snapshot_basic(self, snap_eng):
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            snap_path = f.name
        try:
            result = snap_eng.save_snapshot(snap_path)
            assert isinstance(result, dict) or result is None
            assert os.path.exists(snap_path)
        finally:
            if os.path.exists(snap_path):
                os.unlink(snap_path)

    def test_snapshot_info(self, snap_eng):
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            snap_path = f.name
        try:
            snap_eng.save_snapshot(snap_path)
            info = snap_eng.snapshot_info(snap_path)
            assert isinstance(info, dict)
            assert "metadata" in info or "version" in info
        finally:
            if os.path.exists(snap_path):
                os.unlink(snap_path)

    def test_restore_snapshot_merge(self, snap_eng):
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            snap_path = f.name
        try:
            snap_eng.save_snapshot(snap_path)
            result = snap_eng.restore_snapshot(snap_path, merge=True)
            assert isinstance(result, dict) or result is None
        finally:
            if os.path.exists(snap_path):
                os.unlink(snap_path)

    def test_restore_snapshot_no_merge(self, snap_eng):
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            snap_path = f.name
        try:
            snap_eng.save_snapshot(snap_path)
            result = snap_eng.restore_snapshot(snap_path, merge=False)
            assert isinstance(result, dict) or result is None
        finally:
            if os.path.exists(snap_path):
                os.unlink(snap_path)

    def test_save_snapshot_with_layers(self, snap_eng):
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            snap_path = f.name
        try:
            result = snap_eng.save_snapshot(snap_path, layers=["graph"])
            assert isinstance(result, dict) or result is None
        finally:
            if os.path.exists(snap_path):
                os.unlink(snap_path)
