"""
Integration tests for SnapshotMixin: load_networkx, save_snapshot, load_obo,
import_rdf, import_graph_ndjson, export_graph_ndjson.

All tests run against live ivg-iris with real IRIS SQL. No mocking.
File I/O uses tmp_path (pytest fixture) for reproducibility.
"""
import json
import pytest
from iris_vector_graph.engine import IRISGraphEngine


@pytest.fixture
def engine(iris_connection, iris_master_cleanup):
    return IRISGraphEngine(iris_connection, embedding_dimension=384)


# ---------------------------------------------------------------------------
# load_networkx
# ---------------------------------------------------------------------------

class TestLoadNetworkx:

    def test_load_simple_graph(self, engine, iris_connection):
        nx = pytest.importorskip("networkx")
        G = nx.DiGraph()
        G.add_node("nx_a", type="Person", name="Alice")
        G.add_node("nx_b", type="Person", name="Bob")
        G.add_edge("nx_a", "nx_b", predicate="KNOWS")

        stats = engine.load_networkx(G, auto_sync=True)
        assert stats["nodes"] == 2
        assert stats["edges"] == 1

        cur = iris_connection.cursor()
        cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id IN ('nx_a','nx_b')")
        assert int(cur.fetchone()[0]) == 2

    def test_load_networkx_returns_stats_dict(self, engine):
        nx = pytest.importorskip("networkx")
        G = nx.DiGraph()
        G.add_node("s_a"); G.add_node("s_b")
        G.add_edge("s_a", "s_b", predicate="R")
        stats = engine.load_networkx(G, auto_sync=False)
        assert "nodes" in stats
        assert "edges" in stats
        assert "skipped_nodes" in stats
        assert "skipped_edges" in stats

    def test_load_networkx_skip_existing(self, engine, iris_connection):
        nx = pytest.importorskip("networkx")
        G = nx.DiGraph()
        G.add_node("dup_a"); G.add_node("dup_b")
        G.add_edge("dup_a", "dup_b", predicate="R")

        stats1 = engine.load_networkx(G, skip_existing=True, auto_sync=False)
        stats2 = engine.load_networkx(G, skip_existing=True, auto_sync=False)
        # Second load should skip the already-existing nodes
        assert stats2["skipped_nodes"] >= stats1["nodes"]

    def test_load_networkx_progress_callback(self, engine):
        nx = pytest.importorskip("networkx")
        G = nx.DiGraph()
        for i in range(5):
            G.add_node(f"cb_{i}")
        for i in range(4):
            G.add_edge(f"cb_{i}", f"cb_{i+1}", predicate="R")

        calls = []
        engine.load_networkx(G, progress_callback=lambda n, e: calls.append((n, e)),
                             auto_sync=False)
        assert len(calls) > 0

    def test_load_networkx_edge_predicate_from_label(self, engine, iris_connection):
        nx = pytest.importorskip("networkx")
        G = nx.DiGraph()
        G.add_node("lbl_a"); G.add_node("lbl_b")
        G.add_edge("lbl_a", "lbl_b", label="TAGGED_AS")
        engine.load_networkx(G, auto_sync=False)
        cur = iris_connection.cursor()
        cur.execute("SELECT p FROM Graph_KG.rdf_edges WHERE s='lbl_a'")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "TAGGED_AS"

    def test_load_networkx_auto_rebuild_kg_deprecated_warning(self, engine):
        nx = pytest.importorskip("networkx")
        G = nx.DiGraph()
        G.add_node("dep_a")
        with pytest.warns(DeprecationWarning, match="auto_rebuild_kg"):
            engine.load_networkx(G, auto_rebuild_kg=False)


# ---------------------------------------------------------------------------
# save_snapshot / restore (round-trip)
# ---------------------------------------------------------------------------

class TestSaveSnapshot:

    def test_save_snapshot_creates_zip(self, engine, iris_connection, tmp_path):
        engine.create_node("snap_a", labels=["Thing"])
        engine.create_node("snap_b", labels=["Thing"])
        engine.create_edge("snap_a", "LINKED", "snap_b")

        out = tmp_path / "test_snapshot.zip"
        stats = engine.save_snapshot(str(out))
        assert out.exists(), "Snapshot ZIP file not created"
        assert out.stat().st_size > 0

    def test_save_snapshot_returns_stats_dict(self, engine, tmp_path):
        engine.create_node("snap_c")
        out = tmp_path / "snap.zip"
        stats = engine.save_snapshot(str(out))
        assert isinstance(stats, dict)

    def test_save_snapshot_contains_nodes_table(self, engine, tmp_path):
        import zipfile
        engine.create_node("snap_d", labels=["A"])
        out = tmp_path / "snap2.zip"
        engine.save_snapshot(str(out))
        with zipfile.ZipFile(str(out), "r") as zf:
            names = zf.namelist()
        assert any("node" in n.lower() or "metadata" in n.lower() for n in names), (
            f"Expected nodes or metadata in ZIP, got: {names}"
        )

    def test_snapshot_info_reads_metadata(self, engine, tmp_path):
        engine.create_node("snap_e")
        out = tmp_path / "snap3.zip"
        engine.save_snapshot(str(out))
        info = engine.snapshot_info(str(out))
        assert isinstance(info, dict)
        assert "version" in info or len(info) > 0


# ---------------------------------------------------------------------------
# export_graph_ndjson / import_graph_ndjson
# ---------------------------------------------------------------------------

class TestNDJsonRoundTrip:

    def test_export_ndjson_creates_file(self, engine, iris_connection, tmp_path):
        engine.create_node("nd_a", labels=["Doc"])
        engine.create_node("nd_b", labels=["Doc"])
        engine.create_edge("nd_a", "REFS", "nd_b")

        out = tmp_path / "export.ndjson"
        stats = engine.export_graph_ndjson(str(out))
        assert out.exists()
        assert out.stat().st_size > 0

    def test_export_ndjson_valid_json_lines(self, engine, tmp_path):
        engine.create_node("nd_c")
        out = tmp_path / "export2.ndjson"
        engine.export_graph_ndjson(str(out))
        lines = out.read_text().strip().split("\n")
        for line in lines:
            if line.strip():
                obj = json.loads(line)
                assert isinstance(obj, dict)

    def test_import_ndjson_roundtrip(self, engine, iris_connection, tmp_path):
        engine.create_node("rnd_a", labels=["X"], properties={"val": "1"})
        engine.create_node("rnd_b", labels=["X"])
        engine.create_edge("rnd_a", "GOES", "rnd_b")

        out = tmp_path / "roundtrip.ndjson"
        engine.export_graph_ndjson(str(out))

        # Wipe and reimport
        cur = iris_connection.cursor()
        for t in ["Graph_KG.rdf_edges", "Graph_KG.rdf_labels", "Graph_KG.rdf_props", "Graph_KG.nodes"]:
            try:
                cur.execute(f"DELETE FROM {t} WHERE {'s' if t != 'Graph_KG.nodes' else 'node_id'} LIKE 'rnd_%'")
            except Exception:
                pass
        iris_connection.commit()

        stats = engine.import_graph_ndjson(str(out))
        assert stats is not None

        cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id LIKE 'rnd_%'")
        assert int(cur.fetchone()[0]) >= 2


# ---------------------------------------------------------------------------
# import_rdf (Turtle / N-Triples)
# ---------------------------------------------------------------------------

class TestImportRDF:

    def test_import_rdf_turtle(self, engine, iris_connection, tmp_path):
        pytest.importorskip("rdflib")
        ttl = tmp_path / "test.ttl"
        ttl.write_text("""
@prefix ex: <http://example.org/> .
ex:Alice a ex:Person ;
         ex:knows ex:Bob .
ex:Bob a ex:Person .
""")
        stats = engine.import_rdf(str(ttl), format="turtle")
        assert stats is not None
        cur = iris_connection.cursor()
        cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id LIKE '%Alice%' OR node_id LIKE '%Bob%'")
        assert int(cur.fetchone()[0]) >= 2

    def test_import_rdf_ntriples(self, engine, iris_connection, tmp_path):
        pytest.importorskip("rdflib")
        nt = tmp_path / "test.nt"
        nt.write_text(
            "<http://ex.org/A> <http://ex.org/rel> <http://ex.org/B> .\n"
            "<http://ex.org/B> <http://ex.org/rel> <http://ex.org/C> .\n"
        )
        stats = engine.import_rdf(str(nt), format="nt")
        assert stats is not None
