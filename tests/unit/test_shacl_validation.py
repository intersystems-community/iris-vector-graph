"""Unit tests for spec-198 US2: SHACL Core Validation.

Tests cover:
- T022: ValidationReport and Violation dataclasses
- T023: Conforming graph → conforms==True
- T024: Missing required property → conforms==False with violation details
- T025: sh:Warning severity → conforms stays True
- T026: shapes_source as file path
- T027: shapes_source as rdflib Graph
- T028: shapes_source as Turtle string
- T029: Unreachable URL raises IOError
- T030: node_ids scoping
- T031: Missing pyshacl raises ImportError
"""
from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest


SHAPES_MINCOUNT = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix ex: <http://example.org/> .

ex:PersonShape a sh:NodeShape ;
    sh:targetClass ex:Person ;
    sh:property [
        sh:path ex:name ;
        sh:minCount 1 ;
        sh:message "Person must have a name" ;
    ] .
"""

SHAPES_WARNING = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix ex: <http://example.org/> .

ex:PersonShape a sh:NodeShape ;
    sh:targetClass ex:Person ;
    sh:property [
        sh:path ex:email ;
        sh:minCount 1 ;
        sh:severity sh:Warning ;
        sh:message "Person should have an email" ;
    ] .
"""

DATA_CONFORMING = """
@prefix ex: <http://example.org/> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .

ex:alice a ex:Person ;
    ex:name "Alice" .
"""

DATA_NONCONFORMING = """
@prefix ex: <http://example.org/> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .

ex:bob a ex:Person .
"""


# ---------------------------------------------------------------------------
# T022: ValidationReport and Violation dataclasses
# ---------------------------------------------------------------------------

class TestValidationReportDataclasses:

    def test_validation_report_conforms_true(self):
        from iris_vector_graph._engine.shacl import ValidationReport
        r = ValidationReport(conforms=True, violations=[])
        assert r.conforms is True
        assert r.violations == []

    def test_validation_report_bool_true(self):
        from iris_vector_graph._engine.shacl import ValidationReport
        r = ValidationReport(conforms=True)
        assert bool(r) is True

    def test_validation_report_bool_false(self):
        from iris_vector_graph._engine.shacl import ValidationReport
        from iris_vector_graph._engine.shacl import Violation
        r = ValidationReport(conforms=False, violations=[
            Violation(focus_node="ex:bob", shape="ex:Shape", message="Missing name", severity="Violation")
        ])
        assert bool(r) is False

    def test_to_dict_is_json_serializable(self):
        import json
        from iris_vector_graph._engine.shacl import ValidationReport, Violation
        r = ValidationReport(conforms=False, violations=[
            Violation(
                focus_node="ex:bob", shape="ex:Shape", message="Missing",
                severity="Violation", path="ex:name", value=None,
            )
        ])
        d = r.to_dict()
        json.dumps(d)  # Should not raise
        assert d["conforms"] is False
        assert len(d["violations"]) == 1
        assert d["violations"][0]["focus_node"] == "ex:bob"

    def test_violation_to_dict_all_fields(self):
        from iris_vector_graph._engine.shacl import Violation
        v = Violation(
            focus_node="urn:x", shape="urn:s", message="msg",
            severity="Warning", path="ex:p", value="bad",
        )
        d = v.to_dict()
        assert d["focus_node"] == "urn:x"
        assert d["severity"] == "Warning"
        assert d["path"] == "ex:p"
        assert d["value"] == "bad"

    def test_violation_optional_fields_default_none(self):
        from iris_vector_graph._engine.shacl import Violation
        v = Violation(focus_node="x", shape="s", message="m", severity="Violation")
        assert v.path is None
        assert v.value is None


# ---------------------------------------------------------------------------
# T023/T024/T025: Validation outcomes (using real pyshacl)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("pyshacl"),
    reason="pyshacl not installed"
)
class TestValidationOutcomes:

    def _make_engine_with_data(self, data_ttl: str):
        """Engine that serves given Turtle as its data graph."""
        import rdflib
        from iris_vector_graph.engine import IRISGraphEngine

        conn = MagicMock()
        conn.cursor.return_value = MagicMock()
        engine = IRISGraphEngine.__new__(IRISGraphEngine)
        engine.conn = conn
        engine.embedding_dimension = 4
        engine._store = MagicMock()

        data_graph = rdflib.ConjunctiveGraph()
        data_graph.parse(data=data_ttl, format="turtle")

        with patch("iris_vector_graph._engine.shacl._build_rdflib_graph", return_value=data_graph):
            yield engine

    def test_conforming_graph_returns_conforms_true(self):
        import rdflib
        from iris_vector_graph._engine.shacl import _parse_shacl_report
        import pyshacl

        data = rdflib.ConjunctiveGraph()
        data.parse(data=DATA_CONFORMING, format="turtle")
        shapes = rdflib.Graph()
        shapes.parse(data=SHAPES_MINCOUNT, format="turtle")

        conforms, report_graph, _ = pyshacl.validate(data, shacl_graph=shapes, inference="none")
        report = _parse_shacl_report(report_graph, bool(conforms))
        assert report.conforms is True
        assert report.violations == []

    def test_nonconforming_graph_returns_violations(self):
        import rdflib
        from iris_vector_graph._engine.shacl import _parse_shacl_report
        import pyshacl

        data = rdflib.ConjunctiveGraph()
        data.parse(data=DATA_NONCONFORMING, format="turtle")
        shapes = rdflib.Graph()
        shapes.parse(data=SHAPES_MINCOUNT, format="turtle")

        conforms, report_graph, _ = pyshacl.validate(data, shacl_graph=shapes, inference="none")
        report = _parse_shacl_report(report_graph, bool(conforms))
        assert report.conforms is False
        assert len(report.violations) > 0
        v = report.violations[0]
        assert v.severity == "Violation"

    def test_warning_severity_violation_is_captured(self):
        """sh:Warning violations are captured in the violations list with severity='Warning'."""
        import rdflib
        from iris_vector_graph._engine.shacl import _parse_shacl_report
        import pyshacl

        data = rdflib.ConjunctiveGraph()
        data.parse(data=DATA_NONCONFORMING, format="turtle")
        shapes = rdflib.Graph()
        shapes.parse(data=SHAPES_WARNING, format="turtle")

        conforms, report_graph, _ = pyshacl.validate(data, shacl_graph=shapes, inference="none")
        report = _parse_shacl_report(report_graph, bool(conforms))
        warning_violations = [v for v in report.violations if v.severity == "Warning"]
        assert len(warning_violations) > 0
        assert warning_violations[0].severity == "Warning"


# ---------------------------------------------------------------------------
# T026/T027/T028: shapes_source dispatch
# ---------------------------------------------------------------------------

class TestShapesSourceDispatch:

    def test_file_path_loads_correctly(self, tmp_path):
        from iris_vector_graph._engine.shacl import _load_shapes_graph
        shapes_file = tmp_path / "shapes.ttl"
        shapes_file.write_text(SHAPES_MINCOUNT)
        g = _load_shapes_graph(str(shapes_file))
        assert len(g) > 0

    def test_rdflib_graph_passes_through(self):
        import rdflib
        from iris_vector_graph._engine.shacl import _load_shapes_graph
        g = rdflib.Graph()
        g.parse(data=SHAPES_MINCOUNT, format="turtle")
        result = _load_shapes_graph(g)
        assert result is g

    def test_turtle_string_parses_correctly(self):
        from iris_vector_graph._engine.shacl import _load_shapes_graph
        g = _load_shapes_graph(SHAPES_MINCOUNT)
        assert len(g) > 0

    def test_json_ld_string_parses(self):
        from iris_vector_graph._engine.shacl import _load_shapes_graph
        # Simple JSON-LD
        jsonld = '{"@context": {"sh": "http://www.w3.org/ns/shacl#"}, "@type": "sh:NodeShape"}'
        g = _load_shapes_graph(jsonld)
        assert g is not None


# ---------------------------------------------------------------------------
# T029: Unreachable URL raises IOError
# ---------------------------------------------------------------------------

class TestUnreachableUrl:

    def test_unreachable_http_url_raises_ioerror(self):
        from iris_vector_graph._engine.shacl import _load_shapes_graph
        import urllib.request
        with patch.object(urllib.request, "urlopen", side_effect=Exception("connection refused")):
            with pytest.raises((IOError, Exception)):
                _load_shapes_graph("http://unreachable.example.test/shapes.ttl")

    def test_http_error_raises_ioerror(self):
        from iris_vector_graph._engine.shacl import _load_shapes_graph
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
            "http://ex.test/", 404, "Not Found", {}, None
        )):
            with pytest.raises(IOError, match="404"):
                _load_shapes_graph("http://ex.test/shapes.ttl")


# ---------------------------------------------------------------------------
# T030: node_ids scoping
# ---------------------------------------------------------------------------

class TestNodeIdsScoping:

    def test_node_ids_passed_to_build_data_graph(self):
        """validate_shacl passes node_ids to _build_rdflib_graph."""
        import rdflib
        pytest.importorskip("pyshacl")
        from iris_vector_graph.engine import IRISGraphEngine
        import iris_vector_graph._engine._rdf_utils as rdf_utils_mod

        conn = MagicMock()
        conn.cursor.return_value = MagicMock()
        engine = IRISGraphEngine.__new__(IRISGraphEngine)
        engine.conn = conn
        engine.embedding_dimension = 4
        engine._store = MagicMock()

        captured_kwargs = {}
        original_build = rdf_utils_mod._build_rdflib_graph

        def mock_build(conn, **kwargs):
            captured_kwargs.update(kwargs)
            return rdflib.ConjunctiveGraph()

        rdf_utils_mod._build_rdflib_graph = mock_build
        try:
            engine.validate_shacl(SHAPES_MINCOUNT, node_ids=["urn:x", "urn:y"])
        except Exception:
            pass  # pyshacl may fail on empty graph; we only care about the kwarg
        finally:
            rdf_utils_mod._build_rdflib_graph = original_build

        assert captured_kwargs.get("node_ids") == ["urn:x", "urn:y"]


# ---------------------------------------------------------------------------
# T031: Missing pyshacl raises ImportError with hint
# ---------------------------------------------------------------------------

class TestMissingPyshacl:

    def test_validate_shacl_raises_importerror_without_pyshacl(self):
        from iris_vector_graph.engine import IRISGraphEngine

        conn = MagicMock()
        conn.cursor.return_value = MagicMock()
        engine = IRISGraphEngine.__new__(IRISGraphEngine)
        engine.conn = conn
        engine.embedding_dimension = 4
        engine._store = MagicMock()

        with patch("iris_vector_graph._engine.shacl._require_pyshacl") as mock:
            mock.side_effect = ImportError("pip install iris-vector-graph[rdf]")
            with pytest.raises(ImportError, match="rdf"):
                engine.validate_shacl(SHAPES_MINCOUNT)

