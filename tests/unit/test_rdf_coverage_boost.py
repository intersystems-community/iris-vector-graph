"""Coverage boost tests for spec-198 RDF modules.

Targets uncovered lines in _rdf_utils.py, rdf_export.py, prov.py, shacl.py.
"""
from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _rdf_utils: _to_literal paths
# ---------------------------------------------------------------------------

class TestToLiteral:

    def test_integer_string(self):
        from iris_vector_graph._engine._rdf_utils import _to_literal
        import rdflib
        from rdflib import XSD
        lit = _to_literal("42")
        assert lit.datatype == XSD.integer

    def test_float_string(self):
        from iris_vector_graph._engine._rdf_utils import _to_literal
        from rdflib import XSD
        lit = _to_literal("3.14")
        assert lit.datatype == XSD.decimal

    def test_boolean_true(self):
        from iris_vector_graph._engine._rdf_utils import _to_literal
        from rdflib import XSD
        lit = _to_literal("true")
        assert lit.datatype == XSD.boolean
        assert bool(lit.toPython()) is True

    def test_boolean_false(self):
        from iris_vector_graph._engine._rdf_utils import _to_literal
        from rdflib import XSD
        lit = _to_literal("false")
        assert lit.datatype == XSD.boolean

    def test_plain_string(self):
        from iris_vector_graph._engine._rdf_utils import _to_literal
        import rdflib
        lit = _to_literal("hello world")
        assert isinstance(lit, rdflib.Literal)
        assert str(lit) == "hello world"

    def test_none_returns_none(self):
        from iris_vector_graph._engine._rdf_utils import _to_literal
        assert _to_literal(None) is None


# ---------------------------------------------------------------------------
# _rdf_utils: _build_rdflib_graph with real data rows
# ---------------------------------------------------------------------------

class TestBuildRdfLibGraphWithData:

    def _conn_with_rows(self, label_rows=None, prop_rows=None, edge_rows=None):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor

        call_count = [0]

        def fetchmany(n):
            call_count[0] += 1
            if call_count[0] == 1:
                return label_rows or []
            elif call_count[0] == 2:
                return prop_rows or []
            elif call_count[0] == 3:
                return edge_rows or []
            return []

        cursor.fetchmany.side_effect = fetchmany
        return conn

    def test_label_rows_produce_rdf_type_triples(self):
        from iris_vector_graph._engine._rdf_utils import _build_rdflib_graph
        from rdflib.namespace import RDF

        conn = self._conn_with_rows(
            label_rows=[("urn:ex:A", "urn:ex:Person")],
        )
        g = _build_rdflib_graph(conn)
        import rdflib
        assert (rdflib.URIRef("urn:ex:A"), RDF.type, rdflib.URIRef("urn:ex:Person")) in g

    def test_prop_rows_produce_literal_triples(self):
        from iris_vector_graph._engine._rdf_utils import _build_rdflib_graph
        import rdflib

        conn = self._conn_with_rows(
            prop_rows=[("urn:ex:A", "http://schema.org/name", "Alice")],
        )
        g = _build_rdflib_graph(conn)
        pred = rdflib.URIRef("http://schema.org/name")
        values = list(g.objects(rdflib.URIRef("urn:ex:A"), pred))
        assert len(values) == 1
        assert str(values[0]) == "Alice"

    def test_prop_key_without_uri_gets_urn_prefix(self):
        from iris_vector_graph._engine._rdf_utils import _build_rdflib_graph
        import rdflib

        conn = self._conn_with_rows(
            prop_rows=[("urn:ex:A", "name", "Alice")],
        )
        g = _build_rdflib_graph(conn)
        # "name" → urn:ivg:prop/name; subject urn:ex:A passes through unchanged
        pred = rdflib.URIRef("urn:ivg:prop/name")
        values = list(g.objects(rdflib.URIRef("urn:ex:A"), pred))
        assert len(values) == 1

    def test_edge_rows_produce_spo_triples(self):
        from iris_vector_graph._engine._rdf_utils import _build_rdflib_graph
        import rdflib

        conn = self._conn_with_rows(
            edge_rows=[("urn:ex:A", "urn:ex:knows", "urn:ex:B", None, None)],
        )
        g = _build_rdflib_graph(conn)
        assert (rdflib.URIRef("urn:ex:A"), rdflib.URIRef("urn:ex:knows"), rdflib.URIRef("urn:ex:B")) in g

    def test_edge_with_graph_id_in_named_graph(self):
        from iris_vector_graph._engine._rdf_utils import _build_rdflib_graph
        import rdflib

        conn = self._conn_with_rows(
            edge_rows=[("urn:ex:A", "urn:ex:knows", "urn:ex:B", None, "urn:graph:g1")],
        )
        g = _build_rdflib_graph(conn)
        # ConjunctiveGraph — check the named graph context
        ctx = g.get_context(rdflib.URIRef("urn:graph:g1"))
        assert len(ctx) == 1

    def test_numeric_prop_value(self):
        from iris_vector_graph._engine._rdf_utils import _build_rdflib_graph
        from rdflib import XSD
        import rdflib

        conn = self._conn_with_rows(
            prop_rows=[("urn:ex:A", "http://ex.org/score", "0.95")],
        )
        g = _build_rdflib_graph(conn)
        values = list(g.objects(rdflib.URIRef("urn:ex:A"), rdflib.URIRef("http://ex.org/score")))
        assert len(values) == 1
        assert values[0].datatype in (XSD.decimal, XSD.integer)


# ---------------------------------------------------------------------------
# rdf_export.py: _project_row_as_rdf alternate paths
# ---------------------------------------------------------------------------

class TestProjectRowAsRdf:

    def test_three_col_pattern_produces_triple(self):
        import rdflib
        from iris_vector_graph._engine.rdf_export import _project_row_as_rdf

        g = rdflib.Graph()
        row_data = {
            "subject": "http://ex.org/A",
            "rel": "http://ex.org/knows",
            "object": "http://ex.org/B",
        }
        _project_row_as_rdf(g, row_data, ["subject", "rel", "object"], "urn:ivg:")
        assert len(g) == 1

    def test_single_uri_column_produces_type_triple(self):
        import rdflib
        from rdflib.namespace import RDF
        from iris_vector_graph._engine.rdf_export import _project_row_as_rdf

        g = rdflib.Graph()
        row_data = {"node": "http://ex.org/A"}
        _project_row_as_rdf(g, row_data, ["node"], "urn:ivg:")
        type_triples = list(g.triples((rdflib.URIRef("http://ex.org/A"), RDF.type, None)))
        assert len(type_triples) == 1

    def test_bare_string_single_col_no_triple(self):
        import rdflib
        from iris_vector_graph._engine.rdf_export import _project_row_as_rdf

        g = rdflib.Graph()
        row_data = {"name": "Alice"}  # not a URI → no triple
        _project_row_as_rdf(g, row_data, ["name"], "urn:ivg:")
        assert len(g) == 0


# ---------------------------------------------------------------------------
# prov.py: prov_export_from_cypher
# ---------------------------------------------------------------------------

class TestProvExportFromCypher:

    def test_prov_export_from_cypher_uses_cypher_node_ids(self):
        from iris_vector_graph.engine import IRISGraphEngine
        from iris_vector_graph.result import IVGResult

        conn = MagicMock()
        conn.cursor.return_value = MagicMock()
        engine = IRISGraphEngine.__new__(IRISGraphEngine)
        engine.conn = conn
        engine.embedding_dimension = 4
        engine._store = MagicMock()

        captured_nodes = []

        def mock_get_for_nodes(node_ids):
            captured_nodes.extend(node_ids)
            return []

        engine._get_temporal_edges_for_nodes = mock_get_for_nodes
        engine.execute_cypher = MagicMock(return_value=IVGResult(
            columns=["id"], rows=[["urn:ex:Patient/001"], ["urn:ex:Patient/002"]],
            parameters=[], error=None, bolt_column_types=[],
        ))

        with tempfile.NamedTemporaryFile(suffix=".ttl", delete=False) as f:
            path = f.name
        try:
            result = engine.prov_export_from_cypher("MATCH (n) RETURN n.id", path)
            assert result["path"] == path
            assert "urn:ex:Patient/001" in captured_nodes
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_empty_cypher_result_produces_valid_prov_file(self):
        from iris_vector_graph.engine import IRISGraphEngine
        from iris_vector_graph.result import IVGResult

        conn = MagicMock()
        conn.cursor.return_value = MagicMock()
        engine = IRISGraphEngine.__new__(IRISGraphEngine)
        engine.conn = conn
        engine.embedding_dimension = 4
        engine._store = MagicMock()
        engine._get_temporal_edges_for_nodes = MagicMock(return_value=[])
        engine.execute_cypher = MagicMock(return_value=IVGResult(
            columns=[], rows=[], parameters=[], error=None, bolt_column_types=[],
        ))

        with tempfile.NamedTemporaryFile(suffix=".ttl", delete=False) as f:
            path = f.name
        try:
            result = engine.prov_export_from_cypher("MATCH (n) RETURN n", path)
            assert result["activities"] == 0
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# shacl.py: _load_shapes_graph invalid type
# ---------------------------------------------------------------------------

class TestLoadShapesGraphErrors:

    def test_invalid_type_raises_value_error(self):
        from iris_vector_graph._engine.shacl import _load_shapes_graph
        with pytest.raises(ValueError, match="shapes_source"):
            _load_shapes_graph(42)

    def test_nonexistent_file_path_parses_as_string(self):
        from iris_vector_graph._engine.shacl import _load_shapes_graph
        # A path that doesn't exist is treated as Turtle string, which will fail to parse
        with pytest.raises(Exception):
            _load_shapes_graph("/nonexistent/path/shapes.ttl")


# ---------------------------------------------------------------------------
# prov.py: _get_temporal_edges_window error path
# ---------------------------------------------------------------------------

class TestProvWindowErrorPath:

    def test_iris_error_returns_empty_list(self):
        from iris_vector_graph.engine import IRISGraphEngine

        conn = MagicMock()
        conn.cursor.return_value = MagicMock()
        engine = IRISGraphEngine.__new__(IRISGraphEngine)
        engine.conn = conn
        engine.embedding_dimension = 4
        engine._store = MagicMock()
        engine._iris_obj = MagicMock(side_effect=Exception("IRIS not available"))

        result = engine._get_temporal_edges_window(None, None)
        assert result == []
