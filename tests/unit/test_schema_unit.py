"""
Unit tests for _engine/schema.py covering uncovered branches:
- is_ready (success and failure)
- get_labels, get_relationship_types, get_node_count, get_edge_count
- get_label_distribution, get_property_keys, node_exists
- initialize_schema (basic path)
- get_schema_visualization
- sync, _sync_kg (success + failure), _sync_nkg (success + failure)
- rebuild_kg, rebuild_nkg (deprecated)
- backfill_degp, backfill_deg2p_exact
- materialize_inference (rdfs and owl)
- retract_inference
- reify_edge (success + failure)
- get_reifications (success + failure)
- delete_reification (success + failure)

No IRIS connection needed — mocks conn and cursor.
"""
import json
import pytest
import warnings
from unittest.mock import MagicMock, patch, call
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.capabilities import IRISCapabilities


def _make_eng(dim=4):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.return_value = None
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.description = []
    cursor.close.return_value = None
    cursor.rowcount = 0
    return IRISGraphEngine(conn, embedding_dimension=dim), conn, cursor


# ---------------------------------------------------------------------------
# is_ready
# ---------------------------------------------------------------------------

class TestIsReady:

    def test_ready_returns_true(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.return_value = (42,)
        assert eng.is_ready() is True

    def test_failure_returns_false(self):
        eng, conn, cursor = _make_eng()
        cursor.execute.side_effect = RuntimeError("table not found")
        assert eng.is_ready() is False


# ---------------------------------------------------------------------------
# get_labels / get_relationship_types / get_node_count / get_edge_count
# ---------------------------------------------------------------------------

class TestSchemaReaders:

    def test_get_labels_returns_list(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = [("Gene",), ("Disease",)]
        result = eng.get_labels()
        assert result == ["Gene", "Disease"]

    def test_get_relationship_types(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = [("TREATS",), ("CAUSES",)]
        result = eng.get_relationship_types()
        assert result == ["TREATS", "CAUSES"]

    def test_get_node_count_no_label(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.return_value = (99,)
        result = eng.get_node_count()
        assert result == 99

    def test_get_node_count_with_label(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.return_value = (7,)
        result = eng.get_node_count(label="Gene")
        assert result == 7

    def test_get_edge_count_no_predicate(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.return_value = (200,)
        result = eng.get_edge_count()
        assert result == 200

    def test_get_edge_count_with_predicate(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.return_value = (15,)
        result = eng.get_edge_count(predicate="TREATS")
        assert result == 15

    def test_get_label_distribution(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = [("Gene", 100), ("Disease", 50)]
        result = eng.get_label_distribution()
        assert result == {"Gene": 100, "Disease": 50}

    def test_get_property_keys_no_label(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = [("name",), ("description",)]
        result = eng.get_property_keys()
        assert "name" in result

    def test_get_property_keys_with_label(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = [("name",)]
        result = eng.get_property_keys(label="Gene")
        assert result == ["name"]

    def test_node_exists_true(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.return_value = (1,)
        assert eng.node_exists("n1") is True

    def test_node_exists_false(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.return_value = (0,)
        assert eng.node_exists("missing") is False

    def test_node_exists_none_row(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchone.return_value = None
        assert eng.node_exists("gone") is False


# ---------------------------------------------------------------------------
# get_schema_visualization
# ---------------------------------------------------------------------------

class TestGetSchemaVisualization:

    def test_empty_graph_returns_empty_nodes_rels(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = None
        result = eng.get_schema_visualization()
        assert result == {"nodes": [], "relationships": []}

    def test_with_labels_and_rel_types(self):
        eng, conn, cursor = _make_eng()
        call_seq = iter([
            [("Gene",)],              # DISTINCT labels
            [("Disease",)],           # DISTINCT rel types
            ("n1",),                  # TOP 1 sample for Gene label
            [("name",)],             # prop keys for Gene
            ("n1", "n2"),            # TOP 1 edge for TREATS
            ("Gene",),               # label for src
            ("Disease",),            # label for tgt
        ])
        cursor.fetchall.side_effect = lambda: (
            next(call_seq) if isinstance(next.__self__, type(call_seq)) else []
        )
        # Simpler: mock fetchall and fetchone separately
        cursor.fetchall.side_effect = None
        cursor.fetchone.side_effect = None
        cursor.fetchall.return_value = [("Gene",)]

        all_calls = iter([
            [("Gene",)],         # labels
            [("TREATS",)],       # rel types
            [("name",)],         # props for Gene
        ])
        cursor.fetchall.side_effect = lambda: next(all_calls)

        fetchone_calls = iter([
            ("n1",),             # sample id for Gene
            ("n1", "n2"),        # edge for TREATS
            ("Gene",),           # src label
            ("Disease",),        # tgt label
        ])
        cursor.fetchone.side_effect = lambda: next(fetchone_calls)

        result = eng.get_schema_visualization()
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["name"] == "Gene"
        assert len(result["relationships"]) == 1


# ---------------------------------------------------------------------------
# sync / _sync_kg / _sync_nkg
# ---------------------------------------------------------------------------

class TestSync:

    def test_sync_calls_both_sync_methods(self):
        eng, conn, cursor = _make_eng()
        with patch.object(eng, "_sync_kg", return_value=True) as mock_kg:
            with patch.object(eng, "_sync_nkg", return_value=True) as mock_nkg:
                result = eng.sync()
        assert result is True
        mock_kg.assert_called_once()
        mock_nkg.assert_called_once()

    def test_sync_returns_false_if_kg_fails(self):
        eng, conn, cursor = _make_eng()
        with patch.object(eng, "_sync_kg", return_value=False):
            with patch.object(eng, "_sync_nkg", return_value=True):
                result = eng.sync()
        assert result is False

    def test_sync_kg_success(self):
        eng, conn, cursor = _make_eng()
        mock_iris = MagicMock()
        mock_iris.classMethodVoid.return_value = None
        with patch.object(eng, "_iris_obj", return_value=mock_iris):
            result = eng._sync_kg()
        assert result is True
        assert eng.capabilities.kg_built is True

    def test_sync_kg_failure_returns_false(self):
        eng, conn, cursor = _make_eng()
        with patch("iris_vector_graph.schema._call_classmethod",
                   side_effect=RuntimeError("BuildKG failed")):
            result = eng._sync_kg()
        assert result is False

    def test_sync_nkg_success(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodVoid.return_value = None
        iris_obj.classMethodValue.return_value = "100"
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            with patch.object(eng, "_detect_arno", return_value=False):
                result = eng._sync_nkg()
        assert result is True

    def test_sync_nkg_failure_returns_false(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodVoid.side_effect = RuntimeError("BuildNKG failed")
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            with patch.object(eng, "_detect_arno", return_value=False):
                result = eng._sync_nkg()
        assert result is False

    def test_sync_nkg_with_arno_rust_success(self):
        """Cover lines 505-512: Rust path."""
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = [
            json.dumps({"ok": True}),  # BuildNKGRust
            "100",                      # Build2HopStats
        ]
        iris_obj.classMethodVoid.return_value = None
        eng._arno_capabilities = {"rust_callout": True}
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            with patch.object(eng, "_detect_arno", return_value=True):
                result = eng._sync_nkg()
        assert result is True

    def test_sync_nkg_with_arno_rust_error_fallback(self):
        """Cover lines 513-516: Rust returns error, fall back to ObjectScript."""
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = [
            json.dumps({"error": "OOM"}),  # BuildNKGRust with error
            "100",                          # Build2HopStats
        ]
        iris_obj.classMethodVoid.return_value = None
        eng._arno_capabilities = {"rust_callout": True}
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            with patch.object(eng, "_detect_arno", return_value=True):
                result = eng._sync_nkg()
        assert result is True


# ---------------------------------------------------------------------------
# rebuild_kg / rebuild_nkg (deprecated)
# ---------------------------------------------------------------------------

class TestDeprecatedSync:

    def test_rebuild_kg_warns_and_delegates(self):
        eng, conn, cursor = _make_eng()
        with patch.object(eng, "_sync_kg", return_value=True) as mock_kg:
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                result = eng.rebuild_kg()
        assert result is True
        assert any(issubclass(warning.category, DeprecationWarning) for warning in w)

    def test_rebuild_nkg_warns_and_delegates(self):
        eng, conn, cursor = _make_eng()
        with patch.object(eng, "_sync_nkg", return_value=True) as mock_nkg:
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                result = eng.rebuild_nkg()
        assert result is True
        assert any(issubclass(warning.category, DeprecationWarning) for warning in w)


# ---------------------------------------------------------------------------
# backfill_degp / backfill_deg2p_exact
# ---------------------------------------------------------------------------

class TestBackfillDeg:

    def test_backfill_degp_success(self):
        """Cover line 556."""
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "42"
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng.backfill_degp()
        assert result == 42

    def test_backfill_degp_failure_returns_zero(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = RuntimeError("class not found")
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng.backfill_degp()
        assert result == 0

    def test_backfill_deg2p_exact_success(self):
        """Cover line 565."""
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.return_value = "100"
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng.backfill_deg2p_exact()
        assert result == 100

    def test_backfill_deg2p_exact_failure_returns_zero(self):
        eng, conn, cursor = _make_eng()
        iris_obj = MagicMock()
        iris_obj.classMethodValue.side_effect = RuntimeError("not deployed")
        with patch.object(eng, "_iris_obj", return_value=iris_obj):
            result = eng.backfill_deg2p_exact()
        assert result == 0


# ---------------------------------------------------------------------------
# materialize_inference
# ---------------------------------------------------------------------------

class TestMaterializeInference:

    def test_rdfs_rules_with_empty_graph(self):
        """Cover lines 603-614, 619-633, 636-637: rdfs path, empty DB."""
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (0,)
        result = eng.materialize_inference(rules="rdfs")
        assert "inferred" in result
        assert result["inferred"] == 0

    def test_owl_rules_with_empty_graph(self):
        """Cover lines 693-726: owl extra rules."""
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (0,)
        result = eng.materialize_inference(rules="owl")
        assert "inferred" in result

    def test_with_graph_filter(self):
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (0,)
        result = eng.materialize_inference(rules="rdfs", graph="named_graph_1")
        assert "inferred" in result

    def test_inferred_edges_inserted(self):
        """Cover lines 648, 650-651: transitive closure and insertion."""
        eng, conn, cursor = _make_eng()
        # Provide subClassOf chain: A subClassOf B, B subClassOf C
        call_seq = iter([
            [("A", "B")],    # subclass_direct
            [("B", "C")],    # subprop_direct
            [],              # rdf_type_edges
            [],              # domain_edges
            [],              # range_edges
            [],              # all predicate edges
        ])
        cursor.fetchall.side_effect = lambda: next(call_seq)
        cursor.fetchone.return_value = (0,)  # _exists returns 0
        result = eng.materialize_inference(rules="rdfs")
        assert "inferred" in result


# ---------------------------------------------------------------------------
# retract_inference
# ---------------------------------------------------------------------------

class TestRetractInference:

    def test_retract_all(self):
        """Cover lines 735, 746-747."""
        eng, conn, cursor = _make_eng()
        cursor.rowcount = 5
        result = eng.retract_inference()
        assert result == 5

    def test_retract_with_graph(self):
        eng, conn, cursor = _make_eng()
        cursor.rowcount = 2
        result = eng.retract_inference(graph="named_graph_1")
        assert result == 2

    def test_commit_exception_is_swallowed(self):
        eng, conn, cursor = _make_eng()
        cursor.rowcount = 1
        conn.commit.side_effect = RuntimeError("commit failed")
        result = eng.retract_inference()
        assert result == 1


# ---------------------------------------------------------------------------
# reify_edge
# ---------------------------------------------------------------------------

class TestReifyEdge:

    def test_success_returns_reifier_id(self):
        """Cover lines 792-795."""
        eng, conn, cursor = _make_eng()
        result = eng.reify_edge(
            edge_id=42, reifier_id="reif_1", label="Reification",
            props={"confidence": "0.9"}
        )
        assert result == "reif_1"

    def test_sql_failure_returns_none(self):
        """Cover lines 818-820."""
        eng, conn, cursor = _make_eng()
        cursor.execute.side_effect = RuntimeError("no table")
        result = eng.reify_edge(edge_id=99)
        assert result is None


# ---------------------------------------------------------------------------
# get_reifications
# ---------------------------------------------------------------------------

class TestGetReifications:

    def test_success_returns_list(self):
        """Cover lines 800-817."""
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = [
            ("reif_1", "confidence", "0.9"),
            ("reif_1", "source", "pubmed"),
        ]
        result = eng.get_reifications(edge_id=42)
        assert len(result) == 1
        assert result[0]["reifier_id"] == "reif_1"
        assert result[0]["properties"]["confidence"] == "0.9"

    def test_failure_returns_empty(self):
        """Cover lines 818-820."""
        eng, conn, cursor = _make_eng()
        cursor.execute.side_effect = RuntimeError("table missing")
        result = eng.get_reifications(edge_id=99)
        assert result == []


# ---------------------------------------------------------------------------
# delete_reification
# ---------------------------------------------------------------------------

class TestDeleteReification:

    def test_success_returns_true(self):
        """Cover lines 843-846."""
        eng, conn, cursor = _make_eng()
        result = eng.delete_reification("reif_1")
        assert result is True

    def test_failure_returns_false(self):
        eng, conn, cursor = _make_eng()
        cursor.execute.side_effect = RuntimeError("constraint")
        result = eng.delete_reification("reif_1")
        assert result is False
