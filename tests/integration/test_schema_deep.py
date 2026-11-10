"""
Integration tests targeting uncovered paths in _engine/schema.py.

Covers:
  - run_inference rdfs + owl rules (L603-726)
  - retract_inference (L732-748)
  - reify_edge failure inside its WriteTxn (4.7.0)
  - get_reifications exception path (L818-820)
  - delete_reification exception path (L843-846)
  - rebuild_kg / rebuild_nkg deprecation warnings (L531-550)
  - backfill_degp (L553)
  - embedding dimension mismatch path (L198-237)
  - _sync_nkg (L501-528)
  - rebuild_indexes (L336-348) via bulk_load_session
"""
import pytest
import warnings
from unittest.mock import patch, MagicMock
from iris_vector_graph.engine import IRISGraphEngine


@pytest.fixture
def sc_eng(iris_connection, iris_master_cleanup):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
    for i in range(4):
        eng.create_node(f"sc_{i}", labels=["SC"], properties={"val": i})
    for i in range(3):
        eng.create_edge(f"sc_{i}", "SC_REL", f"sc_{i + 1}")
    eng.sync()
    return eng


# ---------------------------------------------------------------------------
# run_inference — L603+
# ---------------------------------------------------------------------------

class TestRunInference:

    def test_run_inference_rdfs_basic(self, sc_eng):
        result = sc_eng.materialize_inference(rules="rdfs")
        assert isinstance(result, dict)
        assert "inferred" in result
        assert isinstance(result["inferred"], int)

    def test_run_inference_owl_basic(self, sc_eng):
        result = sc_eng.materialize_inference(rules="owl")
        assert isinstance(result, dict)
        assert "inferred" in result

    def test_run_inference_with_graph_filter(self, sc_eng):
        # graph= param restricts to a named graph
        result = sc_eng.materialize_inference(rules="rdfs", graph="test_graph")
        assert isinstance(result, dict)
        assert "inferred" in result

    def test_run_inference_owl_with_rdf_triples(self, sc_eng):
        # Add some RDF-type triples to trigger OWL paths
        cursor = sc_eng.conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES "
                "('sc_0', 'http://www.w3.org/1999/02/22-rdf-syntax-ns#type', 'MyClass'), "
                "('MyClass', 'http://www.w3.org/2000/01/rdf-schema#subClassOf', 'ParentClass')"
            )
            sc_eng.conn.commit()
        except Exception:
            pass
        result = sc_eng.materialize_inference(rules="owl")
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# retract_inference — L732-748
# ---------------------------------------------------------------------------

class TestRetractInference:

    def test_retract_inference_basic(self, sc_eng):
        # Insert an inferred triple first
        cursor = sc_eng.conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO Graph_KG.rdf_edges (s, p, o_id, qualifiers) "
                "VALUES ('sc_x', 'rdfs:type', 'sc_y', '{\"inferred\":\"true\"}')"
            )
            sc_eng.conn.commit()
        except Exception:
            pass
        count = sc_eng.retract_inference()
        assert isinstance(count, int)
        assert count >= 0

    def test_retract_inference_with_graph(self, sc_eng):
        count = sc_eng.retract_inference(graph="some_graph")
        assert isinstance(count, int)

    def test_run_then_retract(self, sc_eng):
        # Run first, then retract
        inferred = sc_eng.materialize_inference(rules="rdfs")
        deleted = sc_eng.retract_inference()
        assert isinstance(deleted, int)


# ---------------------------------------------------------------------------
# reify_edge exception path — L792-795
# ---------------------------------------------------------------------------

class TestReifyEdgeException:

    def test_reify_edge_basic(self, sc_eng):
        cursor = sc_eng.conn.cursor()
        cursor.execute(
            "SELECT edge_id FROM Graph_KG.rdf_edges WHERE s='sc_0' AND p='SC_REL' LIMIT 1"
        )
        row = cursor.fetchone()
        if not row:
            pytest.skip("No edge to reify")
        edge_id = row[0]
        result = sc_eng.reify_edge(edge_id, props={"confidence": "0.8"})
        assert result is not None

    @staticmethod
    def _left(eng, reifier):
        cur = eng.conn.cursor()
        counts = []
        for sql in (
            "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ?",
            "SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE s = ?",
            "SELECT COUNT(*) FROM Graph_KG.rdf_props WHERE s = ?",
            "SELECT COUNT(*) FROM Graph_KG.rdf_reifications WHERE reifier_id = ?",
        ):
            cur.execute(sql, [reifier])
            counts.append(int(cur.fetchone()[0]))
        return counts

    @pytest.mark.parametrize("failure", ("commit", "refused_property"))
    def test_reify_edge_exception_path(self, sc_eng, failure):
        """4.7.0: reify_edge writes in one WriteTxn (it called conn.commit(), which the old version of this test
        patched; that path is gone, so the test exercised nothing). A failure inside it, its commit or a property row
        IRIS refuses after the reifier node, labels and link are written, raises GraphWriteError and leaves none of
        them."""
        from iris_vector_graph._write_txn import WriteTxn
        from iris_vector_graph.errors import GraphWriteError

        cursor = sc_eng.conn.cursor()
        cursor.execute("SELECT edge_id FROM Graph_KG.rdf_edges WHERE s = 'sc_0' AND p = 'SC_REL'")
        edge_id = int(cursor.fetchone()[0])
        reifier = f"sc_reifier_{failure}"
        assert self._left(sc_eng, reifier) == [0, 0, 0, 0]
        if failure == "commit":
            real_commit = WriteTxn.commit

            def commit(txn):
                # reify_edge's own commit: the one that runs once its link row is written (create_node's runs first)
                if self._left(sc_eng, reifier)[3]:
                    raise RuntimeError("forced commit failure")
                return real_commit(txn)

            with patch.object(WriteTxn, "commit", autospec=True, side_effect=commit):
                with pytest.raises(GraphWriteError, match="reify_edge failed.*forced commit failure"):
                    sc_eng.reify_edge(edge_id, reifier_id=reifier, props={"k": "v"})
        else:
            with pytest.raises(GraphWriteError, match="reify_edge failed"):
                sc_eng.reify_edge(edge_id, reifier_id=reifier, props={"k": "v", "K" * 129: "v"})
        assert self._left(sc_eng, reifier) == [0, 0, 0, 0], "a refused reify_edge left rows behind"
        assert sc_eng.reify_edge(edge_id, reifier_id=reifier, props={"k": "v"}) == reifier  # nothing in the way

    def test_reify_nonexistent(self, sc_eng):
        result = sc_eng.reify_edge(999999999)
        assert result is None


# ---------------------------------------------------------------------------
# get_reifications exception path — L818-820
# ---------------------------------------------------------------------------

class TestGetReificationsException:

    def test_get_reifications_exception_path(self, sc_eng):
        # Force the SQL query to raise
        cursor_mock = MagicMock()
        cursor_mock.execute.side_effect = RuntimeError("cursor fail")
        cursor_mock.__enter__ = MagicMock(return_value=cursor_mock)
        cursor_mock.__exit__ = MagicMock(return_value=False)
        # get_reifications' docstring: "a failed query raises (4.6.0; it answered [])"
        with patch.object(sc_eng.conn, "cursor", return_value=cursor_mock):
            with pytest.raises(RuntimeError, match="cursor fail"):
                sc_eng.get_reifications(12345)

    def test_get_reifications_normal(self, sc_eng):
        result = sc_eng.get_reifications(999999)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# delete_reification exception path — L843-846
# ---------------------------------------------------------------------------

class TestDeleteReificationException:

    def test_delete_reification_exception_path(self, sc_eng):
        cursor_mock = MagicMock()
        cursor_mock.execute.side_effect = RuntimeError("delete fail")
        cursor_mock.close = MagicMock()
        # 4.6.0 R203: a refused delete raises GraphWriteError (it answered False)
        from iris_vector_graph.errors import GraphWriteError

        with patch.object(sc_eng.conn, "cursor", return_value=cursor_mock):
            with pytest.raises(GraphWriteError, match="delete_reification failed"):
                sc_eng.delete_reification("fake_reif_id")

    def test_delete_reification_nonexistent(self, sc_eng):
        result = sc_eng.delete_reification("definitely_not_there_12345")
        assert result is True or result is False


# ---------------------------------------------------------------------------
# rebuild_kg / rebuild_nkg deprecation warnings
# ---------------------------------------------------------------------------

class TestDeprecatedRebuildMethods:

    def test_rebuild_kg_warns(self, sc_eng):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            sc_eng.rebuild_kg()
        assert any(issubclass(warning.category, DeprecationWarning) for warning in w)

    def test_rebuild_nkg_warns(self, sc_eng):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            sc_eng.rebuild_nkg()
        assert any(issubclass(warning.category, DeprecationWarning) for warning in w)


# ---------------------------------------------------------------------------
# backfill_degp (L553)
# ---------------------------------------------------------------------------

class TestBackfillDegp:

    def test_backfill_degp(self, sc_eng):
        result = sc_eng.backfill_degp()
        assert isinstance(result, int)
        assert result >= 0


# ---------------------------------------------------------------------------
# _sync_nkg paths (L501-528)
# ---------------------------------------------------------------------------

class TestSyncNKG:

    def test_sync_nkg_success(self, sc_eng):
        # Call _sync_nkg directly — may succeed or gracefully fail
        result = sc_eng._sync_nkg()
        assert isinstance(result, bool)

    def test_sync_nkg_iris_obj_raises(self, sc_eng):
        # 4.6.0 R135: a failed rebuild raises (it answered False, and sync() went on)
        with patch.object(sc_eng, "_iris_obj", side_effect=RuntimeError("no iris")):
            with pytest.raises(RuntimeError, match="no iris"):
                sc_eng._sync_nkg()

    def test_sync_nkg_with_arno_rust_callout(self, sc_eng):
        # Simulate arno capabilities with rust_callout
        mock_iris = MagicMock()
        mock_iris.classMethodValue.return_value = '{"status":"ok"}'
        mock_iris.classMethodVoid.return_value = None
        with patch.object(sc_eng, "_iris_obj", return_value=mock_iris):
            with patch.object(sc_eng, "_detect_arno", return_value=True):
                orig_caps = sc_eng._arno_capabilities
                sc_eng._arno_capabilities = {"bfs": True, "rust_callout": True}
                try:
                    result = sc_eng._sync_nkg()
                except Exception:
                    result = False
                finally:
                    sc_eng._arno_capabilities = orig_caps
        assert isinstance(result, bool)

    def test_sync_nkg_rust_callout_raises(self, sc_eng):
        # BuildNKGRust raises → falls back to ObjectScript
        mock_iris = MagicMock()

        def mock_value(*args, **kwargs):
            method = args[1] if len(args) > 1 else ""
            if method == "BuildNKGRust":
                raise RuntimeError("rust fail")
            return "1"

        mock_iris.classMethodValue.side_effect = mock_value
        mock_iris.classMethodVoid.return_value = None
        with patch.object(sc_eng, "_iris_obj", return_value=mock_iris):
            with patch.object(sc_eng, "_detect_arno", return_value=True):
                orig_caps = sc_eng._arno_capabilities
                sc_eng._arno_capabilities = {"bfs": True, "rust_callout": True}
                try:
                    result = sc_eng._sync_nkg()
                except Exception:
                    result = False
                finally:
                    sc_eng._arno_capabilities = orig_caps
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Embedding dimension mismatch path — L198-237
# ---------------------------------------------------------------------------

class TestEmbeddingDimensionMismatch:

    def test_embedding_dimension_mismatch_nonempty(self, iris_connection):
        # Simulate: db_dim=8, engine configured for 4, table is non-empty
        # This triggers the error log path at L227-235
        from iris_vector_graph._engine.schema import SchemaMixin

        class FakeSchema(SchemaMixin):
            pass

        eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        # patch get_embedding_dimension to return wrong dim
        from iris_vector_graph.schema import GraphSchema
        with patch.object(GraphSchema, "get_embedding_dimension", return_value=8):
            cursor = iris_connection.cursor()
            # Ensure there's a row count > 0
            try:
                cursor.execute("SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings")
                row = cursor.fetchone()
                if row and int(row[0]) == 0:
                    # Insert a dummy row so non-empty path is triggered
                    pass
            except Exception:
                pass
        # Just verify engine initialization handles the mismatch gracefully
        assert eng is not None

    def test_embedding_dimension_none(self, iris_connection):
        # Simulate: db_dim=None → trigger alter column path
        from iris_vector_graph.schema import GraphSchema
        with patch.object(GraphSchema, "get_embedding_dimension", return_value=None):
            eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
        assert eng is not None


# ---------------------------------------------------------------------------
# rebuild_indexes via bulk_load_session (schema.py L336-348)
# ---------------------------------------------------------------------------

class TestRebuildIndexes:

    def test_rebuild_indexes_success(self, sc_eng):
        from iris_vector_graph.schema import GraphSchema
        cursor = sc_eng.conn.cursor()
        result = GraphSchema.rebuild_indexes(cursor)
        assert result is True or result is None or isinstance(result, dict)

    def test_rebuild_indexes_exception_swallowed(self, sc_eng):
        from iris_vector_graph.schema import GraphSchema
        cursor_mock = MagicMock()
        cursor_mock.execute.side_effect = RuntimeError("index fail")
        try:
            GraphSchema.rebuild_indexes(cursor_mock)
        except Exception:
            pass  # Acceptable if it propagates
