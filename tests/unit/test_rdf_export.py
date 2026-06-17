"""Unit tests for spec-198 US1: RDF export.

Tests cover:
- T005: Format inference from file extension
- T006: IRI minting for bare string / valid IRI node IDs
- T007: label_filter, graph_id, node_ids filter SQL scoping (via _build_rdflib_graph mock)
- T008: Edge qualifier reification
- T009: Namespace prefix binding in Turtle output
- T010: Graceful ImportError when rdflib absent
- T011: export_rdf_from_cypher() projection
- T012: register_namespace / list_namespaces persistence
"""
from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(rows_by_query: dict = None):
    """Build a minimal IRISGraphEngine with a mocked connection."""
    from iris_vector_graph.engine import IRISGraphEngine

    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor

    # Default: all queries return empty
    cursor.fetchmany.return_value = []
    cursor.fetchall.return_value = []
    cursor.rowcount = 0

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = conn
    engine.embedding_dimension = 128
    engine._store = MagicMock()
    return engine, conn, cursor


# ---------------------------------------------------------------------------
# T005: Format inference
# ---------------------------------------------------------------------------

class TestFormatInference:

    def test_ttl_infers_turtle(self):
        from iris_vector_graph._engine._rdf_utils import _infer_format
        assert _infer_format("out.ttl") == "turtle"

    def test_nt_infers_nt(self):
        from iris_vector_graph._engine._rdf_utils import _infer_format
        assert _infer_format("out.nt") == "nt"

    def test_nq_infers_nquads(self):
        from iris_vector_graph._engine._rdf_utils import _infer_format
        assert _infer_format("out.nq") == "nquads"

    def test_jsonld_infers_jsonld(self):
        from iris_vector_graph._engine._rdf_utils import _infer_format
        assert _infer_format("out.jsonld") == "json-ld"

    def test_unknown_defaults_to_turtle(self):
        from iris_vector_graph._engine._rdf_utils import _infer_format
        assert _infer_format("out.xyz") == "turtle"

    def test_no_extension_defaults_to_turtle(self):
        from iris_vector_graph._engine._rdf_utils import _infer_format
        assert _infer_format("output") == "turtle"


# ---------------------------------------------------------------------------
# T006: IRI minting
# ---------------------------------------------------------------------------

class TestIriMinting:

    def test_bare_string_gets_urn_ivg_prefix(self):
        from iris_vector_graph._engine._rdf_utils import _mint_iri
        assert _mint_iri("Patient/001") == "urn:ivg:Patient/001"

    def test_http_uri_passes_through(self):
        from iris_vector_graph._engine._rdf_utils import _mint_iri
        uri = "http://example.org/Patient/001"
        assert _mint_iri(uri) == uri

    def test_urn_passes_through(self):
        from iris_vector_graph._engine._rdf_utils import _mint_iri
        uri = "urn:fhir:Patient/001"
        assert _mint_iri(uri) == uri

    def test_https_passes_through(self):
        from iris_vector_graph._engine._rdf_utils import _mint_iri
        uri = "https://example.org/foo"
        assert _mint_iri(uri) == uri

    def test_custom_base_uri(self):
        from iris_vector_graph._engine._rdf_utils import _mint_iri
        assert _mint_iri("foo", base_uri="http://base.org/") == "http://base.org/foo"


# ---------------------------------------------------------------------------
# T007: Filter parameters (SQL construction via _build_rdflib_graph)
# ---------------------------------------------------------------------------

class TestFilterParameters:

    def _run_with_mock_cursor(self, **kwargs):
        """Run _build_rdflib_graph with a mock cursor, return SQL calls."""
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        cursor.fetchmany.return_value = []

        from iris_vector_graph._engine._rdf_utils import _build_rdflib_graph
        _build_rdflib_graph(conn, **kwargs)
        return [call.args[0] for call in cursor.execute.call_args_list]

    def test_label_filter_included_in_labels_query(self):
        sqls = self._run_with_mock_cursor(label_filter=["Protein"])
        labels_sql = next((s for s in sqls if "rdf_labels" in s), None)
        assert labels_sql is not None
        assert "IN" in labels_sql.upper()

    def test_graph_id_included_in_edges_query(self):
        sqls = self._run_with_mock_cursor(graph_id="http://g.example.org/")
        edges_sql = next((s for s in sqls if "rdf_edges" in s), None)
        assert edges_sql is not None
        assert "graph_id" in edges_sql

    def test_node_ids_filter_in_labels_query(self):
        sqls = self._run_with_mock_cursor(node_ids=["n1", "n2"])
        labels_sql = next((s for s in sqls if "rdf_labels" in s), None)
        assert labels_sql is not None
        assert "IN" in labels_sql.upper()

    def test_no_filters_no_extra_where_clauses(self):
        sqls = self._run_with_mock_cursor()
        # All queries should still be issued (labels, props, edges)
        assert any("rdf_labels" in s for s in sqls)
        assert any("rdf_props" in s for s in sqls)
        assert any("rdf_edges" in s for s in sqls)


# ---------------------------------------------------------------------------
# T008: Edge qualifier reification
# ---------------------------------------------------------------------------

class TestQualifierReification:

    def test_edge_with_qualifier_emits_reifier_node(self):
        import rdflib
        from rdflib import URIRef, BNode
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor

        # execute() is called 3 times: labels, props, edges
        # fetchmany() returns empty for labels and props, edge with qualifier for edges
        call_count = [0]
        def fetchmany_side_effect(n):
            call_count[0] += 1
            if call_count[0] == 3:  # 3rd fetchmany = edges first fetch
                return [("urn:s1", "urn:knows", "urn:o1", '{"confidence": 0.9}', None)]
            return []

        cursor.fetchmany.side_effect = fetchmany_side_effect

        from iris_vector_graph._engine._rdf_utils import _build_rdflib_graph
        g = _build_rdflib_graph(conn)

        # Should contain rdf:subject/predicate/object reification pattern
        all_predicates = {str(p) for s, p, o in g}
        assert any(
            "subject" in p.lower() or "reif" in p.lower() or "qualifier" in p.lower()
            for p in all_predicates
        ), f"Expected reification predicates, got: {all_predicates}"

    def test_edge_without_qualifier_no_reifier(self):
        import rdflib
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        call_count = [0]

        def fetchmany_side_effect(n):
            call_count[0] += 1
            if call_count[0] == 5:
                return [("s:1", "p:knows", "o:2", None, None)]
            return []

        cursor.fetchmany.side_effect = fetchmany_side_effect
        from iris_vector_graph._engine._rdf_utils import _build_rdflib_graph
        g = _build_rdflib_graph(conn)

        all_predicates = {str(p) for s, p, o in g}
        assert not any("reif" in p.lower() for p in all_predicates)


# ---------------------------------------------------------------------------
# T009: Namespace prefix binding
# ---------------------------------------------------------------------------

class TestNamespacePrefixBinding:

    def test_registered_prefix_appears_in_turtle(self):
        engine, conn, cursor = _make_engine()

        import rdflib
        from rdflib import URIRef, ConjunctiveGraph
        from rdflib.namespace import RDF

        with tempfile.NamedTemporaryFile(suffix=".ttl", delete=False) as f:
            path = f.name

        try:
            with patch.object(engine, "list_namespaces", return_value={"ex": "http://example.org/"}):
                with patch("iris_vector_graph._engine.rdf_export._build_rdflib_graph") as mock_build:
                    g = ConjunctiveGraph()
                    g.add((URIRef("http://example.org/A"), RDF.type, URIRef("http://example.org/B")))
                    mock_build.return_value = g
                    engine.export_rdf(path)

            content = open(path).read()
            assert "@prefix ex:" in content or "ex:" in content
        finally:
            os.unlink(path)

    def test_no_registered_prefixes_still_works(self):
        engine, conn, cursor = _make_engine()
        import rdflib
        from rdflib import URIRef
        from rdflib.namespace import RDF

        with tempfile.NamedTemporaryFile(suffix=".ttl", delete=False) as f:
            path = f.name

        try:
            with patch.object(engine, "list_namespaces", return_value={}):
                with patch("iris_vector_graph._engine.rdf_export._build_rdflib_graph") as mock_build:
                    from rdflib import ConjunctiveGraph as CG
                    mock_build.return_value = CG()
                    result = engine.export_rdf(path)
            assert result["triples"] == 0
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# T010: Graceful ImportError when rdflib absent
# ---------------------------------------------------------------------------

class TestGracefulDegradation:

    def test_export_rdf_raises_importerror_without_rdflib(self, monkeypatch):
        engine, _, _ = _make_engine()
        with patch("iris_vector_graph._engine.rdf_export._require_rdflib") as mock:
            mock.side_effect = ImportError("rdflib not installed")
            with pytest.raises(ImportError, match="rdflib"):
                engine.export_rdf("/tmp/test.ttl")

    def test_export_rdf_from_cypher_raises_importerror_without_rdflib(self, monkeypatch):
        engine, _, _ = _make_engine()
        with patch("iris_vector_graph._engine.rdf_export._require_rdflib") as mock:
            mock.side_effect = ImportError("rdflib not installed")
            with pytest.raises(ImportError, match="rdflib"):
                engine.export_rdf_from_cypher("MATCH (n) RETURN n", "/tmp/test.ttl")


# ---------------------------------------------------------------------------
# T011: export_rdf_from_cypher
# ---------------------------------------------------------------------------

class TestExportRdfFromCypher:

    def test_empty_cypher_result_produces_valid_file(self):
        engine, conn, cursor = _make_engine()
        from iris_vector_graph.result import IVGResult

        empty_result = IVGResult(columns=[], rows=[], parameters=[], error=None, bolt_column_types=[])
        engine.execute_cypher = MagicMock(return_value=empty_result)

        with tempfile.NamedTemporaryFile(suffix=".ttl", delete=False) as f:
            path = f.name
        try:
            with patch.object(engine, "list_namespaces", return_value={}):
                result = engine.export_rdf_from_cypher("MATCH (n) RETURN n", path)
            assert result["triples"] == 0
            assert os.path.exists(path)
            # File should be parseable by rdflib
            import rdflib
            g = rdflib.Graph()
            g.parse(path, format="turtle")
        finally:
            os.unlink(path)

    def test_spo_columns_produce_triple(self):
        engine, conn, cursor = _make_engine()
        from iris_vector_graph.result import IVGResult

        result_data = IVGResult(
            columns=["s", "p", "o"],
            rows=[["http://ex.org/A", "http://ex.org/knows", "http://ex.org/B"]],
            parameters=[],
            error=None,
            bolt_column_types=[],
        )
        engine.execute_cypher = MagicMock(return_value=result_data)

        with tempfile.NamedTemporaryFile(suffix=".ttl", delete=False) as f:
            path = f.name
        try:
            with patch.object(engine, "list_namespaces", return_value={}):
                result = engine.export_rdf_from_cypher("MATCH (s)-[p]->(o) RETURN s,p,o", path)
            assert result["triples"] == 1
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# T012: register_namespace / list_namespaces
# ---------------------------------------------------------------------------

class TestNamespacePersistence:

    def test_register_namespace_inserts_when_not_exists(self):
        engine, conn, cursor = _make_engine()
        cursor.rowcount = 0  # UPDATE affects 0 rows → INSERT

        engine.register_namespace("ex", "http://example.org/")

        # Should have called UPDATE then INSERT
        calls = [c.args[0] for c in cursor.execute.call_args_list]
        assert any("UPDATE" in c.upper() for c in calls)
        assert any("INSERT" in c.upper() for c in calls)
        conn.commit.assert_called()

    def test_register_namespace_updates_when_exists(self):
        engine, conn, cursor = _make_engine()
        cursor.rowcount = 1  # UPDATE affects 1 row → no INSERT needed

        engine.register_namespace("ex", "http://example.org/v2/")

        calls = [c.args[0] for c in cursor.execute.call_args_list]
        assert any("UPDATE" in c.upper() for c in calls)
        # Should NOT have called INSERT
        assert not any("INSERT" in c.upper() for c in calls)

    def test_list_namespaces_returns_dict(self):
        engine, conn, cursor = _make_engine()
        cursor.fetchall.return_value = [("ex", "http://example.org/"), ("fhir", "http://hl7.org/fhir/")]

        result = engine.list_namespaces()
        assert result == {"ex": "http://example.org/", "fhir": "http://hl7.org/fhir/"}

    def test_list_namespaces_returns_empty_on_error(self):
        engine, conn, cursor = _make_engine()
        cursor.execute.side_effect = Exception("table not found")

        result = engine.list_namespaces()
        assert result == {}
